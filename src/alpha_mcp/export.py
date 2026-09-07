"""Export the prompt library as Agent Skills (SKILL.md) so tools-only clients get the same workflows.

Codex reads  ~/.agents/skills/<name>/SKILL.md   (and <repo>/.agents/skills)
Claude Code  ~/.claude/skills/<name>/SKILL.md   (and <repo>/.claude/skills)
"""

from pathlib import Path

from .promptlib import PromptSpec, load_specs

TARGETS = {
    "codex": Path.home() / ".agents" / "skills",
    "claude": Path.home() / ".claude" / "skills",
}
MARKER = "<!-- managed by alpha-mcp; edit prompts/ in the alpha-mcp repo instead -->"


def _skill_md(spec: PromptSpec) -> str:
    # Skills take free-text input, so placeholders become named slots the user fills in the request.
    args = "\n".join(f"- `{a.name}`: {a.description}{' (required)' if a.required else ''}" for a in spec.args)
    body = spec.body
    for a in spec.args:
        body = body.replace("{{" + a.name + "}}", f"<{a.name}>")
    return (
        f"---\nname: {spec.name}\ndescription: {spec.description}\n---\n{MARKER}\n\n"
        f"# {spec.title}\n\n"
        f"Inputs (take them from the user's request; ask if a required one is missing):\n{args}\n\n"
        f"{body.strip()}\n"
    )


def export_skills(target: str, root: Path | None = None) -> list[Path]:
    root = root or TARGETS[target]
    written = []
    for spec in load_specs():
        path = root / spec.name / "SKILL.md"
        content = _skill_md(spec)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue  # idempotent: only touch files that changed
        if path.exists() and MARKER not in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"refusing to overwrite unmanaged skill at {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def remove_skills(target: str, root: Path | None = None) -> list[Path]:
    """Delete the SKILL.md files this tool wrote (never a hand-written one)."""
    root = root or TARGETS[target]
    removed = []
    for spec in load_specs():
        path = root / spec.name / "SKILL.md"
        if path.exists() and MARKER in path.read_text(encoding="utf-8"):
            path.unlink()
            removed.append(path)
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
    return removed
