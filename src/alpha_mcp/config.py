"""Configuration from environment variables, optionally loaded from .env files.

Sensitive values (origin host, remote binary path, repo URL) have no defaults in code. They come
from the environment or from the first .env file that defines them, in this order (real
environment variables always win):

  1. the file named by $ALPHA_MCP_ENV_FILE
  2. ./.env               (the working directory the MCP client started us in)
  3. the per-user file:   ~/.config/alpha-mcp/.env   (Linux)
                          ~/Library/Application Support/alpha-mcp/.env   (macOS)
                          %APPDATA%\\alpha-mcp\\.env   (Windows)
"""

import os
import shlex
import sys
from pathlib import Path

from dotenv import load_dotenv

WINDOWS = sys.platform == "win32"


def user_config_dir() -> Path:
    if WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "alpha-mcp"


def user_data_dir() -> Path:
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "alpha-mcp"


USER_ENV_FILE = user_config_dir() / ".env"


def env_file_candidates() -> list[Path]:
    out = [Path(os.environ["ALPHA_MCP_ENV_FILE"])] if os.environ.get("ALPHA_MCP_ENV_FILE") else []
    try:
        out.append(Path.cwd() / ".env")
    except OSError:  # cwd deleted
        pass
    out.append(USER_ENV_FILE)
    return out


def load_env_files() -> list[Path]:
    loaded = []
    for path in env_file_candidates():
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded


ENV_FILES = load_env_files()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _split_args(value: str) -> list[str]:
    # shlex in POSIX mode eats the backslashes of Windows paths, so use non-POSIX rules there.
    tokens = shlex.split(value, posix=not WINDOWS)
    return [t.strip('"') for t in tokens] if WINDOWS else tokens


# --- sensitive: no defaults in code, see module docstring ---------------------------------------
DB_ORIGIN = _get("ALPHA_MCP_DB_ORIGIN")  # [USER@]HOST:PATH of the live database
REMOTE_EXE = _get("ALPHA_MCP_REMOTE_EXE")  # sqlite3_rsync binary on the origin host (--exe)
GIT = _get("ALPHA_MCP_GIT")  # git+ssh://...@stable source that `setup` registers in the clients

# --- everything else has a sensible default -----------------------------------------------------
# Local replica. Absolute, so sqlite3_rsync never mistakes it for HOST:PATH and the file: URI is valid.
DB_LOCAL = Path(_get("ALPHA_MCP_DB_LOCAL") or user_data_dir() / "Global.db").expanduser().resolve()

# Local sqlite3_rsync binary (name on PATH or absolute path). On Windows PATHEXT finds sqlite3_rsync.exe.
SQLITE3_RSYNC = _get("ALPHA_MCP_SQLITE3_RSYNC", "sqlite3_rsync")

# ssh program (--ssh). sqlite3_rsync passes this as ONE token, so options like BatchMode go in
# ~/.ssh/config for the host (or point this at a tiny wrapper script). Empty = plain "ssh".
SSH = _get("ALPHA_MCP_SSH")

# Verbosity flag handed to sqlite3_rsync. -vvv adds the "page updates" summary line; never go below
# -vv, because then it appends "2>/dev/null" to the remote command, which a Windows shell on the
# origin host does not understand.
SYNC_VERBOSITY = _get("ALPHA_MCP_SYNC_VERBOSITY", "-vvv")
SYNC_TIMEOUT = int(_get("ALPHA_MCP_SYNC_TIMEOUT", "600"))
# Anything else to pass through, e.g. "--protocol 1" for an older remote binary or "--port 2222".
SYNC_ARGS = _split_args(_get("ALPHA_MCP_SYNC_ARGS"))

# Auto-sync before a query when the replica is missing or older than this (seconds). 0 disables.
DB_MAX_AGE = int(_get("ALPHA_MCP_DB_MAX_AGE", str(6 * 3600)))

# Only these tables are queryable. Empty = every table in the DB (fine for a read-only replica).
TABLE_ALLOWLIST = {t.strip() for t in _get("ALPHA_MCP_TABLES").split(",") if t.strip()}

# --- fund review: local writes + documents ----------------------------------------------------------
# Documents are stored locally, mirroring the layout under the DataSets root on the origin:
#   <DATASETS_PREFIX>\Crypto\Bitwise\<fund>\DataSet  ->  <MATERIALS_ROOT>/Crypto/Bitwise/<fund>/Materials/
MATERIALS_ROOT = Path(_get("ALPHA_MCP_MATERIALS_ROOT") or user_data_dir() / "Materials").expanduser().resolve()
DATASETS_PREFIX = _get("ALPHA_MCP_DATASETS_PREFIX", r"Z:\DataSets")
DOWNLOAD_MAX_MB = int(_get("ALPHA_MCP_DOWNLOAD_MAX_MB", "50"))
USER_AGENT = _get("ALPHA_MCP_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) alpha-mcp/0.1 document fetcher")
