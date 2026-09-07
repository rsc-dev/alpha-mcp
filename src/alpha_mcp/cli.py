"""`alpha-mcp` entry point.

alpha-mcp                          serve over stdio (what MCP clients run)
alpha-mcp sync                     replicate Global.db now (cron / manual)
alpha-mcp export-skills codex|claude
alpha-mcp setup [--origin X] [--remote-exe X] [--git X]
                                   save the given settings to the per-user .env, then register the
                                   server in Claude Code, Codex and Claude Desktop (whichever exist)
alpha-mcp remove [--purge]         unregister everywhere and delete the exported skills;
                                   --purge also deletes the replica and the per-user .env
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

SERVER_NAME = "alpha"
ENV_KEYS = {"origin": "ALPHA_MCP_DB_ORIGIN", "remote_exe": "ALPHA_MCP_REMOTE_EXE", "git": "ALPHA_MCP_GIT"}


# --- helpers -------------------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str] | None:
    """Run a client CLI if it is installed. shutil.which resolves .cmd/.exe shims on Windows."""
    exe = shutil.which(cmd[0])
    if exe is None:
        return None
    return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, check=check)


def _launch_command() -> list[str]:
    git = os.environ.get("ALPHA_MCP_GIT", "").strip() or config.GIT
    if not git:
        sys.exit("ALPHA_MCP_GIT is not set: pass --git git+ssh://... or put it in .env")
    return ["uvx", "--from", git, "alpha-mcp"]


def _desktop_config() -> Path | None:
    """claude_desktop_config.json, only if Claude Desktop's config directory exists."""
    if config.WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Claude"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude"
    return base / "claude_desktop_config.json" if base.is_dir() else None


def _edit_desktop(register: bool) -> str | None:
    path = _desktop_config()
    if path is None:
        return None
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    servers = cfg.setdefault("mcpServers", {})
    if register:
        launch = _launch_command()
        # Desktop does not inherit the shell PATH, so the uvx path must be absolute.
        servers[SERVER_NAME] = {"command": shutil.which("uvx") or "uvx", "args": launch[1:]}
    else:
        servers.pop(SERVER_NAME, None)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return str(path)


def write_user_env(values: dict[str, str]) -> Path:
    """Upsert KEY="value" lines in the per-user .env (created 0600 on POSIX)."""
    path = config.USER_ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for key, value in values.items():
        line = f'{key}="{value}"'
        for i, existing in enumerate(lines):
            if not existing.lstrip().startswith("#") and existing.split("=", 1)[0].strip() == key:
                lines[i] = line
                break
        else:
            lines.append(line)
        os.environ[key] = value  # so the rest of this run sees the new value
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not config.WINDOWS:
        path.chmod(0o600)
    return path


# --- commands ------------------------------------------------------------------------------------


def setup(args: argparse.Namespace) -> None:
    result: dict[str, object] = {"server": SERVER_NAME, "registered": [], "warnings": []}
    given = {ENV_KEYS[k]: v for k, v in vars(args).items() if k in ENV_KEYS and v}
    if given:
        result["env_file"] = str(write_user_env(given))
    launch = _launch_command()
    result["command"] = launch

    if _run(["claude", "--version"]) is not None:
        _run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME])
        _run(["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", *launch], check=True)
        result["registered"].append("claude-code")
    if _run(["codex", "--version"]) is not None:
        _run(["codex", "mcp", "remove", SERVER_NAME])
        _run(["codex", "mcp", "add", SERVER_NAME, "--env", "ALPHA_MCP_EXPORT_SKILLS=codex", "--", *launch], check=True)
        from .export import export_skills

        export_skills("codex")
        result["registered"].append("codex")
    if (desktop := _edit_desktop(register=True)) is not None:
        result["registered"].append(f"claude-desktop ({desktop})")

    if not (os.environ.get("ALPHA_MCP_DB_ORIGIN") or config.DB_ORIGIN):
        result["warnings"].append("ALPHA_MCP_DB_ORIGIN is not set; sync_db will fail until it is (setup --origin ...)")
    if shutil.which(config.SQLITE3_RSYNC) is None:
        result["warnings"].append(f"{config.SQLITE3_RSYNC} not found on PATH")
    print(json.dumps(result, indent=2))


