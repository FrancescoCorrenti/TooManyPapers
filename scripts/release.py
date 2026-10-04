#!/usr/bin/env python3
"""Cut a release of the Too Many Papers plugin.

    python scripts/release.py 0.3.0            # do it
    python scripts/release.py 0.3.0 --dry-run  # show what would change

Release model: `main` is development and never reaches users directly. The
marketplace entry pins the plugin to the tag of the latest release (ref + sha),
and Claude Code only offers an update when plugin.json's `version` changes.

Steps, stopping at the first failure:
  1. checks: clean tree on main, in sync with origin, tag free, semver newer,
     a non-empty "## Unreleased" section in CHANGELOG.md
  2. version written to plugin.json, pyproject.toml (+ uv.lock), SKILL.md
  3. "## Unreleased" becomes "## X.Y.Z — date" (a fresh empty Unreleased on top)
  4. targeted tests: MCP smoke test and test_project_papers
  5. release commit + annotated tag vX.Y.Z, pushed
  6. marketplace.json pinned to the tag (ref + sha), committed, pushed
  7. GitHub release with that changelog section as notes
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "too-many-papers-plugin"
SERVER = PLUGIN / "server"
PLUGIN_JSON = PLUGIN / ".claude-plugin" / "plugin.json"
PYPROJECT = SERVER / "pyproject.toml"
SKILL = PLUGIN / "skills" / "too-many-papers" / "SKILL.md"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://github.com/FrancescoCorrenti/TooManyPapers.git"
TRAILER = ("\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_013cZKtnhQU7muhCaq5v7au7\n")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def run(*cmd, cwd=ROOT, capture=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True, encoding="utf-8")
    if r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "") if capture else ""
        sys.exit(f"FAILED: {' '.join(cmd)}\n{out.strip()[-2000:]}")
    return (r.stdout or "").strip() if capture else ""


def current_version() -> str:
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]


def unreleased_section(text: str) -> tuple[int, int, str]:
    m = re.search(r"^## Unreleased[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        sys.exit("CHANGELOG.md has no '## Unreleased' section.")
    return m.start(), m.end(), m.group(1).strip()


def replace_once(path: Path, pattern: str, repl: str, dry: bool):
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"Version field not found in {path.relative_to(ROOT)}")
    print(f"  {path.relative_to(ROOT)}")
    if not dry:
        path.write_bytes(new.encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    v, dry, tag = a.version, a.dry_run, f"v{a.version}"

    # 1. checks
    if not SEMVER.match(v):
        sys.exit(f"'{v}' is not X.Y.Z")
    old = current_version()
    if tuple(map(int, v.split("."))) <= tuple(map(int, old.split("."))):
        sys.exit(f"{v} is not newer than the current {old}")
    if run("git", "branch", "--show-current") != "main":
        sys.exit("Release from main only.")
    if run("git", "status", "--porcelain"):
        sys.exit("Working tree not clean: commit or stash first.")
    run("git", "fetch", "-q", "origin")
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", "origin/main"):
        sys.exit("main differs from origin/main: pull/push first.")
    if run("git", "tag", "-l", tag):
        sys.exit(f"Tag {tag} already exists.")
    changelog = CHANGELOG.read_text(encoding="utf-8")
    start, end, notes = unreleased_section(changelog)
    if not notes:
        sys.exit("The Unreleased section of CHANGELOG.md is empty: write the notes first.")
    print(f"Release {old} -> {v}\n\nNotes:\n{notes}\n\nFiles:")

    # 2. versions
    replace_once(PLUGIN_JSON, r'("version":\s*")[^"]+(")', rf"\g<1>{v}\g<2>", dry)
    replace_once(PYPROJECT, r'^(version\s*=\s*")[^"]+(")', rf"\g<1>{v}\g<2>", dry)
    replace_once(SKILL, r'^(  version:\s*")[^"]+(")', rf"\g<1>{v}\g<2>", dry)

    # 3. changelog
    print(f"  {CHANGELOG.relative_to(ROOT)}")
    if dry:
        print("\nDry run: nothing written, nothing pushed.")
        return
    dated = f"## Unreleased\n\n## {v} — {date.today().isoformat()}\n\n{notes}\n\n"
    CHANGELOG.write_bytes((changelog[:start] + dated + changelog[end:]).encode("utf-8"))
    run("uv", "lock", "-q", cwd=SERVER)

    # 4. tests
    print("\nTests: mcp_smoke.py, test_project_papers.py")
    run("uv", "run", "python", "tests/mcp_smoke.py", cwd=SERVER)
    run("uv", "run", "--with", "pytest", "python", "-m", "pytest", "-q",
        "tests/test_project_papers.py", cwd=SERVER)

    # 5. release commit + tag
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", f"Release {tag}" + TRAILER)
    run("git", "tag", "-a", tag, "-m", f"Too Many Papers {v}\n\n{notes}")
    sha = run("git", "rev-parse", f"{tag}^{{commit}}")
    run("git", "push", "-q", "origin", "main", tag)

    # 6. pin the marketplace to the tag
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == "too-many-papers")
    entry["source"] = {"source": "git-subdir", "url": REPO_URL,
                       "path": "too-many-papers-plugin", "ref": tag, "sha": sha}
    MARKETPLACE.write_bytes((json.dumps(market, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    run("git", "add", str(MARKETPLACE))
    run("git", "commit", "-q", "-m", f"Point the marketplace at {tag}" + TRAILER)
    run("git", "push", "-q", "origin", "main")

    # 7. GitHub release
    notes_file = ROOT / ".git" / "RELEASE_NOTES.md"
    notes_file.write_text(notes + "\n", encoding="utf-8")
    url = run("gh", "release", "create", tag, "--title", f"Too Many Papers {v}",
              "--notes-file", str(notes_file), "--verify-tag")
    notes_file.unlink()
    print(f"\nReleased {tag} ({sha[:12]}): {url}")


if __name__ == "__main__":
    main()
