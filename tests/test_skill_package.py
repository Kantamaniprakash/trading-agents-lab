"""Tests for the agent skill in ``skill/``: packaging rules, the promises the
skill's text must keep, and that the commands it tells an agent to run still
exist in the CLI.

The last part is the one that matters over time. The skill is prose, so
nothing else fails when a subcommand or flag is renamed.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shlex
import time
import zipfile
from pathlib import Path

import pytest

from tradinglab import __version__
from tradinglab.agents.desk import snapshot_to_dict  # noqa: F401  (import check)
from tradinglab.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "trading-agents-desk"

_spec = importlib.util.spec_from_file_location(
    "build_skill", REPO_ROOT / "skill" / "build_skill.py"
)
build_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_skill)

# "~/.tradinglab/venv/bin/tradinglab <args>" or "python -m tradinglab.cli <args>".
_CLI_LINE = re.compile(r"^(?:\S*[/\\])?(?:tradinglab|python3? -m tradinglab\.cli) (.+)$")


def _skill_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILL_DIR.rglob("*.md")))


def _documented_cli_commands() -> list[tuple[str, str]]:
    """Every tradinglab command line inside a fenced bash block of the skill
    and of the listing's pre-upload checklist."""
    found: list[tuple[str, str]] = []
    paths = sorted(SKILL_DIR.rglob("*.md")) + [REPO_ROOT / "skill" / "LISTING.md"]
    for path in paths:
        in_bash = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_bash = stripped == "```bash"
                continue
            match = _CLI_LINE.match(stripped) if in_bash else None
            if match:
                found.append((path.name, match.group(1)))
    return found