def remove(args: argparse.Namespace) -> None:
    from .export import remove_skills

    result: dict[str, object] = {"server": SERVER_NAME, "unregistered": [], "deleted": []}
    if _run(["claude", "mcp", "remove", "--scope", "user", SERVER_NAME]) is not None:
        result["unregistered"].append("claude-code")
    if _run(["codex", "mcp", "remove", SERVER_NAME]) is not None:
        result["unregistered"].append("codex")
    if (desktop := _edit_desktop(register=False)) is not None:
        result["unregistered"].append(f"claude-desktop ({desktop})")
    for target in ("codex", "claude"):
        result["deleted"] += [str(p) for p in remove_skills(target)]
    if args.purge:
        for path in (
            config.DB_LOCAL,
            config.DB_LOCAL.with_name(config.DB_LOCAL.name + "-wal"),
            config.DB_LOCAL.with_name(config.DB_LOCAL.name + "-shm"),
            config.DB_LOCAL.with_name(config.DB_LOCAL.name + ".synced"),
            config.USER_ENV_FILE,
        ):
            if path.exists():
                path.unlink()
                result["deleted"].append(str(path))
    print(json.dumps(result, indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="alpha-mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="serve over stdio (default)")
    sy = sub.add_parser("sync", help="replicate Global.db now")
    sy.add_argument("--discard-local", action="store_true", help="set aside pending local edits and sync anyway")
    pe = sub.add_parser("pending", help="show local edits not yet applied to the origin")
    pe.add_argument("--clear", action="store_true", help="set the pending file aside (kept as .discarded)")
    e = sub.add_parser("export-skills", help="write prompts as SKILL.md files")
    e.add_argument("target", choices=["codex", "claude"])
    s = sub.add_parser("setup", help="save settings and register the server in the installed clients")
    s.add_argument("--origin", help="[USER@]HOST:PATH of the live Global.db")
    s.add_argument(
        "--remote-exe", help="sqlite3_rsync binary on the origin host, e.g. Z:/Tools/sqlite/sqlite3_rsync.exe"
    )
    s.add_argument("--git", help="git+ssh://...@stable source the clients should run")
    r = sub.add_parser("remove", help="unregister the server from the installed clients")
    r.add_argument("--purge", action="store_true", help="also delete the local replica and the per-user .env")
    args = p.parse_args(argv)

    if args.cmd in (None, "serve"):
        if os.environ.get("ALPHA_MCP_EXPORT_SKILLS"):  # opt-in: keep skills in sync on every launch
            from .export import export_skills

            export_skills(os.environ["ALPHA_MCP_EXPORT_SKILLS"])
        from .server import serve

        serve()
    elif args.cmd == "sync":
        from mcp.server.mcpserver.exceptions import ToolError

        from . import db

        try:
            print(json.dumps(db.sync(discard_local=args.discard_local), indent=2))
        except ToolError as e:  # same message the model would get, without a traceback
            sys.exit(f"error: {e}")
    elif args.cmd == "pending":
        from . import db

        if args.clear:
            print(f"set aside: {db.discard_pending()}")
        elif db.PENDING_SQL.is_file():
            print(db.PENDING_SQL.read_text(encoding="utf-8"), end="")
        else:
            print("no pending edits", file=sys.stderr)
    elif args.cmd == "export-skills":
        from .export import export_skills

        for path in export_skills(args.target):
            print(path, file=sys.stderr)
    elif args.cmd == "setup":
        setup(args)
    elif args.cmd == "remove":
        remove(args)
