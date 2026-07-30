"""
SO-101 hardware demo — newt-starter-so101.

Plugs an SO101 embodiment into newt.Robot(model="so101") and runs one
closed-loop trial on a physical SO-101 follower arm with two USB cameras.

Usage:
    uv sync --extra hardware
    uv run python3 run.py setup            # writes ~/.config/nt/nt.toml
    export NT_API_KEY=<key>                # or: newt login
    lerobot-calibrate --robot.type=so101_follower \\
        --robot.port=<port> --robot.id=<arm-id>   # one-time, see README §Calibration
    uv run python3 run.py --task "Pick up the cube and place it in the bin."

No-hardware verification (works on a stock Mac, no cameras/arm):
    uv run python3 run.py --check

Hardware requirements:
    - SO-101 follower arm on USB (serial port: /dev/ttyACM* Linux, /dev/tty.usbmodem* macOS)
    - Two USB webcams (top + side views)
    - SO-101 calibrated once with `lerobot-calibrate` (NOT factory-calibrated; see README)
    - ~/.config/nt/nt.toml populated: arm serial port + top/side camera indices

Full runbook: README.md §Troubleshooting

The SO-101 is a pure joint-space arm — six motor positions in, six out, plus two
RGB frames. run.py stays thin and embodiment-agnostic; all the SO-101-specific
wiring lives in embodiment.py. (Cf. the Trossen starter: same run.py shape,
different embodiment.)
"""
from __future__ import annotations

import sys
import os
import time
from pathlib import Path

import newt
from newt._credentials import read_api_key

from embodiment import (
    SO101,
    _EmergencyStop,
    _load_site_config,
    _load_arm_port,
    _load_cameras,
    _import_hardware_deps,
    _LEROBOT_IMPORT_ERR,
    _start_keyboard_listener,
    _restore_terminal,
    _DEFAULT_SITE_CONFIG_PATH,
    _REST_POSE,
    _JOINT_ORDER,
    _CAMERA_KEYS,
)

import embodiment as _embodiment_mod

# Model tag in the live registry. Source: brief-251 closeout / brief-258c —
# tags ["so101", "so-101"], UID ft_6341c5_d13da9. We pass the embodiment-led tag;
# the SDK resolves it to the fine-tune. The `nt` library picks the model — the
# tag is the stable handle, not a user-facing model choice.
MODEL = "so101"

# Wall-clock cap for one trial. Matches the newt.Robot.run default; a single
# trial, no N>1 loop. Defined here (not in embodiment.py) — it's a run-policy
# knob, not a property of the rig.
MAX_DURATION_S: float = 30.0

# SO-101 / MolmoAct2 is OPEN-VOCABULARY (language-conditioned): --task takes any
# natural-language instruction. There is NO fixed task-key list (unlike the
# Trossen starter, which inlined trained keys). The live registry is the
# source of truth for what the fine-tune was trained on; --check performs the
# registry round-trip. We do not invent trained-task keys here. The prompts below
# are ILLUSTRATIVE examples to adapt to your scene — not a claimed trained set.
_DEFAULT_TASK = "Pick up the object and place it in the container."
_EXAMPLE_PROMPTS = [
    "Pick up the object and place it in the container.",
    "Put the cube into the bin.",
    "Move the item from the table into the box.",
]


# ---------------------------------------------------------------------------
# Shape sanity check — verifiable without hardware.
# Run: python3 -c "import run; run._check_obs_shapes(run._build_mock_obs())"
# ---------------------------------------------------------------------------

import numpy as np


def _build_mock_obs() -> dict:
    """Mock obs dict with correct SO-101 shapes for testing _check_obs_shapes.

    Joint-space, RGB-only: state (6,), two square 378x378 CHW frames, and NO
    depth/intrinsics/extrinsics — the SO-101 has none.
    """
    return {
        "state": np.zeros(6, dtype=np.float32),
        "images": {cam: np.zeros((3, 378, 378), dtype=np.uint8) for cam in _CAMERA_KEYS},
    }


