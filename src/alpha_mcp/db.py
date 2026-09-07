"""SQLite access: replicate the remote database with sqlite3_rsync, then answer read-only lookups.

Works the same on Linux, macOS and Windows: the local path is absolute (so sqlite3_rsync treats it
as local, not HOST:PATH), the read-only connection uses a file: URI built with Path.as_uri(), and
subprocess output is decoded as UTF-8 regardless of the console code page.

Anticipated failures raise ToolError so the *message* reaches the model; any other exception is
reported to the client only as "Error executing tool <name>" (details stay in the server log)."""

import logging
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import config

log = logging.getLogger(__name__)

_STAMP = config.DB_LOCAL.with_name(config.DB_LOCAL.name + ".synced")  # last successful sync, epoch seconds
PENDING_SQL = config.DB_LOCAL.with_name(config.DB_LOCAL.name + ".pending.sql")  # local edits not yet on the origin
AUDIT_LOG = config.DB_LOCAL.with_name(config.DB_LOCAL.name + ".audit.jsonl")
_SUMMARY = re.compile(r"sent ([\d,]+) bytes, received ([\d,]+) bytes|speedup is ([\d.]+)|page updates: (\d+)")


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _config_hint() -> str:
    files = ", ".join(str(p) for p in config.env_file_candidates())
    return f"set it in the environment or in a .env file (searched: {files})"


def pending_count() -> int:
    """Number of local UPDATE statements not yet applied to the origin (see funds.py)."""
    if not PENDING_SQL.is_file():
        return 0
    return sum(1 for line in PENDING_SQL.read_text(encoding="utf-8").splitlines() if line.startswith("UPDATE "))


def discard_pending() -> Path | None:
    """Set the pending file aside (never deleted) so a sync from the origin may proceed."""
    if not PENDING_SQL.is_file():
        return None
    archived = PENDING_SQL.with_name(PENDING_SQL.name + time.strftime(".%Y%m%d-%H%M%S.discarded"))
    PENDING_SQL.rename(archived)
    return archived


def sync(discard_local: bool = False) -> dict[str, Any]:
    """Make the local replica a page-level copy of ORIGIN with sqlite3_rsync over ssh.

    sqlite3_rsync reads the origin inside a read transaction (safe on a live DB), sends only the
    pages whose hashes differ, and applies them to the replica in one transaction, so a query
    running here sees either the old or the new state, never a half-copied file.

    Refuses while local edits are pending: the sync would silently overwrite them.
    """
    if not config.DB_ORIGIN:
        raise ToolError("ALPHA_MCP_DB_ORIGIN is not configured; " + _config_hint())
    if (n := pending_count()) and not discard_local:
        raise ToolError(
            f"{n} local edit(s) are pending in {PENDING_SQL} and a sync from the origin would overwrite them. "
            "Apply them to the origin first (`alpha-mcp pending` prints the SQL), then run `alpha-mcp pending --clear` "
            "or call sync_db with discard_local_changes=true to set them aside."
        )
    archived = discard_pending() if discard_local else None
    config.DB_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which(config.SQLITE3_RSYNC)
    if exe is None:
        raise ToolError(
            f"{config.SQLITE3_RSYNC!r} not found on PATH; install the SQLite >= 3.50 tools "
            "or set ALPHA_MCP_SQLITE3_RSYNC to the binary's full path"
        )
    cmd = [exe, config.DB_ORIGIN, str(config.DB_LOCAL), config.SYNC_VERBOSITY]
    if config.REMOTE_EXE:
        cmd += ["--exe", config.REMOTE_EXE]
    if config.SSH:
        cmd += ["--ssh", config.SSH]
    cmd += config.SYNC_ARGS
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.SYNC_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"sqlite3_rsync timed out after {config.SYNC_TIMEOUT}s") from None
    output = (proc.stdout + "\n" + proc.stderr).strip()
    tail = "\n".join(output.splitlines()[-8:])
    if proc.returncode != 0 or "not synced" in output:
        raise ToolError(f"sqlite3_rsync failed (exit {proc.returncode}):\n{tail}")

    _ensure_wal()
    _STAMP.write_text(str(time.time()))
    result: dict[str, Any] = {
        "origin": config.DB_ORIGIN,
        "local_path": str(config.DB_LOCAL),
        "discarded_local_edits": str(archived) if archived else None,
        "bytes": config.DB_LOCAL.stat().st_size,
        "seconds": round(time.monotonic() - t0, 2),
        "log_tail": tail,
    }
    for m in _SUMMARY.finditer(output):
        if m.group(1):
            result["sent_bytes"], result["received_bytes"] = _int(m.group(1)), _int(m.group(2))
        elif m.group(3):
            result["speedup"] = float(m.group(3))
        elif m.group(4):
            result["pages_updated"] = int(m.group(4))
    return result


