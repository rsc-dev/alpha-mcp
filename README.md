# alpha-mcp

MCP server for the Alpha team: investing prompts plus read-only lookups in a local replica of
`Global.db`, kept current with `sqlite3_rsync`. One command to install, auto-updates from git,
runs on Linux, macOS and Windows, works from Claude Code, Claude Desktop and Codex.

## What it exposes

| Kind | Name | Purpose |
|---|---|---|
| tool | `sync_db` | Replicate `Global.db` from the origin host (only changed pages are transferred) |
| tool | `list_tables` | Tables with primary key, columns and row counts |
| tool | `get_row(table, key, key_column=None)` | One row by key; pass `key_column` for natural keys (`Yahoo`, `ThesisCode`, `Ticker`) |
| tool | `find_rows(table, column, value, columns?)` | Every row where column = value, e.g. all funds of a provider |
| tool | `get_prompt(name, arguments)` | Rendered prompt text, for clients without MCP prompt support (Codex) |
| tool | `update_fund(id, fields, sources?)` | Write verified fields of one `FundsDataSets` row to the local replica (see Fund review) |
| tool | `chatgpt_response_schema()` | The key list `ChatGPTResponse` values must follow |
| tool | `finish_provider_review(provider, notes?)` | Stamp `PromptProviderFunds.LastReviewDate` |
| tool | `list_materials(id)`, `save_material(id, url)` | Documents stored for a fund; download a new one, never overwriting |
| prompt | `fund_provider_review` | Every file in `src/alpha_mcp/prompts/` becomes a prompt |

Before any query the server syncs automatically when the replica is missing or older than
`ALPHA_MCP_DB_MAX_AGE` (default 6 h). A failed auto-sync (offline, VPN down) logs a warning and
serves the existing replica. The server never writes to the origin.

## Requirements

