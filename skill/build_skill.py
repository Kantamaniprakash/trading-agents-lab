"""Validate the agent skill and package it as an uploadable ZIP.

Usage:  python skill/build_skill.py [--skip-remote-check]
Writes: dist/trading-agents-desk.zip

The archive holds one top-level folder named after the skill, which is the
layout skill marketplaces and agent harnesses expect. The build is
deterministic: the same sources always produce byte-identical output, on any
operating system.

The ZIP contains instructions only. The code those instructions run is
installed from this repository at a pinned release tag, so the build also
checks that the pinned tag matches the package version and, unless told not
to, that the release can actually be downloaded.
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path(__file__).resolve().parent / "trading-agents-desk"
DIST_DIR = REPO_ROOT / "dist"

_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_MAX_NAME_CHARS = 64
_MAX_DESCRIPTION_CHARS = 1024
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh"}
_FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
# A plain YAML scalar must not start with an indicator character, and ": " or
# " #" inside it would be read as a mapping or a comment.
_YAML_UNSAFE_START = tuple("!&*[]{}>|@`'\"%#,?-")
_RELEASE_URL_RE = re.compile(
    r"https://github\.com/Kantamaniprakash/trading-agents-lab/archive/refs/tags/"
    r"(v[0-9][0-9A-Za-z.\-]*)\.zip"
)


def read_frontmatter(skill_md: Path) -> dict[str, str]:
    """Parse the flat ``key: value`` YAML frontmatter at the top of a SKILL.md.

    Only single-line plain scalars are accepted, and anything a real YAML
    parser would read differently is rejected, so this hand-rolled reader and
    a marketplace's YAML library always agree on the values.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{skill_md}: missing '---' frontmatter opener on line 1")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        value = value.strip()
        if not sep or not key.strip() or key[0].isspace():
            raise ValueError(f"{skill_md}: unsupported frontmatter line: {line!r}")
        if not value:
            raise ValueError(f"{skill_md}: frontmatter key {key.strip()!r} has no value")
        if value.startswith(_YAML_UNSAFE_START) or ": " in value or " #" in value \
                or value.endswith(":"):
            raise ValueError(
                f"{skill_md}: the value of {key.strip()!r} is not a plain YAML scalar "
                "(it starts with an indicator character, or contains ': ' or ' #')"
            )
        if key.strip() in fields:
            raise ValueError(f"{skill_md}: duplicate frontmatter key {key.strip()!r}")
        fields[key.strip()] = value
    raise ValueError(f"{skill_md}: frontmatter is never closed with '---'")


def package_version(repo_root: Path = REPO_ROOT) -> str:
    """The ``version`` declared in pyproject.toml."""
    text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml declares no version")
    return match.group(1)


def pinned_release_urls(skill_dir: Path = SKILL_DIR) -> list[str]:
    """Every install URL in the skill's Markdown; ValueError unless all are pinned.

    A buyer's agent installs whatever the URL points at. A branch can change
    after the listing was reviewed; a release tag cannot be moved by accident.
    """
    urls: list[str] = []
    for path in sorted(skill_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "archive/refs/heads/" in text:
            raise ValueError(
                f"{path.relative_to(skill_dir)}: installs from a branch "
                "(archive/refs/heads/...). Pin a release tag instead."
            )
        urls += [match.group(0) for match in _RELEASE_URL_RE.finditer(text)]
    if not urls:
        raise ValueError("the skill names no pinned release URL to install from")
    return urls


def _packaged_files(skill_dir: Path) -> list[Path]:
    """Regular files to ship, sorted; hidden files, hidden folders, caches and
    symlinks are left out."""
    files = []
    for path in sorted(skill_dir.rglob("*")):
        relative = path.relative_to(skill_dir)
        if path.is_symlink() or not path.is_file():
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        files.append(path)
    return files


def validate(skill_dir: Path = SKILL_DIR, version: str | None = None) -> list[Path]:
    """Check the skill folder and return the files to package, sorted.

    ``version`` is the package version the pinned install URLs must match; it
    is read from pyproject.toml when the skill is this repository's own.
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

    files = _packaged_files(skill_dir)
    for path in files:
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            raise ValueError(
                f"{path.relative_to(skill_dir)}: only text files are packaged "
                f"({', '.join(sorted(_TEXT_SUFFIXES))})"
            )

    if version is None and skill_dir == SKILL_DIR:
        version = package_version()
    if version is not None:
        for url in pinned_release_urls(skill_dir):
            tag = _RELEASE_URL_RE.fullmatch(url).group(1)
            if tag != f"v{version}":
                raise ValueError(
                    f"the skill installs release {tag} but pyproject.toml is at "
                    f"{version}; pin v{version}"
                )
    return files


def build(skill_dir: Path = SKILL_DIR, dist_dir: Path = DIST_DIR,
          version: str | None = None) -> Path:
    """Validate ``skill_dir`` and write ``{dist_dir}/{skill name}.zip``."""
    files = validate(skill_dir, version=version)
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_path = dist_dir / f"{skill_dir.name}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = f"{skill_dir.name}/{path.relative_to(skill_dir).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # same header whichever OS runs the build
            info.external_attr = 0o644 << 16
            # A Windows checkout may hold CRLF line endings; ship LF everywhere.
            data = path.read_bytes().replace(b"\r\n", b"\n")
            archive.writestr(info, data)
    return out_path


def release_is_downloadable(url: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Whether ``url`` answers a HEAD request with success; ``(ok, detail)``."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    skip_remote = "--skip-remote-check" in args
    try:
        validate()
        urls = sorted(set(pinned_release_urls()))
        if not skip_remote:
            for url in urls:
                ok, detail = release_is_downloadable(url)
                if not ok:
                    raise ValueError(
                        f"the pinned release cannot be downloaded ({detail}): {url}\n"
                        "Merge the code, then create and push the tag, before uploading "
                        "this skill: every buyer's install would fail.\n"
                        "To build anyway for a local look, pass --skip-remote-check."
                    )
        out_path = build()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    with zipfile.ZipFile(out_path) as archive:
        for info in archive.infolist():
            print(f"  {info.file_size:>6}  {info.filename}")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    if skip_remote:
        print("warning: the pinned release was NOT checked. Do not upload this ZIP "
              "until the tag exists on GitHub.", file=sys.stderr)


if __name__ == "__main__":
    main()
