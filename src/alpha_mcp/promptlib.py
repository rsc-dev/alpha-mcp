"""Prompt library: Markdown files in alpha_mcp/prompts/ with a TOML front matter block (+++ ... +++).

One source of truth, three delivery paths:
  1. native MCP prompts  (Claude Code /mcp__<server>__<name>, Claude Desktop "+" menu, ...)
  2. a `get_prompt` tool for clients that only speak tools (Codex today)
  3. exported as Agent Skills (SKILL.md) for Codex / Claude Code — see export.py
"""

import inspect
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from typing import Annotated

from mcp.server.mcpserver.prompts.base import Prompt
from pydantic import Field

_FRONT = re.compile(r"^\+\+\+\s*\n(.*?)\n\+\+\+\s*\n(.*)$", re.S)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class Arg:
    name: str
    description: str = ""
    required: bool = False
    default: str = ""


@dataclass
class PromptSpec:
    name: str
    title: str
    description: str
    body: str
    args: list[Arg] = field(default_factory=list)

    def render(self, values: dict[str, str | None]) -> str:
        merged = builtins() | {a.name: (values.get(a.name) or a.default) for a in self.args}
        missing = [a.name for a in self.args if a.required and not merged[a.name]]
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(missing)}")
        return _PLACEHOLDER.sub(lambda m: merged.get(m.group(1), m.group(0)), self.body).strip()


def builtins() -> dict[str, str]:
    """Placeholders every prompt can use without declaring them: {{now}}, {{today}}, {{today_dotted}}."""
    now = datetime.now()
    return {
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": now.strftime("%Y-%m-%d"),
        "today_dotted": now.strftime("%Y.%m.%d"),
    }


def load_specs() -> list[PromptSpec]:
    specs = []
    for entry in sorted((resources.files("alpha_mcp") / "prompts").iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        m = _FRONT.match(entry.read_text(encoding="utf-8"))
        if not m:
            raise ValueError(f"{entry.name}: missing +++ front matter")
        meta, body = tomllib.loads(m.group(1)), m.group(2)
        specs.append(
            PromptSpec(
                name=meta.get("name", entry.name[:-3]),
                title=meta.get("title", entry.name[:-3]),
                description=meta["description"],
                body=body,
                args=[Arg(**a) for a in meta.get("arguments", [])],
            )
        )
    return specs


def to_mcp_prompt(spec: PromptSpec) -> Prompt:
    """Build an MCP Prompt with a real signature so clients get a proper argument form."""

    def render(**kwargs: str) -> str:
        return spec.render(kwargs)

    params = [
        inspect.Parameter(
            a.name,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if a.required else a.default,
            annotation=Annotated[str, Field(description=a.description)],
        )
        for a in spec.args
    ]
    # Both the SDK (inspect.signature) and pydantic's validate_call (get_type_hints) must see the arguments.
    render.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    render.__annotations__ = {p.name: p.annotation for p in params} | {"return": str}
    render.__name__ = spec.name
    render.__doc__ = spec.description
    return Prompt.from_function(render, name=spec.name, title=spec.title, description=spec.description)
