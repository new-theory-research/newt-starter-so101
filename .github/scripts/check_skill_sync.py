#!/usr/bin/env python3
"""Verify the starter's SKILL.md is in sync with the canonical in the installed newt package.

Strips hardware-specific blocks (<!-- hardware-specific-start/end -->) and the
canonical-source comment line before comparing, so intentional per-starter
substitutions don't cause false failures.

Exit 0 = in sync. Exit 1 = diverged (unified diff on stderr).
"""
import sys
import importlib.resources
import difflib


def normalize(text: str) -> str:
    """Strip sync markers and collapse blank lines so the structural comparison is clean."""
    out = []
    in_hw_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "<!-- hardware-specific-start -->":
            in_hw_block = True
            continue
        if stripped == "<!-- hardware-specific-end -->":
            in_hw_block = False
            continue
        if in_hw_block:
            continue
        if stripped.startswith("<!-- canonical source:") and stripped.endswith("-->"):
            continue
        out.append(line)

    # Collapse consecutive blank lines to a single blank (avoids marker-removal
    # introducing spurious blank-line differences).
    collapsed: list[str] = []
    prev_blank = False
    for line in out:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    return "\n".join(collapsed)


canonical_ref = importlib.resources.files("newt") / "skills" / "newt-onboarding" / "SKILL.md"
try:
    canonical_text = canonical_ref.read_text(encoding="utf-8")
except Exception as exc:
    print(f"error: could not read canonical skill from installed newt package: {exc}", file=sys.stderr)
    sys.exit(1)

starter_path = ".claude/skills/newt-onboarding/SKILL.md"
try:
    with open(starter_path, encoding="utf-8") as fh:
        starter_text = fh.read()
except FileNotFoundError:
    print(f"error: starter skill not found at {starter_path}", file=sys.stderr)
    sys.exit(1)

norm_canonical = normalize(canonical_text)
norm_starter = normalize(starter_text)

if norm_canonical == norm_starter:
    print("Skill sync OK — starter matches canonical (hardware-specific section excluded)")

    # Dead docs domains must never appear anywhere in the starter skill,
    # including hardware-specific blocks. The live domain is newtheory-docs.vercel.app.
    forbidden = ("docs.newtheory.ai", "nt-docs-eight.vercel.app")
    hits = [(n, line) for n, line in enumerate(starter_text.splitlines(), start=1)
            if any(domain in line for domain in forbidden)]
    if hits:
        for n, line in hits:
            print(f"{starter_path}:{n}: {line.strip()}", file=sys.stderr)
        print("error: dead docs domain in SKILL.md — the live domain is newtheory-docs.vercel.app", file=sys.stderr)
        sys.exit(1)
    print("Domain check OK — no dead docs domains")
    sys.exit(0)

diff = list(difflib.unified_diff(
    norm_canonical.splitlines(keepends=True),
    norm_starter.splitlines(keepends=True),
    fromfile="canonical (newt-python/src/newt/skills/newt-onboarding/SKILL.md)",
    tofile="starter (.claude/skills/newt-onboarding/SKILL.md)",
))
print("SKILL.md has diverged from the canonical in newt-python.", file=sys.stderr)
print("To fix: sync the starter copy from newt-python/src/newt/skills/newt-onboarding/SKILL.md,", file=sys.stderr)
print("preserving the canonical-source header and hardware-specific-start/end markers.", file=sys.stderr)
print("", file=sys.stderr)
print("".join(diff), file=sys.stderr)
sys.exit(1)