- [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh` (Linux/macOS),
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows)
- `sqlite3_rsync` on `PATH`, SQLite >= 3.50. It is in the `sqlite-tools` bundle on sqlite.org
  (`sqlite3_rsync.exe` on Windows). To build it on Linux/macOS from the amalgamation:
  `gcc -O2 -DSQLITE_ENABLE_DBPAGE_VTAB tool/sqlite3_rsync.c sqlite3.c -lm -o sqlite3_rsync`
- OpenSSH client (`ssh` / `ssh.exe`, preinstalled on Windows 10+) with key access to the origin
  host. `sqlite3_rsync --ssh` accepts a program name only, so options go in `~/.ssh/config`
  (`C:\Users\you\.ssh\config` on Windows):

  ```
  Host <origin-host>
    User <origin-user>
    IdentityFile ~/.ssh/<key>
    IdentitiesOnly yes
    BatchMode yes
    ConnectTimeout 10
  ```

  `BatchMode yes` matters: the MCP server has no terminal, so a password prompt would hang until
  the sync timeout. Accept the host key once by hand (`ssh <origin-host>`) before relying on it.

## Install (teammates)

The origin host, the remote binary path and the repo URL are not in the code. Pass them once;
`setup` stores them in the per-user `.env` and registers the server in every client it finds:

```bash
uvx --from "git+ssh://git@github.com/rsc-dev/alpha-mcp.git@stable" alpha-mcp setup \
  --origin "<user>@<host>:<path>/Global.db" \
  --remote-exe "<path to sqlite3_rsync on the host>" \
  --git "git+ssh://git@github.com/rsc-dev/alpha-mcp.git@stable"
```

Registered as `alpha` in Claude Code (user scope), Codex (with skills exported to
`~/.agents/skills`), and Claude Desktop (its JSON config, if the app is installed). Every later
client start re-checks the `stable` tag and rebuilds only if it moved, so updates need no
reinstall. `alpha-mcp remove` reverses all of it; `alpha-mcp remove --purge` also deletes the local
replica and the per-user `.env`.

Prompts appear as `/mcp__alpha__fund_provider_review` in Claude Code, under "+" in Claude Desktop, and as
`$fund_provider_review` skills in Codex (Codex has no MCP prompt support yet; the `get_prompt` tool is the
second fallback there). Codex: raise `startup_timeout_sec` to 60 and `tool_timeout_sec` to 300 in
`[mcp_servers.alpha]` of `~/.codex/config.toml`; the defaults are too tight for a cold build and a
first full sync.

## Configuration

Everything is an environment variable. Values come from the process environment first, then from
the first `.env` file that defines them:

1. the file named by `ALPHA_MCP_ENV_FILE`
2. `./.env` in the working directory the client started the server in
3. the per-user file: `~/.config/alpha-mcp/.env` (Linux), `~/Library/Application Support/alpha-mcp/.env`
   (macOS), `%APPDATA%\alpha-mcp\.env` (Windows). This is what `setup` writes, mode 0600 on POSIX.

`.env` is gitignored; `.env.example` documents every key. Rule 2 means a `.env` in whatever repo you
open can redirect the sync to another host, so keep the per-user file as the source of truth and
treat repo `.env` files as you would any other executable content from that repo.

| Variable | Default | Meaning |
|---|---|---|
| `ALPHA_MCP_DB_ORIGIN` | (required) | `[USER@]HOST:PATH` of the live DB |
| `ALPHA_MCP_REMOTE_EXE` | | `--exe` on the origin host; leave empty for a local-file origin |
| `ALPHA_MCP_GIT` | | Source `setup` registers in the clients |
| `ALPHA_MCP_DB_LOCAL` | `~/.local/share/alpha-mcp/Global.db`, `%LOCALAPPDATA%\alpha-mcp\Global.db` on Windows | Local replica |
| `ALPHA_MCP_SQLITE3_RSYNC` | `sqlite3_rsync` | Local binary name or full path |
| `ALPHA_MCP_SSH` | (ssh) | `--ssh` program name |
| `ALPHA_MCP_SYNC_VERBOSITY` | `-vvv` | Keep `-vv` or more: below that the remote command gets `2>/dev/null`, which cmd.exe on a Windows origin rejects |
| `ALPHA_MCP_SYNC_ARGS` | | Extra args, e.g. `--protocol 1`, `--port 2222` |
| `ALPHA_MCP_SYNC_TIMEOUT` | `600` | Seconds |
| `ALPHA_MCP_DB_MAX_AGE` | `21600` | Auto-sync when the replica is older than this; `0` disables |
| `ALPHA_MCP_TABLES` | | Comma-separated allowlist of queryable tables; empty = all |
| `ALPHA_MCP_EXPORT_SKILLS` | | `codex` or `claude`: export prompts as skills on every launch |
| `ALPHA_MCP_ENV_FILE` | | Explicit `.env` path, checked first |
| `ALPHA_MCP_MATERIALS_ROOT` | `~/.local/share/alpha-mcp/Materials` | Local root for fund documents |
| `ALPHA_MCP_DATASETS_PREFIX` | `Z:\DataSets` | Prefix stripped from `DataSet` paths when mapping to the local root |
| `ALPHA_MCP_DOWNLOAD_MAX_MB` | `50` | Per-document download limit |
| `ALPHA_MCP_USER_AGENT` | browser-like | `User-Agent` for document downloads |

The replica is switched to WAL mode after the first sync on purpose: in rollback-journal mode a
concurrent reader makes `sqlite3_rsync` fail with `database is locked`. The `-wal`/`-shm` files
next to it are normal.

## Fund review

`/mcp__alpha__fund_provider_review <provider>` runs the provider review: for every `FundsDataSets`
row of the provider the client researches the fund (its own web tools), stores the fund's documents
with `save_material`, writes the resolved fields with `update_fund`, and finally stamps the
provider with `finish_provider_review`. The rules of the review live in the tools, not only in the
prompt text:

- `update_fund` accepts exactly the review's column list (CUSIP, CINS, ISIN, SEDOL, LEI, RIC, BBGID,
  FIGI, MIC, the four URL columns, FundName, InceptionDate, FundClosedDate, FundDomicile, AssetClass,
  FundType, the three frequency columns, IndexName, ChatGPTResponse, ChatGPTLastUpdate, Processed,
  Notes) and rejects everything else, including `ReviewFinished` and `FundClosed`.
- ISIN, CUSIP/CINS, SEDOL, LEI, FIGI/BBGID are checksum-validated; MIC is format-checked.
- `InceptionDate`/`FundClosedDate` must be `YYYY.MM.DD`; `ChatGPTLastUpdate` is `YYYY.MM.DD HH:MM:SS`
  and is set automatically when `ChatGPTResponse` changes; `ChatGPTResponse` must keep the key set
  your existing rows use (`chatgpt_response_schema` returns it); URL columns reject Internet Archive
  links; a value that is new to a column's vocabulary comes back as a warning, not an error; `Notes`
  are appended, never replaced.
- `PromptProviderFunds.LastReviewDate` is written as `YYYY-MM-DD HH:MM:SS`; `ReviewFinished` is never touched.

**Writes never reach the origin.** They go to the local replica, and every statement is also
appended to `<replica>.pending.sql` (plus before/after values in `<replica>.audit.jsonl`). While
edits are pending, `sync_db` and the automatic staleness sync refuse to run, because a sync would
overwrite them. Apply the SQL to the origin yourself, then `alpha-mcp pending --clear` (the file is
set aside as `.discarded`, not deleted), or push the whole replica back with your upload script.
Point `ALPHA_MCP_DB_LOCAL` at the `Global.db` your download/upload scripts use if you want the
server to edit that same file.

**Documents stay local.** `save_material` stores into `ALPHA_MCP_MATERIALS_ROOT`, mirroring the
layout under `ALPHA_MCP_DATASETS_PREFIX` on the origin, so
`Z:\DataSets\Crypto\Bitwise\BITCCLS - ...\DataSet` maps to
`<root>/Crypto/Bitwise/BITCCLS - .../Materials/`. Identical content is reported as `duplicate`,
changed content under an existing name is stored with a date suffix, and each download is logged in
the directory's `.downloads.jsonl`. Only public http(s) hosts are fetched (private and loopback
addresses are refused), up to `ALPHA_MCP_DOWNLOAD_MAX_MB`.

## Prompts

One Markdown file per prompt in `src/alpha_mcp/prompts/`, TOML front matter between `+++` lines,
`{{name}}` placeholders in the body (`{{today}}`, `{{today_dotted}}` and `{{now}}` are always available):

```markdown
+++
name = "my_prompt"
title = "Shown as the prompt's label"
description = "Shown in the client's prompt menu."

[[arguments]]
name = "ticker"
description = "Yahoo symbol, e.g. NVDA"
required = true
+++
Call `get_row("Tickers", "{{ticker}}", key_column="Yahoo")` and ...
```

Adding a prompt is a PR that adds a file. Tell the model which `get_row` calls to make; that is
what grounds the prompt in the DB.

## CLI

```bash
alpha-mcp                                  # serve over stdio (what clients run)
alpha-mcp sync [--discard-local]           # replicate Global.db now (refuses while edits are pending)
alpha-mcp pending [--clear]                # SQL of local edits not yet applied to the origin
alpha-mcp setup [--origin] [--remote-exe] [--git]   # save settings, register in clients
alpha-mcp remove [--purge]                 # unregister; --purge also deletes replica + .env
alpha-mcp export-skills codex|claude       # write prompts as SKILL.md files
```

## Development

```bash
cp .env.example .env       # fill in the origin; ./.env is read when running from this checkout
uv sync --group dev
uv run alpha-mcp sync      # real sync against the origin host
uv run ruff check src && uv run ruff format --check src
uv run --with "mcp[cli]" mcp dev src/alpha_mcp/server.py:mcp   # MCP Inspector
```

To exercise the sync without the origin host, point it at a local copy (`sqlite3_rsync` accepts
two local paths):

```bash
env ALPHA_MCP_DB_ORIGIN=/path/to/Global.db ALPHA_MCP_REMOTE_EXE= ALPHA_MCP_DB_LOCAL=/tmp/Global.db uv run alpha-mcp sync
```

The checked-in `.mcp.json` registers this checkout as server `alpha` for Claude Code sessions
started in this directory (project scope, approval prompted on first use).

## Release

```bash
# bump version in pyproject.toml and src/alpha_mcp/__init__.py, then
git tag -f stable && git push -f origin stable
```

Whoever can move `stable` runs code on every teammate's machine at their next session: protect the
tag, require reviews on `main`, and pin dependencies (`uvx --from git` does not read `uv.lock`).
