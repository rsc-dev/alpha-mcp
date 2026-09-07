"""Fund data review: constrained writes to the LOCAL replica, plus fund documents ("Materials").

Nothing here touches the origin database. Every write goes to the local replica and is also
appended as an UPDATE statement to <replica>.pending.sql (to apply on the origin later) and, with
before/after values, to <replica>.audit.jsonl. While pending edits exist, syncing from the origin is
refused (see db.sync), because it would overwrite them.

The column allowlist, the date formats, the identifier checksums and the ChatGPTResponse schema are
enforced here so the model cannot get them wrong.
"""

import hashlib
import ipaddress
import json
import re
import socket
import time
from collections import Counter
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import unquote, urlparse

import httpx2
from mcp.server.mcpserver.exceptions import ToolError

from . import config, db

FUND_TABLE = "FundsDataSets"
PROVIDER_TABLE = "PromptProviderFunds"

# Exactly the "fields to check and update" list of the review prompt. Everything else is rejected.
UPDATABLE = [
    "CUSIP", "CINS", "ISIN", "SEDOL", "LEI", "RIC", "BBGID", "FIGI", "MIC",
    "FundUrl", "FundFactSheetUrl", "FundProspectusUrl", "IndexDocumentationUrl",
    "FundName", "InceptionDate", "FundClosedDate", "FundDomicile", "AssetClass", "FundType",
    "FundRebalancingFrequency", "IndexName", "IndexRebalancingFrequency", "IndexReconstructionFrequency",
    "ChatGPTResponse", "ChatGPTLastUpdate", "Processed", "Notes",
]  # fmt: skip
URL_COLUMNS = {"FundUrl", "FundFactSheetUrl", "FundProspectusUrl", "IndexDocumentationUrl"}
DATE_COLUMNS = {"InceptionDate", "FundClosedDate"}  # YYYY.MM.DD
VOCAB_COLUMNS = {
    "AssetClass", "FundType", "FundDomicile",
    "FundRebalancingFrequency", "IndexRebalancingFrequency", "IndexReconstructionFrequency",
}  # fmt: skip
IDENTIFIER_COLUMNS = {"CUSIP", "CINS", "ISIN", "SEDOL", "LEI", "RIC", "BBGID", "FIGI", "MIC"}

DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
DATETIME_DOTTED_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}$")
ARCHIVE_HOSTS = ("web.archive.org", "archive.org", "archive.ph", "archive.today")


# --- identifier checksums ---------------------------------------------------------------------------


def _alnum_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if ch.isalpha():
        return ord(ch.upper()) - 55  # A=10 ... Z=35
    return {"*": 36, "@": 37, "#": 38}[ch]


def _double_add_double(chars: str) -> int:
    """CUSIP/CINS/FIGI check digit: double every second value, sum the digits."""
    total = 0
    for i, ch in enumerate(chars):
        v = _alnum_value(ch)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for d in reversed(digits):
        v = int(d)
        if double:
            v *= 2
            v = v // 10 + v % 10
        total += v
        double = not double
    return total % 10 == 0


def check_isin(v: str) -> str | None:
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", v):
        return "ISIN must be 2 letters + 9 alphanumerics + 1 check digit"
    return None if _luhn_ok("".join(str(_alnum_value(c)) for c in v)) else "ISIN check digit is wrong"


def check_cusip(v: str) -> str | None:
    if not re.fullmatch(r"[A-Z0-9*@#]{8}\d", v):
        return "CUSIP/CINS must be 8 characters + 1 check digit"
    return None if _double_add_double(v[:8]) == int(v[8]) else "CUSIP/CINS check digit is wrong"


def check_sedol(v: str) -> str | None:
    if not re.fullmatch(r"[B-DF-HJ-NP-TV-Z0-9]{6}\d", v):
        return "SEDOL must be 6 characters (no vowels) + 1 check digit"
    total = sum(w * _alnum_value(c) for w, c in zip((1, 3, 1, 7, 3, 9), v[:6], strict=True))
    return None if (10 - total % 10) % 10 == int(v[6]) else "SEDOL check digit is wrong"


def check_lei(v: str) -> str | None:
    if not re.fullmatch(r"[A-Z0-9]{18}\d{2}", v):
        return "LEI must be 20 alphanumerics ending in 2 check digits"
    return None if int("".join(str(_alnum_value(c)) for c in v)) % 97 == 1 else "LEI check digits are wrong"