def _check_obs_shapes(obs: dict) -> None:
    """Assert obs dict returned by read_state() has correct SO-101 shapes.
    Verifiable without hardware: call with _build_mock_obs().
    """
    assert "state" in obs, "obs missing 'state'"
    assert "images" in obs, "obs missing 'images'"
    # The SO-101 carries no depth/intrinsics/extrinsics. Assert they are ABSENT —
    # if a future edit re-adds the Trossen cartesian machinery, this fails loud.
    assert "depth_maps" not in obs, "SO-101 obs must not carry depth_maps"
    assert "intrinsics" not in obs, "SO-101 obs must not carry intrinsics"
    assert "extrinsics" not in obs, "SO-101 obs must not carry extrinsics"

    state = obs["state"]
    assert hasattr(state, "shape") and state.shape == (6,), (
        f"state must be (6,) float32, got shape={getattr(state, 'shape', '?')}"
    )
    assert state.dtype == np.float32, f"state dtype must be float32, got {state.dtype}"

    for cam in _CAMERA_KEYS:
        img = obs["images"][cam]
        assert img.ndim == 3 and img.shape == (3, 378, 378), (
            f"images[{cam}] must be (3, 378, 378) CHW, got {img.shape}"
        )
        assert img.dtype == np.uint8, f"images[{cam}] must be uint8, got {img.dtype}"


# Verify mock obs shapes at import time (no hardware required).
_check_obs_shapes(_build_mock_obs())


# ---------------------------------------------------------------------------
# setup subcommand — delegates to scripts/setup.py.
#
# run.py stays thin: the setup machinery (serial-port detection, camera mapping,
# config write) lives in scripts/setup.py. `run.py setup ...` forwards to it so
# the inference path here is never weighed down by setup concerns.
# ---------------------------------------------------------------------------


def _delegate_setup(argv: list[str]) -> int:
    """Run scripts/setup.py with the remaining argv. Returns its exit code."""
    import subprocess

    setup_py = Path(__file__).resolve().parent / "scripts" / "setup.py"
    if not setup_py.exists():
        print(
            f"ERROR: scripts/setup.py not found at {setup_py}.\n"
            "The starter repo looks incomplete — re-clone it.",
            file=sys.stderr,
        )
        return 1
    proc = subprocess.run([sys.executable, str(setup_py), *argv])
    return proc.returncode


# ---------------------------------------------------------------------------
# --reset
# ---------------------------------------------------------------------------


def _reset_arm(site_config_path: Path | str | None = None, arm_id: str | None = None) -> None:
    """Send the arm to the rest pose and exit (no trial).

    Used after a trial / crash to bring the arm back to a known pose. Unlike the
    Trossen starter — which bypassed lerobot with a raw driver — the SO-101 has
    no bypass: we construct the same SO101 embodiment run.py uses and drive the
    rest move through its public execute() (one-row chunk). SO101.connect() does
    NOT move the arm, so constructing the rig for a reset is safe.

    _REST_POSE is a SANE DEFAULT, not a verified-safe pose — see embodiment.py
    and confirm on the physical arm during the smoke (open item T-D).

    arm_id: which [[robot_config.arms]] entry to use (by `id`). Required when the
    config has more than one arm.
    """
    print("[reset] connecting to SO-101…", flush=True)
    rig = SO101.from_config(site_config_path=site_config_path, arm_id=arm_id)
    try:
        print("[reset] moving to rest pose…", flush=True)
        rest_row = np.array(
            [[_REST_POSE[m] for m in _JOINT_ORDER]], dtype=np.float32
        )
        rig.execute(rest_row)  # first-chunk path: send + settle
        print("[reset] arm at rest.", flush=True)
    finally:
        rig.teardown()
    print("[reset] done.", flush=True)


# ---------------------------------------------------------------------------
# --check (no-hardware verification)
# ---------------------------------------------------------------------------


def _check_fail(stage: str, error: object, hint: str) -> None:
    """Print the failing --check stage + actual error + one-line fix hint; exit 1."""
    print(f"check failed at stage: {stage}", file=sys.stderr)
    print(f"  error: {error}", file=sys.stderr)
    print(f"  fix:   {hint}", file=sys.stderr)
    sys.exit(1)