def _make_skill(tmp_path: Path, name: str = "demo-skill", frontmatter: str | None = None,
                body: str = "body\n") -> Path:
    skill = tmp_path / name
    skill.mkdir(parents=True)
    if frontmatter is None:
        frontmatter = f"---\nname: {name}\ndescription: Does a thing. Use when asked.\n---\n"
    (skill / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return skill


# ---------------------------------------------------------------------------
# the real skill
# ---------------------------------------------------------------------------

def test_frontmatter_is_valid_and_matches_folder():
    fields = build_skill.read_frontmatter(SKILL_DIR / "SKILL.md")
    assert fields["name"] == SKILL_DIR.name == "trading-agents-desk"
    assert 0 < len(fields["description"]) <= 1024
    assert set(fields) <= {"name", "description", "license"}  # portable across harnesses
    assert (SKILL_DIR / "LICENSE.txt").read_text(encoding="utf-8") == \
        (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")


def test_skill_docs_cover_every_workflow():
    used = {args.split()[0] for _, args in _documented_cli_commands()}
    assert {"snapshot", "desk", "agents", "probe", "baselines", "train-ml", "download",
            "report"} <= used


@pytest.mark.parametrize("source,args", _documented_cli_commands())
def test_documented_command_parses_with_the_real_cli(source, args):
    """A renamed subcommand or flag must fail here, not in a buyer's terminal."""
    argv = shlex.split(args)
    if "--help" in argv or "--version" in argv:
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(argv)
        assert exc.value.code == 0
    else:
        build_parser().parse_args(argv)  # argparse exits non-zero on any mismatch


def test_every_command_in_the_skill_uses_the_dedicated_environment():
    """Agent shells start fresh per command, so a bare ``python`` would run the
    user's project interpreter, where the package is not installed."""
    for path in sorted(SKILL_DIR.rglob("*.md")):
        in_bash = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_bash = stripped == "```bash"
            elif in_bash:
                assert stripped.startswith(("~/.tradinglab/venv/bin/", "python3 -m venv ")), \
                    f"{path.name}: {stripped}"


def test_skill_json_keys_match_what_the_command_emits():
    """SKILL.md tells the agent which keys to branch on. Each must exist in
    what the command really prints."""
    import numpy as np
    import pandas as pd

    from tradinglab.agents.desk import build_desk_snapshot

    idx = pd.bdate_range(end="2026-03-13", periods=320)
    close = 100.0 + np.arange(320) * 0.1
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": 1e6}, index=idx)
    desk = build_desk_snapshot("ABC", live=False, df=df,
                               now=pd.Timestamp("2026-03-13 17:00", tz="America/New_York"))
    emitted = set(snapshot_to_dict(desk))
    documented = set(re.findall(r"`([a-z_]+)`", (SKILL_DIR / "SKILL.md").read_text("utf-8")))
    for key in ("context", "ticker", "company", "instrument_type", "currency", "as_of",
                "fetched_at", "last_close", "partial_session", "stale", "prices_behind",
                "has_fundamentals", "fundamentals_missing", "news_headlines", "notes",
                "disclaimer"):
        assert key in documented and key in emitted, key


def test_install_is_pinned_to_the_release_this_code_ships_as():
    urls = build_skill.pinned_release_urls()
    assert urls and all(url.endswith(f"/archive/refs/tags/v{__version__}.zip") for url in urls)
    assert build_skill.package_version() == __version__
    # The setup check compares versions, so a buyer holding an older install
    # is told to upgrade instead of running old code under new instructions.
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "~/.tradinglab/venv/bin/tradinglab --version" in skill_md
    assert f"`tradinglab {__version__}`" in skill_md
    assert not re.findall(r"`tradinglab (?!%s`)\d+\.\d+\.\d+`" % re.escape(__version__), skill_md)
    listing = (REPO_ROOT / "skill" / "LISTING.md").read_text(encoding="utf-8")
    assert f"archive/refs/tags/v{__version__}.zip" in listing and f"git tag v{__version__}" in listing
    assert "archive/refs/heads/" not in listing


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
    assert "has not been" in text and "backtested" in text
    assert "-0.9%" in text and "+15.7%" in text
    assert "ask before every run" in text


def test_skill_takes_no_personal_inputs():
    """The desk is a simulation from no position. Text that feeds it the
    user's holdings turns its output into a personal recommendation."""
    text = _skill_text().lower()
    for phrase in ("user's position", "they hold", "long-only user", "existing stance",
                   "your position", "your holding"):
        assert phrase not in text, phrase
    assert "no personal inputs" in text


def test_skill_avoids_wording_that_reads_as_hiding_things_from_the_user():
    """Marketplace scanners flag instructions to conceal output or actions."""
    text = _skill_text().lower()
    for phrase in ("do not tell the user", "don't tell the user", "secretly",
                   "without telling", "do not mention", "hide this"):
        assert phrase not in text, phrase


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------

def test_build_writes_the_expected_archive(tmp_path):
    out = build_skill.build(dist_dir=tmp_path)
    with zipfile.ZipFile(out) as archive:
        names = sorted(archive.namelist())
        assert names == [
            "trading-agents-desk/LICENSE.txt",
            "trading-agents-desk/SKILL.md",
            "trading-agents-desk/references/research-harness.md",
            "trading-agents-desk/references/roles.md",
        ]
        assert archive.read("trading-agents-desk/SKILL.md") == \
            (SKILL_DIR / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        assert {info.date_time for info in archive.infolist()} == {(2026, 1, 1, 0, 0, 0)}


def test_build_is_deterministic_across_time_and_line_endings(tmp_path):
    """Same content must give the same bytes: whenever the files were last
    touched, and whether the checkout uses LF or CRLF."""
    unix = _make_skill(tmp_path / "unix", body="line one\nline two\n")
    (unix / "notes.txt").write_text("a\nb\n", encoding="utf-8", newline="")
    windows = _make_skill(tmp_path / "windows", body="")
    (windows / "SKILL.md").write_bytes((unix / "SKILL.md").read_bytes().replace(b"\n", b"\r\n"))
    (windows / "notes.txt").write_bytes(b"a\r\nb\r\n")
    old = time.time() - 86400 * 400
    for path in windows.rglob("*"):
        os.utime(path, (old, old))
    first = build_skill.build(unix, tmp_path / "out-unix")
    second = build_skill.build(windows, tmp_path / "out-windows")
    assert first.read_bytes() == second.read_bytes()


def test_build_leaves_out_hidden_files_caches_and_symlinks(tmp_path):
    skill = _make_skill(tmp_path)
    (skill / ".DS_Store").write_text("junk")
    (skill / ".git").mkdir()
    (skill / ".git" / "config.txt").write_text("junk")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "x.txt").write_text("junk")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("ok\n")
    outside = tmp_path / "secret.txt"
    outside.write_text("must not ship")
    try:
        (skill / "link.txt").symlink_to(outside)
    except OSError:  # symlinks need privileges on some Windows setups
        pass
    with zipfile.ZipFile(build_skill.build(skill, tmp_path / "out")) as archive:
        assert sorted(archive.namelist()) == ["demo-skill/SKILL.md",
                                              "demo-skill/references/guide.md"]


@pytest.mark.parametrize("frontmatter,problem", [
    ("name: demo-skill\ndescription: x\n---\n", "frontmatter opener"),
    ("---\nname: demo-skill\ndescription: x\n", "never closed"),
    ("---\nname: demo-skill\n  nested: x\n---\n", "unsupported frontmatter line"),
    ("---\nname: demo-skill\ndescription:\n---\n", "has no value"),
    ("---\nname: demo-skill\ndescription: Scores: a site\n---\n", "not a plain YAML scalar"),
    ("---\nname: demo-skill\ndescription: 'quoted'\n---\n", "not a plain YAML scalar"),
    ("---\nname: demo-skill\ndescription: a #comment\n---\n", "not a plain YAML scalar"),
    ("---\nname: demo-skill\nname: demo-skill\ndescription: x\n---\n", "duplicate"),
    ("---\nname: Demo Skill\ndescription: x\n---\n", "lowercase letters"),
    ("---\nname: demo--skill\ndescription: x\n---\n", "lowercase letters"),
    ("---\nname: demo-skill\n---\n", "description is required"),
    ("---\nname: demo-skill\ndescription: " + "x" * 1025 + "\n---\n", "the limit is 1024"),
])
def test_validate_rejects_bad_frontmatter(tmp_path, frontmatter, problem):
    skill = _make_skill(tmp_path, frontmatter=frontmatter, body="")
    with pytest.raises(ValueError, match=problem):
        build_skill.validate(skill)


def test_validate_rejects_a_name_longer_than_the_spec_allows(tmp_path):
    name = "a" * 65
    with pytest.raises(ValueError, match="at most 64"):
        build_skill.validate(_make_skill(tmp_path, name=name))


def test_validate_rejects_name_folder_mismatch(tmp_path):
    skill = _make_skill(tmp_path, name="other-name",
                        frontmatter="---\nname: demo-skill\ndescription: x\n---\n")
    with pytest.raises(ValueError, match="must match the folder name"):
        build_skill.validate(skill)


def test_validate_rejects_binary_payloads_and_missing_skill_md(tmp_path):
    skill = _make_skill(tmp_path)
    (skill / "tool.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="only text files"):
        build_skill.validate(skill)
    (tmp_path / "empty-skill").mkdir()
    with pytest.raises(ValueError, match="SKILL.md not found"):
        build_skill.validate(tmp_path / "empty-skill")


URL = "https://github.com/Kantamaniprakash/trading-agents-lab/archive/refs/{ref}.zip"


def test_validate_rejects_an_install_from_a_branch(tmp_path):
    """A branch moves after the listing was reviewed; a buyer would run code
    nobody looked at."""
    skill = _make_skill(tmp_path, body=f"pip install \"{URL.format(ref='heads/main')}\"\n")
    with pytest.raises(ValueError, match="installs from a branch"):
        build_skill.validate(skill, version="0.2.0")


def test_validate_rejects_a_pin_that_disagrees_with_the_package_version(tmp_path):
    skill = _make_skill(tmp_path, body=f"pip install \"{URL.format(ref='tags/v0.1.0')}\"\n")
    with pytest.raises(ValueError, match="installs release v0.1.0 but pyproject.toml is at 0.2.0"):
        build_skill.validate(skill, version="0.2.0")
    assert build_skill.validate(skill, version="0.1.0")
    with pytest.raises(ValueError, match="names no pinned release"):
        build_skill.validate(_make_skill(tmp_path / "b"), version="0.2.0")


def test_main_checks_the_release_before_it_writes_anything(monkeypatch, tmp_path, capsys):
    """Uploading before the tag exists would break every buyer's install."""
    monkeypatch.setattr(build_skill, "DIST_DIR", tmp_path)
    monkeypatch.setattr(build_skill, "release_is_downloadable", lambda url: (False, "HTTP 404"))
    with pytest.raises(SystemExit) as exc:
        build_skill.main([])
    assert exc.value.code == 1 and not list(tmp_path.glob("*.zip"))
    assert "cannot be downloaded (HTTP 404)" in capsys.readouterr().err

    monkeypatch.setattr(build_skill, "release_is_downloadable", lambda url: (True, "HTTP 200"))
    build_skill.main([])
    assert (tmp_path / "trading-agents-desk.zip").is_file()


def test_main_skip_remote_check_builds_offline_and_warns(monkeypatch, tmp_path, capsys):
    def no_network(url):
        raise AssertionError("--skip-remote-check must not touch the network")

    monkeypatch.setattr(build_skill, "DIST_DIR", tmp_path)
    monkeypatch.setattr(build_skill, "release_is_downloadable", no_network)
    build_skill.main(["--skip-remote-check"])
    captured = capsys.readouterr()
    assert (tmp_path / "trading-agents-desk.zip").is_file()
    assert "was NOT checked" in captured.err and "Do not upload" in captured.err


def test_release_is_downloadable_reports_what_the_server_said(monkeypatch):
    url = "https://example.invalid/archive/refs/tags/v9.9.9.zip"
    seen: list = []

    class Response:
        def __init__(self, status):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def answer(status):
        def urlopen(request, timeout):
            seen.append((request.get_method(), request.full_url))
            return Response(status)
        return urlopen

    def fail(error):
        def urlopen(request, timeout):
            raise error
        return urlopen

    monkeypatch.setattr(build_skill.urllib.request, "urlopen", answer(200))
    assert build_skill.release_is_downloadable(url) == (True, "HTTP 200")
    assert seen == [("HEAD", url)]
    monkeypatch.setattr(build_skill.urllib.request, "urlopen", answer(304))
    assert build_skill.release_is_downloadable(url) == (False, "HTTP 304")
    not_found = build_skill.urllib.error.HTTPError(url, 404, "Not Found", None, None)
    monkeypatch.setattr(build_skill.urllib.request, "urlopen", fail(not_found))
    assert build_skill.release_is_downloadable(url) == (False, "HTTP 404")
    monkeypatch.setattr(build_skill.urllib.request, "urlopen",
                        fail(build_skill.urllib.error.URLError("no route to host")))
    assert build_skill.release_is_downloadable(url) == (False, "no route to host")


def test_the_real_skill_is_validated_against_pyproject_without_being_told(monkeypatch):
    """``python skill/build_skill.py`` passes no version: validate() must look
    it up itself, or a stale pin ships."""
    monkeypatch.setattr(build_skill, "package_version", lambda: "9.9.9")
    with pytest.raises(ValueError, match="pin v9.9.9"):
        build_skill.validate()


def test_a_branch_install_hidden_in_a_reference_file_is_rejected(tmp_path):
    skill = _make_skill(tmp_path, body="pip install " + URL.format(ref="tags/v0.2.0"))
    (skill / "references").mkdir()
    (skill / "references" / "more.md").write_text(
        "pip install " + URL.format(ref="heads/main"), encoding="utf-8")
    with pytest.raises(ValueError, match="installs from a branch"):
        build_skill.validate(skill, version="0.2.0")


def test_archive_member_order_does_not_depend_on_the_platform(tmp_path):
    """Windows and POSIX sort path separators and letter case differently.
    Members are ordered by their archive name, so the bytes match anywhere."""
    skill = _make_skill(tmp_path)
    (skill / "Zeta.txt").write_text("z\n")
    (skill / "alpha.txt").write_text("a\n")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_text("g\n")
    (skill / "references-extra.md").write_text("e\n")
    with zipfile.ZipFile(build_skill.build(skill, tmp_path / "out")) as archive:
        names = [name.split("/", 1)[1] for name in archive.namelist()]
    assert names == sorted(names) == ["SKILL.md", "Zeta.txt", "alpha.txt",
                                      "references-extra.md", "references/guide.md"]


@pytest.mark.parametrize("value", ["true", "No", "null", "~", "42", "1.5", "1e3", "2026-01-01",
                                   "0b101", "5.", "1:30", "+0x1F", "=", "<<", "1_", ".1_9"])
def test_frontmatter_values_yaml_would_not_read_as_text_are_rejected(tmp_path, value):
    skill = _make_skill(tmp_path, frontmatter=f"---\nname: demo-skill\ndescription: {value}\n---\n",
                        body="")
    with pytest.raises(ValueError, match="would not read the value"):
        build_skill.validate(skill)


@pytest.mark.parametrize("value", ["0.2.0", "3 things", "1:30 pm", "10:30 start", "v0b1", "0xZZ",
                                   "Runs a desk. Use when asked."])
def test_frontmatter_values_that_are_text_are_accepted(tmp_path, value):
    skill = _make_skill(tmp_path, frontmatter=f"---\nname: demo-skill\ndescription: {value}\n---\n",
                        body="")
    assert build_skill.validate(skill)


@pytest.mark.parametrize("frontmatter", [
    "---\nname:demo-skill\ndescription: x\n---\n",            # no space after the colon
    "---\nname: demo-skill\ndescription: a\tb\n---\n",        # a tab inside a value
])
def test_frontmatter_lines_yaml_would_read_differently_are_rejected(tmp_path, frontmatter):
    skill = _make_skill(tmp_path, frontmatter=frontmatter, body="")
    with pytest.raises(ValueError, match="unsupported frontmatter line"):
        build_skill.validate(skill)