def check_figi(v: str) -> str | None:
    if not re.fullmatch(r"[B-DF-HJ-NP-TV-Z0-9]{2}G[B-DF-HJ-NP-TV-Z0-9]{8}\d", v):
        return "FIGI must be 12 characters: 2 consonants/digits, 'G', 8 consonants/digits, 1 check digit"
    return None if _double_add_double(v[:11]) == int(v[11]) else "FIGI check digit is wrong"


def check_mic(v: str) -> str | None:
    return None if re.fullmatch(r"[A-Z0-9]{4}", v) else "MIC must be 4 uppercase alphanumerics"


CHECKS = {
    "ISIN": check_isin,
    "CUSIP": check_cusip,
    "CINS": check_cusip,
    "SEDOL": check_sedol,
    "LEI": check_lei,
    "FIGI": check_figi,
    "BBGID": check_figi,
    "MIC": check_mic,
}


# --- value validation -------------------------------------------------------------------------------


def _url_ok(v: str) -> str | None:
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        return "must be an http(s) URL"
    if u.netloc.lower().endswith(ARCHIVE_HOSTS):
        return "Internet Archive links are not allowed in the database; store the original URL"
    return None


def vocabulary(conn, column: str) -> list[str]:
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{FUND_TABLE}" WHERE "{column}" IS NOT NULL AND "{column}" != \'\''
    )
    return sorted(r[0] for r in rows)


def chatgpt_schema(conn) -> dict[str, Any]:
    """The key set your existing ChatGPTResponse values use (majority key set) plus one example."""
    key_sets: Counter[tuple[str, ...]] = Counter()
    example: dict[str, Any] | None = None
    for (raw,) in conn.execute(f'SELECT "ChatGPTResponse" FROM "{FUND_TABLE}" WHERE "ChatGPTResponse" LIKE \'{{%\''):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            key_sets[tuple(data)] += 1
            example = example or data
    if not key_sets:
        raise ToolError("no existing ChatGPTResponse JSON to derive a schema from")
    return {"keys": list(key_sets.most_common(1)[0][0]), "example": example}


def _validate(conn, column: str, value: Any, warnings: list[str]) -> Any:
    if column not in UPDATABLE:
        raise ToolError(f"{column!r} is not updatable by this process; allowed: {', '.join(UPDATABLE)}")
    if value is None:
        return None
    if column == "Processed":
        if str(value) not in ("0", "1"):
            raise ToolError("Processed must be 0 or 1")
        return int(value)
    if column == "ChatGPTResponse":
        data = value if isinstance(value, dict) else json.loads(value)
        expected = chatgpt_schema(conn)["keys"]
        if list(data) != expected:
            raise ToolError(f"ChatGPTResponse must keep the existing schema; keys in this order: {expected}")
        return json.dumps(data, ensure_ascii=False)
    value = str(value).strip()
    if column in IDENTIFIER_COLUMNS:
        value = value.upper()
        if column in CHECKS and (err := CHECKS[column](value)):
            raise ToolError(f"{column} {value!r}: {err}")
    elif column in DATE_COLUMNS:
        if not DATE_RE.fullmatch(value):
            raise ToolError(f"{column} must be YYYY.MM.DD, got {value!r}")
        datetime.strptime(value, "%Y.%m.%d")
    elif column == "ChatGPTLastUpdate":
        if not DATETIME_DOTTED_RE.fullmatch(value):
            raise ToolError(f"ChatGPTLastUpdate must be 'YYYY.MM.DD HH:MM:SS', got {value!r}")
    elif column in URL_COLUMNS:
        if err := _url_ok(value):
            raise ToolError(f"{column} {value!r}: {err}")
    elif column in VOCAB_COLUMNS:
        known = vocabulary(conn, column)
        if value not in known:
            warnings.append(f"{column}={value!r} is new to this column; existing values include {known[:12]}")
    return value


# --- writes to the local replica ----------------------------------------------------------------------


def _quote(conn, value: Any) -> str:
    return conn.execute("SELECT quote(?)", (value,)).fetchone()[0]


