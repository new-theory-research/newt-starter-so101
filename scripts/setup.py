#!/usr/bin/env python3
"""
setup.py — agent-runnable setup for newt-starter-so101.

Goal: drive the rig from `git clone` to "ready to `uv run python3 run.py`"
without hand-editing the config. Does what a human would do during the manual
setup in the README, auto-detecting the values it safely can.

Layers:
  1) Filesystem boilerplate: ~/.config/nt/nt.toml from conf/nt.toml.example.
  2) Serial port: glob /dev/ttyACM* (Linux) + /dev/tty.usbmodem* (macOS). If
     exactly one candidate, autofill `port`; if zero or many, leave the field
     and report what to do.
  3) Cameras: reported as needs-attention with instructions. We deliberately do
     NOT auto-open camera devices — that triggers OS permission prompts and is
     intrusive during setup. The `top`/`side` indices are a short manual edit.
  4) Report: human prose by default, JSON with --json.

No extrinsics step: the SO-101 has no depth cameras, so there is no
camera-pose machinery to fill (and none of the Trossen identity-fallback
footgun). This is the whole difference from the Trossen starter's setup.

Flags:
  --force            overwrite ~/.config/nt/nt.toml without prompting
  --json             structured JSON output (for agent consumption)
  --report-only      dry-run; report planned changes without writing
  --non-interactive  fail loud on any step that needs a human

Failure modes are loud: every step that can't complete prints exactly what's
missing and how to provide it.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "conf" / "nt.toml.example"
TARGET_DIR = Path.home() / ".config" / "nt"
TARGET_FILE = TARGET_DIR / "nt.toml"

# Serial-port glob patterns, in detection order. SO-101 over USB shows up as a
# CDC-ACM device (Linux) or a usbmodem device (macOS).
PORT_GLOBS = ("/dev/ttyACM*", "/dev/tty.usbmodem*")


@dataclass
class StepResult:
    name: str
    status: str  # "ok" | "skipped" | "needs-attention" | "error"
    detail: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Report:
    config_path: str = str(TARGET_FILE)
    steps: list[StepResult] = field(default_factory=list)
    needs_attention: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    wrote_file: bool = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "config_path": self.config_path,
                "wrote_file": self.wrote_file,
                "steps": [asdict(s) for s in self.steps],
                "needs_attention": self.needs_attention,
                "next_steps": self.next_steps,
            },
            indent=2,
        )


# ---------------------------------------------------------------------------
# Layer 2 — serial port detection
# ---------------------------------------------------------------------------


def detect_serial_port() -> tuple[str | None, str]:
    """Return (port, detail). port is None if not exactly one candidate found."""
    found: list[str] = []
    for pattern in PORT_GLOBS:
        found.extend(sorted(glob.glob(pattern)))
    found = sorted(set(found))
    if len(found) == 1:
        return found[0], f"detected one serial device: {found[0]}"
    if len(found) > 1:
        return None, (
            f"found multiple serial devices ({', '.join(found)}); cannot tell "
            "which is the SO-101. Unplug the arm, re-run, note which disappears, "
            "then set `port` by hand."
        )
    return None, (
        "no serial device matched /dev/ttyACM* or /dev/tty.usbmodem*. Is the "
        "SO-101 powered and plugged in over USB? Set `port` by hand once you "
        "know it (ls /dev/ttyACM* on Linux, ls /dev/tty.usbmodem* on macOS)."
    )


# ---------------------------------------------------------------------------
# Layer 1 — render nt.toml from template + detected values
# ---------------------------------------------------------------------------


def render_nt_toml(port: str | None) -> str:
    """Build the nt.toml content from template + detected serial port."""
    text = TEMPLATE.read_text()
    if port:
        # Replace the first `port = "..."` value (the arm's). Camera entries use
        # `index_or_path`, so this only touches the arm line.
        text = re.sub(
            r'(port\s*=\s*)"[^"]*"',
            rf'\1"{port}"',
            text,
            count=1,
        )
    return text


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def confirm_overwrite(target: Path) -> bool:
    try:
        answer = input(
            f"\n{target} already exists. Overwrite? [y/N]: "
        ).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def run(args: argparse.Namespace) -> Report:
    report = Report()

    # Step 1 — filesystem
    if not TEMPLATE.exists():
        report.steps.append(
            StepResult(
                "template",
                "error",
                f"template not found at {TEMPLATE}; cannot proceed",
            )
        )
        report.needs_attention.append(
            f"Missing template {TEMPLATE} — verify the starter repo is intact."
        )
        return report

    target_exists = TARGET_FILE.exists()
    if target_exists and not args.force and not args.report_only:
        if args.non_interactive:
            report.steps.append(
                StepResult(
                    "config",
                    "error",
                    f"{TARGET_FILE} exists; --non-interactive will not overwrite. "
                    "Re-run with --force or remove the file.",
                )
            )
            report.needs_attention.append(
                f"{TARGET_FILE} already exists; pass --force to overwrite."
            )
            return report
        if not confirm_overwrite(TARGET_FILE):
            report.steps.append(
                StepResult(
                    "config",
                    "skipped",
                    f"{TARGET_FILE} kept as-is at user request",
                )
            )
            report.next_steps.append(
                "Re-run with --force if you actually want to regenerate."
            )
            return report

    # Step 2 — serial port
    port, port_detail = detect_serial_port()
    if port:
        report.steps.append(
            StepResult("serial-port", "ok", port_detail, {"port": port})
        )
    else:
        report.steps.append(
            StepResult("serial-port", "needs-attention", port_detail)
        )
        report.needs_attention.append(
            f"port — {port_detail.rstrip('.')}. "
            f"Edit {TARGET_FILE} after this script finishes."
        )

    # Step 3 — cameras (no auto-open; manual mapping)
    report.steps.append(
        StepResult(
            "cameras",
            "needs-attention",
            "top/side camera indices are a manual edit — we do not auto-open "
            "camera devices during setup (it triggers OS permission prompts)",
        )
    )
    report.needs_attention.append(
        f"camera indices — set `index_or_path` for `top` and `side` in "
        f"{TARGET_FILE}. Usually 1 and 2 (0 is often the built-in camera). "
        "See README §Cameras."
    )

    # Render + write
    rendered = render_nt_toml(port)

    if args.report_only:
        report.steps.append(
            StepResult("write", "skipped", "--report-only set; no write")
        )
        report.next_steps.append(
            "Re-run without --report-only to actually write the config."
        )
        report.wrote_file = False
    else:
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        TARGET_FILE.write_text(rendered)
        report.steps.append(StepResult("write", "ok", f"wrote {TARGET_FILE}"))
        report.wrote_file = True
        if report.needs_attention:
            report.next_steps.append(
                f"Resolve the items above (edit {TARGET_FILE}), calibrate the arm "
                "(README §Calibration), then: uv run python3 run.py --check"
            )
        else:
            report.next_steps.append(
                "Calibrate the arm (README §Calibration), then: "
                "uv run python3 run.py --check"
            )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="setup",
        description=(
            "Agent-runnable setup for newt-starter-so101. Auto-detects the SO-101 "
            "serial port and writes ~/.config/nt/nt.toml."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite ~/.config/nt/nt.toml without prompting",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a structured JSON report (for agent consumption)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="dry-run; show what would be written without touching the filesystem",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="fail loud on any step that needs a human prompt",
    )
    args = parser.parse_args()

    report = run(args)

    if args.json:
        print(report.to_json())
    else:
        print(f"\nnewt-starter setup → {report.config_path}")
        for s in report.steps:
            tag = {
                "ok": "OK",
                "skipped": "--",
                "needs-attention": "!!",
                "error": "XX",
            }.get(s.status, "??")
            print(f"  [{tag}] {s.name}: {s.detail}")
        if report.needs_attention:
            print("\nNeeds attention:")
            for item in report.needs_attention:
                print(f"  - {item}")
        if report.next_steps:
            print("\nNext:")
            for item in report.next_steps:
                print(f"  - {item}")
        print()

    any_error = any(s.status == "error" for s in report.steps)
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
