"""Tests for the agent skill in ``skill/``: packaging rules, and that the
commands the skill tells an agent to run still exist in the CLI.

The second part is the one that matters over time. The skill is prose, so
nothing else fails when a subcommand or flag is renamed.
"""
from __future__ import annotations

import importlib.util
import re
import shlex
import zipfile
from pathlib import Path

import pytest

from tradinglab.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "trading-agents-desk"

_spec = importlib.util.spec_from_file_location(
    "build_skill", REPO_ROOT / "skill" / "build_skill.py"
)
build_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_skill)

_CLI_PREFIX = "python -m tradinglab.cli "


def _documented_cli_commands() -> list[tuple[str, str]]:
    """Every ``python -m tradinglab.cli ...`` line inside a fenced bash block."""
    found: list[tuple[str, str]] = []
    for path in sorted(SKILL_DIR.rglob("*.md")):
        in_bash = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_bash = stripped == "```bash"
                continue
            if in_bash and stripped.startswith(_CLI_PREFIX):
                found.append((path.name, stripped[len(_CLI_PREFIX):]))
    return found


def test_frontmatter_is_valid_and_matches_folder():
    fields = build_skill.read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fields["name"] == SKILL_DIR.name == "trading-agents-desk"
    assert 0 < len(fields["description"]) <= 1024
    assert set(fields) <= {"name", "description", "license"}  # portable across harnesses


def test_skill_docs_cover_every_workflow():
    commands = _documented_cli_commands()
    used = {args.split()[0] for _, args in commands}
    assert {"snapshot", "desk", "agents", "probe", "baselines", "train-ml"} <= used


@pytest.mark.parametrize("source,args", _documented_cli_commands())
def test_documented_command_parses_with_the_real_cli(source, args):
    """A renamed subcommand or flag must fail here, not in a buyer's terminal."""
    argv = shlex.split(args)
    if "--help" in argv:
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(argv)
        assert exc.value.code == 0
    else:
        build_parser().parse_args(argv)  # argparse exits non-zero on any mismatch


def test_roles_reference_names_every_pipeline_role():
    text = (SKILL_DIR / "references" / "roles.md").read_text(encoding="utf-8").lower()
    for role in (
        "market analyst", "fundamentals analyst", "news analyst", "sentiment analyst",
        "bull researcher", "bear researcher", "facilitator", "trader",
        "risky analyst", "safe analyst", "neutral analyst", "fund manager",
    ):
        assert role in text, f"roles.md no longer describes the {role}"


def test_skill_states_its_limits():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "not investment advice" in text
    assert "never trades" in text
    assert "did not beat buy-and-hold" in text


def test_build_writes_a_clean_deterministic_zip(tmp_path):
    first = build_skill.build(dist_dir=tmp_path / "a")
    second = build_skill.build(dist_dir=tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
    assert "trading-agents-desk/SKILL.md" in names
    assert "trading-agents-desk/references/roles.md" in names
    assert all(name.startswith("trading-agents-desk/") for name in names)
    assert not [n for n in names if re.search(r"__pycache__|\.pyc$|/\.", n)]


def test_validate_rejects_name_folder_mismatch(tmp_path):
    bad = tmp_path / "other-name"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "---\nname: trading-agents-desk\ndescription: x\n---\nbody\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must match the folder name"):
        build_skill.validate(bad)


def test_validate_rejects_binary_payloads(tmp_path):
    skill = tmp_path / "demo-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: x\n---\nbody\n", encoding="utf-8"
    )
    (skill / "tool.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="only text files"):
        build_skill.validate(skill)