def _record(
    conn, table: str, key: str, key_value: Any, changes: dict[str, tuple[Any, Any]], sources: list[str]
) -> None:
    sets = ", ".join(f'"{c}" = {_quote(conn, new)}' for c, (_, new) in changes.items())
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.PENDING_SQL.open("a", encoding="utf-8") as f:
        f.write(f"-- {stamp} {table} {key}={key_value}\n")
        f.write(f'UPDATE "{table}" SET {sets} WHERE "{key}" = {_quote(conn, key_value)};\n')
    with db.AUDIT_LOG.open("a", encoding="utf-8") as f:
        entry = {
            "at": stamp,
            "table": table,
            key: key_value,
            "changes": {c: {"before": old, "after": new} for c, (old, new) in changes.items()},
            "sources": sources,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_fund(
    fund_id: int, fields: dict[str, Any], sources: list[str] | None = None, append_notes: bool = True
) -> dict[str, Any]:
    if not fields:
        raise ToolError("no fields given")
    warnings: list[str] = []
    with db.connect_rw() as conn:
        row = conn.execute(f'SELECT * FROM "{FUND_TABLE}" WHERE "ID" = ?', (fund_id,)).fetchone()
        if row is None:
            raise ToolError(f"{FUND_TABLE} has no row with ID={fund_id}")
        row = dict(row)
        changes: dict[str, tuple[Any, Any]] = {}
        for column, raw in fields.items():
            value = _validate(conn, column, raw, warnings)
            if column == "Notes" and append_notes and value and row.get("Notes") and value not in row["Notes"]:
                value = f"{row['Notes'].rstrip()}\n{value}"
            if value != row.get(column) and not (value in ("", None) and row.get(column) in ("", None)):
                changes[column] = (row.get(column), value)
        if "ChatGPTResponse" in changes and "ChatGPTLastUpdate" not in fields:
            changes["ChatGPTLastUpdate"] = (row.get("ChatGPTLastUpdate"), datetime.now().strftime("%Y.%m.%d %H:%M:%S"))
        if changes:
            sets = ", ".join(f'"{c}" = ?' for c in changes)
            conn.execute(
                f'UPDATE "{FUND_TABLE}" SET {sets} WHERE "ID" = ?', [*(n for _, n in changes.values()), fund_id]
            )
            _record(conn, FUND_TABLE, "ID", fund_id, changes, sources or [])
    return {
        "id": fund_id,
        "fund": row.get("FundTickerUnique") or row.get("FundTicker"),
        "updated": {c: {"before": old, "after": new} for c, (old, new) in changes.items()},
        "unchanged": [c for c in fields if c not in changes],
        "warnings": warnings,
        "pending_edits": db.pending_count(),
        "note": "written to the local replica only; `alpha-mcp pending` prints the SQL to apply on the origin",
    }


def finish_provider_review(provider: str, notes: str | None = None) -> dict[str, Any]:
    """Stamp PromptProviderFunds.LastReviewDate (YYYY-MM-DD HH:MM:SS). ReviewFinished is never touched."""
    with db.connect_rw() as conn:
        rows = conn.execute(f'SELECT * FROM "{PROVIDER_TABLE}" WHERE "Name" = ?', (provider,)).fetchall()
        if len(rows) != 1:
            similar = [
                r[0]
                for r in conn.execute(f'SELECT "Name" FROM "{PROVIDER_TABLE}" WHERE "Name" LIKE ?', (f"%{provider}%",))
            ]
            raise ToolError(f"{len(rows)} providers named {provider!r}; similar names: {similar[:10]}")
        row = dict(rows[0])
        changes: dict[str, tuple[Any, Any]] = {
            "LastReviewDate": (row["LastReviewDate"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        }
        if notes and notes not in (row.get("Notes") or ""):
            changes["Notes"] = (row.get("Notes"), f"{row['Notes'].rstrip()}\n{notes}" if row.get("Notes") else notes)
        sets = ", ".join(f'"{c}" = ?' for c in changes)
        conn.execute(
            f'UPDATE "{PROVIDER_TABLE}" SET {sets} WHERE "ID" = ?', [*(n for _, n in changes.values()), row["ID"]]
        )
        _record(conn, PROVIDER_TABLE, "ID", row["ID"], changes, [])
    return {
        "provider": provider,
        "id": row["ID"],
        "updated": {c: n for c, (_, n) in changes.items()},
        "pending_edits": db.pending_count(),
    }


# --- documents ("Materials") -----------------------------------------------------------------------


def materials_dir(row: dict[str, Any]) -> Path:
    """Local mirror of the fund's Materials directory, derived from its DataSet path."""
    dataset = (row.get("DataSet") or "").strip()
    if dataset:
        parts = list(PureWindowsPath(dataset).parts)
        prefix = list(PureWindowsPath(config.DATASETS_PREFIX).parts)
        if [p.lower() for p in parts[: len(prefix)]] == [p.lower() for p in prefix]:
            parts = parts[len(prefix) :]
        if parts and parts[-1].lower() == "dataset":
            parts = parts[:-1]
        rel = Path(*parts) if parts else Path(row.get("FundProvider") or "unknown")
    else:
        rel = Path(row.get("FundProvider") or "unknown") / (
            row.get("FundTickerUnique") or row.get("FundTicker") or "unknown"
        )
    return config.MATERIALS_ROOT / rel / "Materials"


def _fund_row(fund_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(f'SELECT * FROM "{FUND_TABLE}" WHERE "ID" = ?', (fund_id,)).fetchone()
    if row is None:
        raise ToolError(f"{FUND_TABLE} has no row with ID={fund_id}")
    return dict(row)


def list_materials(fund_id: int) -> dict[str, Any]:
    row = _fund_row(fund_id)
    folder = materials_dir(row)
    files = []
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                st = p.stat()
                files.append(
                    {
                        "file": p.name,
                        "bytes": st.st_size,
                        "modified": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                    }
                )
    return {
        "id": fund_id,
        "fund": row.get("FundTickerUnique") or row.get("FundTicker"),
        "directory": str(folder),
        "files": files,
    }


def _guard_url(url: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("only http(s) URLs can be downloaded")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError as e:
        raise ToolError(f"cannot resolve {u.hostname}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ToolError(f"{u.hostname} resolves to a non-public address; refusing to download")
    return url


def _filename(url: str, headers: dict[str, str], override: str | None, content_type: str) -> str:
    name = override or ""
    if not name and (cd := headers.get("content-disposition")):
        if m := re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I):
            name = unquote(m.group(1).strip().strip('"'))
        elif m := re.search(r'filename="?([^";]+)"?', cd, re.I):
            name = m.group(1).strip()
    if not name:
        name = unquote(Path(urlparse(url).path).name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "document"
    if "." not in name:
        ext = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt"}.get(
            content_type.split(";")[0].strip(), ""
        )
        name += ext
    return name[:150]


def save_material(fund_id: int, url: str, filename: str | None = None) -> dict[str, Any]:
    """Download one document into the fund's Materials directory. Never overwrites: identical content is
    reported as a duplicate, different content under the same name is saved with a date suffix."""
    row = _fund_row(fund_id)
    folder = materials_dir(row)
    folder.mkdir(parents=True, exist_ok=True)
    _guard_url(url)
    limit = config.DOWNLOAD_MAX_MB * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    try:
        with httpx2.Client(follow_redirects=True, timeout=60, headers={"User-Agent": config.USER_AGENT}) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ToolError(f"HTTP {resp.status_code} for {url}")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ToolError(f"download exceeds {config.DOWNLOAD_MAX_MB} MB")
                    chunks.append(chunk)
                final_url = str(resp.url)
    except httpx2.HTTPError as e:
        raise ToolError(f"download failed: {e}") from None
    data = b"".join(chunks)
    if not data:
        raise ToolError("empty response")
    digest = hashlib.sha256(data).hexdigest()
    name = _filename(final_url, headers, filename, headers.get("content-type", ""))
    target = folder / name
    status = "new"
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            status = "duplicate"
        else:
            status = "renamed"
            stem, suffix = Path(name).stem, Path(name).suffix
            n = 2
            target = folder / f"{stem} ({time.strftime('%Y-%m-%d')}){suffix}"
            while target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                target = folder / f"{stem} ({time.strftime('%Y-%m-%d')}) ({n}){suffix}"
                n += 1
            if target.exists():
                status = "duplicate"
    if status != "duplicate":
        target.write_bytes(data)
    with (folder / ".downloads.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "url": url,
                    "file": target.name,
                    "sha256": digest,
                    "status": status,
                }
            )
            + "\n"
        )
    return {
        "id": fund_id,
        "fund": row.get("FundTickerUnique") or row.get("FundTicker"),
        "status": status,
        "file": str(target),
        "bytes": len(data),
        "content_type": headers.get("content-type"),
        "sha256": digest,
        "final_url": final_url,
    }


def find_rows(
    table: str, column: str, value: str, columns: list[str] | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """All rows where column = value, read-only. `columns` trims wide rows (JSON blobs) from the result."""
    with db.connect() as conn:
        tables = db._tables(conn)
        if table not in tables:
            raise ToolError(f"unknown table {table!r}; call list_tables")
        known = [c["name"] for c in db._columns(conn, table)]
        if column not in known:
            raise ToolError(f"unknown column {column!r} in {table!r}")
        selected = columns or known
        if bad := [c for c in selected if c not in known]:
            raise ToolError(f"unknown column(s) {bad} in {table!r}")
        cols = ", ".join(f'"{c}"' for c in selected)
        rows = conn.execute(f'SELECT {cols} FROM "{table}" WHERE "{column}" = ? LIMIT ?', (value, limit)).fetchall()
        return [dict(r) for r in rows]