def _run_check(site_config_path: Path | str | None = None, arm_id: str | None = None) -> None:
    """No-hardware verification: config → arm/camera selection → (if key) registry contract.

    Delivers shape validation on a stock Mac with no cameras/arm. Never imports
    lerobot or the servo SDK (lerobot's camera discovery dies on a keyless Mac).

    Two tiers:
      - Always: load the site config, name the arm and cameras that would be used.
        This is the floor — it passes with no API key and no network.
      - When NT_API_KEY is set: also fetch /v1/models and confirm the live so101
        contract arrived (state_shape, cameras). This proves the key + registry
        round-trip without touching hardware.

    No fixture inference: there is no SO-101-shaped fixture bundled yet (the
    shipped fixtures are 8-DOF Trossen episodes). We do NOT fabricate a synthetic
    observation to fake an inference — a zeroed obs is exactly the kind of
    invented input Rule 10 forbids. The real first-inference proof is the smoke.

    Exits 0 on success; on failure names the stage, shows the error, gives a fix.
    """
    # (a) Site config — same file the hardware path reads.
    config_path = Path(site_config_path or _DEFAULT_SITE_CONFIG_PATH).expanduser().resolve()
    try:
        raw = _load_site_config(site_config_path)
    except Exception as exc:
        _check_fail(
            "config",
            exc,
            "run `uv run python3 run.py setup` to create ~/.config/nt/nt.toml from the template",
        )
    print(f"[check] config: loaded {config_path}", flush=True)

    # (a.1) Arm selection — verify and report which arm would be used.
    try:
        selected_id, selected_port = _load_arm_port(raw, arm_id)
    except Exception as exc:
        _check_fail(
            "arm-selection",
            exc,
            "run with --arm <id>; see nt.toml [[robot_config.arms]] for configured arm ids",
        )
    print(
        f"[check] arm: {selected_id} (port {selected_port}) — this arm would be used",
        flush=True,
    )

    # (a.2) Camera selection — confirm both required cameras are configured.
    cameras = _load_cameras(raw)
    missing = [cam for cam in _CAMERA_KEYS if cam not in cameras]
    if missing:
        _check_fail(
            "camera-selection",
            f"nt.toml is missing camera(s): {', '.join(missing)}",
            f"add a [[camera_config.cameras]] entry with id = '{missing[0]}' "
            "and its index_or_path (e.g. 0)",
        )
    cam_summary = ", ".join(
        f"{cam} (index {cameras[cam]['index_or_path']})" for cam in _CAMERA_KEYS
    )
    print(f"[check] cameras: {cam_summary} — these cameras would be used", flush=True)

    # (b) API key — env-first (NT_API_KEY), then the stored credentials file.
    # No key is NOT a failure: the config/arm/camera floor already passed. We
    # stop here loudly (Rule 10 — no silent success), telling the developer what
    # was NOT verified and how to verify it.
    api_key = os.environ.get("NT_API_KEY") or read_api_key()
    if not api_key:
        print(
            "[check] api key: not set — skipping the live registry round-trip.\n"
            "        Config, arm, and cameras are valid. To also verify the API\n"
            "        contract, set NT_API_KEY (or run `newt login`) and re-run --check.",
            flush=True,
        )
        print(
            "check passed (config-only) — your nt.toml is valid. "
            "Set NT_API_KEY to verify the API, connect the arm for motion.",
            flush=True,
        )
        sys.exit(0)
    print("[check] api key: found", flush=True)

    # (c) Registry round-trip — Robot construction with NO embodiment fetches
    # /v1/models and resolves the so101 contract the hardware path uses.
    try:
        robot = newt.Robot(api_key=api_key, model=MODEL)
    except newt.AuthError as exc:
        _check_fail(
            "registry",
            exc,
            "verify NT_API_KEY is a valid nt_ key; rotate it in the NT console if needed",
        )
    except Exception as exc:
        _check_fail(
            "registry",
            exc,
            "check your network connection, then retry once",
        )

    # Surface the resolved so101 contract so the developer sees it arrived.
    # (_registry is the stored /v1/models response; no public accessor yet.)
    entry = next(
        (
            e
            for e in robot._registry
            if e.get("uid") == MODEL
            or MODEL in (e.get("tags") or [])
            or "so-101" in (e.get("tags") or [])
        ),
        None,
    )
    if entry is None:
        _check_fail(
            "contract",
            f"model '{MODEL}' not found in the registry response",
            "the registry answered but without this model — report this to your New Theory contact",
        )
    contract = entry.get("contract") or {}
    state_shape = contract.get("state_shape")
    cameras_contract = contract.get("cameras")
    if not state_shape:
        _check_fail(
            "contract",
            f"registry entry '{entry.get('uid', MODEL)}' carries no state_shape contract",
            "the registry answered but the contract is incomplete — report this to your New Theory contact",
        )
    print(
        f"[check] registry: resolved model {entry.get('uid', MODEL)} · "
        f"contract arrived · state_shape {state_shape} · cameras {cameras_contract}",
        flush=True,
    )

    print(
        "check passed — your install can resolve the so101 contract. "
        "Connect the arm and run a calibration (see README §Calibration) for motion.",
        flush=True,
    )
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run one closed-loop trial on hardware.

    Constructs an SO101 embodiment from config and wires it into
    newt.Robot(embodiment=rig, model="so101"), then calls
    robot.run(task, max_duration=MAX_DURATION_S). Single trial; no N>1 loop.

    Flags:
        --check        No-hardware verification: config → arm/camera selection →
                       (if NT_API_KEY set) live registry contract. Stock Mac, no arm.
        --reset        Move the arm to rest pose and exit (no trial).
        --task TEXT    Natural-language instruction (open-vocabulary model).
                       Omit to use an example prompt. Pass --list-tasks for guidance.
        --list-tasks   Explain the open-vocabulary interface + example prompts, exit 0.
        --arm ID       Select which [[robot_config.arms]] entry to use.
        --site-config  Path to the site config TOML (default ~/.config/nt/nt.toml).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="SO-101 (MolmoAct2) demo. `run.py setup` writes the site config."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify the install without hardware: config, arm + camera selection, "
            "and (if NT_API_KEY is set) the live registry contract. No cameras, no arm."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Move the arm to rest pose and exit. No trial, no inference.",
    )
    parser.add_argument(
        "--arm",
        metavar="ID",
        default=None,
        help=(
            "Select which [[robot_config.arms]] entry to use by its `id` field. "
            "Required when nt.toml has more than one arm. Example: --arm so101_a"
        ),
    )
    parser.add_argument(
        "--site-config",
        metavar="PATH",
        default=None,
        help="Path to the site config TOML (default: ~/.config/nt/nt.toml).",
    )
    parser.add_argument(
        "--task",
        metavar="TEXT",
        default=None,
        help=(
            "Natural-language instruction for the open-vocabulary policy. "
            "Omit to use an example prompt; pass --list-tasks for guidance."
        ),
    )
    parser.add_argument(
        "--list-tasks",
        action="store_true",
        help="Explain the open-vocabulary task interface + example prompts, then exit 0.",
    )
    args = parser.parse_args()

    # --list-tasks: open-vocab guidance. No fixed key list to print.
    if args.list_tasks:
        print(
            "SO-101 (MolmoAct2) is an open-vocabulary, language-conditioned policy.\n"
            "--task takes any natural-language instruction; there is no fixed task-key list.\n"
            "\n"
            "Pass your own instruction, e.g.:\n"
            '  uv run python3 run.py --task "Pick up the red block and place it in the bin."\n'
            "\n"
            "Example prompts (illustrative — adapt to your scene and objects):"
        )
        for p in _EXAMPLE_PROMPTS:
            print(f"  - {p}")
        print(
            "\nThe live registry is the source of truth for what the fine-tune was "
            "trained on;\n--check performs that round-trip when NT_API_KEY is set."
        )
        sys.exit(0)

    # Validate --task before any dispatch — no silent fallback. An EXPLICIT empty
    # instruction is a hard error (not a quiet default-swap). Omitting --task
    # entirely (None) falls back to the documented example below.
    if args.task is not None and not args.task.strip():
        print(
            "ERROR: --task was given but is empty. Pass a non-empty instruction, e.g.\n"
            '  --task "Pick up the cube and place it in the bin."\n'
            "Or omit --task to use an example prompt.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.check:
        _run_check(site_config_path=args.site_config, arm_id=args.arm)
        return  # _run_check exits; defensive

    if args.reset:
        _reset_arm(site_config_path=args.site_config, arm_id=args.arm)
        sys.exit(0)

    # Env-first: NT_API_KEY overrides the stored file. Cite: newt._credentials module.
    api_key = os.environ.get("NT_API_KEY") or read_api_key()
    if not api_key:
        print(
            "ERROR: no API key found.\n"
            "Set NT_API_KEY in your environment, or run `newt login` to store it:\n"
            "  export NT_API_KEY=<your-key>  # or: newt login",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.task is None:
        task_prompt = _DEFAULT_TASK
        print(
            f"[demo] no --task given; using example prompt {task_prompt!r}.\n"
            "[demo] SO-101 is open-vocabulary — pass --task to describe your scene.",
            flush=True,
        )
    else:
        task_prompt = args.task

    print("[demo] Initializing hardware rig…", flush=True)
    rig = SO101.from_config(site_config_path=args.site_config, arm_id=args.arm)

    # Arm Ctrl+H listener AFTER rig init so the connect-time work can't be aborted
    # mid-flight. From here through teardown the operator can hit Ctrl+H to
    # safe + home the arm.
    _start_keyboard_listener()

    # T1: entry point reads exactly like any other newt.Robot consumer.
    robot = newt.Robot(
        api_key=api_key,
        embodiment=rig,
        model=MODEL,
    )

    stop_reason = "error"
    exception_str: str | None = None
    emergency_stopped = False
    t_start = time.time()
    wall_s: float = 0.0

    try:
        print(f"[demo] Starting trial: {task_prompt!r}", flush=True)
        result = robot.run(task_prompt, max_duration=MAX_DURATION_S)
        stop_reason = result.stop_reason
    except _EmergencyStop:
        stop_reason = "emergency_stop"
        emergency_stopped = True
        try:
            rig.emergency_home()
        except Exception as exc:
            print(f"[demo] emergency_home failed: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        print("\n[demo] Interrupted by operator (Ctrl-C).", flush=True)
    except newt.AuthError as exc:
        exception_str = str(exc)
        print(
            f"\n[demo] Authentication error: {exc}\n"
            "Rotate your NT API key at the NT console and re-export NT_API_KEY.",
            file=sys.stderr,
        )
    finally:
        wall_s = time.time() - t_start
        rig.teardown()
        _restore_terminal()

    print(
        f"\n=== SO-101 (MolmoAct2) Demo — single trial ===\n"
        f"task:            {task_prompt}\n"
        f"model:           {MODEL}\n"
        f"max_duration_s:  {MAX_DURATION_S}\n"
        f"stop_reason:     {stop_reason}\n"
        f"chunks_observed: {rig.chunks_observed}\n"
        f"wall_s:          {wall_s:.1f}\n"
        f"exception:       {exception_str}\n"
        f"gripper_polarity_at_axis_6: unverified\n"
        f"  (operator records by watching the gripper open/close during the run;\n"
        f"   axis-6 sign is negative in synthetic obs but physical sign is the\n"
        f"   designed smoke resolution — see README §Gripper caveat)"
    )

    # Ctrl+H exits with 130 (Ctrl+C convention) so the shell + any harness can
    # distinguish operator-aborted from normal completion / model-driven stop.
    if emergency_stopped:
        sys.exit(130)


if __name__ == "__main__":
    # `run.py setup ...` delegates to scripts/setup.py before argparse sees it.
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        sys.exit(_delegate_setup(sys.argv[2:]))
    main()