def _ensure_wal() -> None:
    """Put the replica in WAL mode once. In rollback-journal mode an open reader makes the next
    sync fail with 'database is locked'; in WAL mode readers and the sync writer coexist."""
    with sqlite3.connect(config.DB_LOCAL) as conn:
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            conn.execute("PRAGMA journal_mode=wal")


def age_seconds() -> float | None:
    if not config.DB_LOCAL.exists():
        return None
    try:
        return time.time() - float(_STAMP.read_text())
    except (OSError, ValueError):
        return time.time() - config.DB_LOCAL.stat().st_mtime


def ensure_fresh() -> None:
    """Sync if the local copy is missing or stale. Never blocks a query on a *failed* sync
    if a usable copy exists (offline laptops still get answers, just older ones)."""
    age = age_seconds()
    if age is None:
        sync()
    elif pending_count():
        log.debug("auto-sync skipped: local edits pending")
    elif config.DB_MAX_AGE and age > config.DB_MAX_AGE:
        try:
            sync()
        except Exception as e:  # noqa: BLE001
            log.warning("auto-sync failed, using %.0fh-old copy: %s", age / 3600, e)


def connect_rw() -> sqlite3.Connection:
    """Read-write connection to the replica for the review tools. Writes never reach the origin."""
    conn = sqlite3.connect(config.DB_LOCAL, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def connect() -> sqlite3.Connection:
    # A new read-only connection per call: cheap, and it always sees what sqlite3_rsync last committed.
    conn = sqlite3.connect(config.DB_LOCAL.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    names = [r["name"] for r in rows]
    return [n for n in names if not config.TABLE_ALLOWLIST or n in config.TABLE_ALLOWLIST]


def _columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    # `table` has already been validated against sqlite_master, so quoting is belt-and-braces.
    return conn.execute(f'PRAGMA table_info("{table}")').fetchall()


def list_tables() -> list[dict[str, Any]]:
    ensure_fresh()
    with connect() as conn:
        out = []
        for t in _tables(conn):
            cols = _columns(conn, t)
            out.append(
                {
                    "table": t,
                    "primary_key": [c["name"] for c in cols if c["pk"]],
                    "columns": [c["name"] for c in cols],
                    "rows": conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0],
                }
            )
        return out


def get_row(table: str, key: str, key_column: str | None = None) -> dict[str, Any] | None:
    """Return one row from `table` where `key_column` (default: the primary key) equals `key`."""
    ensure_fresh()
    with connect() as conn:
        if table not in _tables(conn):
            raise ToolError(f"unknown table {table!r}; call list_tables")
        cols = _columns(conn, table)
        if key_column is None:
            pk = [c["name"] for c in cols if c["pk"]]
            if len(pk) != 1:
                raise ToolError(f"{table!r} has no single-column primary key; pass key_column")
            key_column = pk[0]
        elif key_column not in {c["name"] for c in cols}:
            raise ToolError(f"unknown column {key_column!r} in {table!r}")
        # Identifiers are validated + quoted; the value is a bound parameter. No f-string SQL on user input.
        row = conn.execute(f'SELECT * FROM "{table}" WHERE "{key_column}" = ? LIMIT 1', (key,)).fetchone()
        return dict(row) if row else None
