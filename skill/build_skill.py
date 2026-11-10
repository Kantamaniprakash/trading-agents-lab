"""Validate the agent skill and package it as an uploadable ZIP.

Usage:  python skill/build_skill.py
Writes: dist/trading-agents-desk.zip

The archive holds one top-level folder named after the skill, which is the
layout skill marketplaces and agent harnesses expect. The build is
deterministic: the same sources always produce byte-identical output.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent / "trading-agents-desk"
DIST_DIR = Path(__file__).resolve().parents[1] / "dist"

_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_MAX_NAME_CHARS = 64
_MAX_DESCRIPTION_CHARS = 1024
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh"}
_FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


def read_frontmatter(skill_md: Path) -> dict[str, str]:
    """Parse the flat ``key: value`` YAML frontmatter at the top of a SKILL.md."""
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{skill_md}: missing '---' frontmatter opener on line 1")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key[0].isspace():
            raise ValueError(f"{skill_md}: unsupported frontmatter line: {line!r}")
        fields[key.strip()] = value.strip()
    raise ValueError(f"{skill_md}: frontmatter is never closed with '---'")


def validate(skill_dir: Path = SKILL_DIR) -> list[Path]:
    """Check the skill folder and return the files to package, sorted.

    Raises ``ValueError`` naming the first problem found.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"{skill_dir}: SKILL.md not found")
    fields = read_frontmatter(skill_md)

    name = fields.get("name", "")
    if not _NAME_RE.fullmatch(name) or len(name) > _MAX_NAME_CHARS:
        raise ValueError(
            f"name {name!r} must be lowercase letters, digits and hyphens, "
            f"at most {_MAX_NAME_CHARS} characters"
        )
    if name != skill_dir.name:
        raise ValueError(f"name {name!r} must match the folder name {skill_dir.name!r}")

    description = fields.get("description", "")
    if not description:
        raise ValueError("description is required")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"description is {len(description)} characters; "
            f"the limit is {_MAX_DESCRIPTION_CHARS}"
        )

    files = sorted(
        p for p in skill_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")
    )
    for path in files:
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            raise ValueError(
                f"{path.relative_to(skill_dir)}: only text files are packaged "
                f"({', '.join(sorted(_TEXT_SUFFIXES))})"
            )
    return files


def build(skill_dir: Path = SKILL_DIR, dist_dir: Path = DIST_DIR) -> Path:
    """Validate ``skill_dir`` and write ``{dist_dir}/{skill name}.zip``."""
    files = validate(skill_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_path = dist_dir / f"{skill_dir.name}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return out_path


def main() -> None:
    try:
        out_path = build()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    with zipfile.ZipFile(out_path) as archive:
        for info in archive.infolist():
            print(f"  {info.file_size:>6}  {info.filename}")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
