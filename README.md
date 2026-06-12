# newt-starter-so101

A minimal starter kit for running **MolmoAct2** — New Theory's SO-101 inference model — on a stock **SO-101 follower arm** with two USB cameras. Clone, configure, calibrate, run — the arm moves.

The SO-101 is a $200 arm: the lowest barrier to entry in the lineup. This kit takes you from `git clone` to a moving arm in about five minutes once the arm is calibrated.

## Prerequisites

- **SO-101 follower arm** connected over USB (appears as a serial device — `/dev/ttyACM*` on Linux, `/dev/tty.usbmodem*` on macOS)
- **2 USB webcams** — one looking down at the workspace (`top`), one looking across it (`side`)
- **newt CLI** — installed globally: `uv tool install "git+ssh://git@github.com/new-theory-research/newt-python.git"`. If you followed [Getting started](https://newtheory-docs.vercel.app/docs/getting-started), you have this.
- **NT API key** — set as `NT_API_KEY` in your environment, or stored via `newt login`
- **SSH key registered with the `new-theory-research` GitHub org** — the starter pulls `newt` and `lerobot` from private repos over SSH (`git+ssh://`), so `uv sync` needs a working SSH key. Test with `ssh -T git@github.com`. (No SSH key? See below for the HTTPS path.)
- **Python 3.11–3.13** and [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (a `.python-version` file pins 3.12 — uv handles this automatically). Python 3.14+ is not supported yet (the torch wall).

### No SSH key?

If your machine authenticates to GitHub over HTTPS only (e.g. via `gh auth`), `uv sync` will fail because the deps use `git+ssh://`. Two commands rewrite those URLs to HTTPS:

```bash
gh auth setup-git
git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"
```

`gh auth setup-git` registers the GitHub CLI as your HTTPS credential helper. The `insteadOf` line rewrites the `ssh://git@github.com/` prefix that uv passes to git for every `git+ssh://git@github.com/...` dep. After both commands, `uv sync` resolves via HTTPS and authenticates with your existing `gh` session.

### macOS works out of the box

Unlike the Trossen WidowX kit (which needs Intel RealSense depth cameras and a from-source build on macOS), the SO-101 uses plain USB webcams and Feetech servos. There is no depth camera, no RealSense SDK, no macOS wheel gap. `uv sync --extra hardware` installs the same on macOS and Linux. macOS serial ports just look different: `/dev/tty.usbmodem*` instead of `/dev/ttyACM*`.

## Setup

```bash
# SSH clone:
git clone git@github.com:new-theory-research/newt-starter-so101.git
# or HTTPS clone (no SSH key):
git clone https://github.com/new-theory-research/newt-starter-so101.git

cd newt-starter-so101

# Shape validation, no physical hardware (works on a stock Mac):
uv sync
# Physical rig (installs the Feetech servo SDK):
uv sync --extra hardware

uv run python3 run.py setup            # writes ~/.config/nt/nt.toml
export NT_API_KEY=your_key_here        # or: newt login

# One-time calibration — the SO-101 is NOT factory-calibrated (see §Calibration):
lerobot-calibrate --robot.type=so101_follower --robot.port=<port> --robot.id=so101_a

uv run python3 run.py --task "Pick up the cube and place it in the bin."
```

`uv run python3 run.py setup` handles the boilerplate (creates `~/.config/nt/nt.toml` from the template) and auto-detects what it can:

- **Serial port** — by enumerating connected serial devices; if exactly one SO-101 candidate is present, it fills the port in.
- **Camera indices** — `top` and `side` are left as template placeholders for you to fill in. We deliberately do **not** auto-open camera devices during setup, because opening them triggers OS permission prompts. Plug your cameras in one at a time and note the index each takes.

It reports what was autofilled and what still needs your attention. See "Manual setup" below if you'd rather do it by hand.

### Setup script flags

```bash
uv run python3 run.py setup                    # interactive (default)
uv run python3 run.py setup --force            # overwrite ~/.config/nt/nt.toml without prompting
uv run python3 run.py setup --report-only      # dry-run; show what would change
uv run python3 run.py setup --non-interactive  # fail loud on any step needing a prompt
uv run python3 run.py setup --json             # structured JSON output (for agent consumption)
```

## Verify your install (no robot needed)

```bash
uv run python3 run.py --check
```

`--check` has two tiers and never touches the arm or cameras:

- **Always** (no API key, no network): it loads your `nt.toml`, then names the arm and the two cameras (`top`, `side`) it would use. This is the floor — it exits 0 on a stock Mac with nothing connected.
- **With `NT_API_KEY` set**: it also does the live registry round-trip and confirms the `so101` contract arrived (state shape, camera names). This proves your key authenticates and the model resolves.

There is no fixture inference in `--check`: the SDK's bundled fixtures are 8-DOF Trossen episodes, the wrong shape for the SO-101's 6-DOF joint space. We do not fabricate a synthetic observation to fake an inference — the real first-inference proof is the live run on the arm. If you're setting up on a Mac, `--check` is where you stop until you're at the robot: the same repo and the same commands run on the Linux rig, plus `uv sync --extra hardware`.

## Run

```bash
uv run python3 run.py --task "Pick up the cube and place it in the bin."
```

The script connects to the MolmoAct2 inference API, streams two camera frames and the arm's six joint positions, and executes action chunks on the arm.

**MolmoAct2 is open-vocabulary (language-conditioned).** `--task` takes any natural-language instruction — there is no fixed task-key list. Omit `--task` and the script uses an illustrative example prompt and tells you so. Run `uv run python3 run.py --list-tasks` for the interface explanation and a few example prompts to adapt to your scene.

On first run, expect a pause of up to ~50 seconds while the inference container cold-starts. Subsequent calls land in a few seconds.

To move the arm to a safe rest pose without running inference:

```bash
uv run python3 run.py --reset
```

If your `nt.toml` defines multiple `[[robot_config.arms]]` entries, pass `--arm <id>` to select which arm drives the session — without it, `run.py` refuses and lists the configured arm ids.

## Stopping the arm

Press **Ctrl+H** at any time during the run to abort. The listener arms automatically when the script starts — no flag, no setup. On press, inference stops and the arm homes to its rest pose before exiting.

The listener needs a real TTY; piped or non-interactive invocations skip it silently and print a notice on startup. Ctrl+C still works in either case.

This is a software abort, not a hardware kill — use the rig's physical power switch for true emergency stops.

## Calibration

**The SO-101 is NOT factory-calibrated.** Unlike the Trossen WidowX (calibration burned into EEPROM at manufacturing), an SO-101 ships with no per-arm calibration — you must calibrate once before the first run, or the joint positions the model sees will not match the physical arm.

lerobot provides the calibration flow first-class:

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=<port> --robot.id=so101_a
```

`<port>` is your arm's serial port (`/dev/ttyACM0` on Linux, `/dev/tty.usbmodem*` on macOS). `--robot.id` must match the `id` field in your `nt.toml` arm entry (`so101_a` in the template) — that id names the calibration file.

What it does: it disables torque, prompts you to centre each joint, then to sweep each joint through its full range of motion (press ENTER to stop each sweep). It writes the result to:

```
~/.cache/huggingface/lerobot/calibration/robots/so101_follower/<id>.json
```

On subsequent runs the starter reuses that calibration automatically. To re-run it, calibrate again with the same `--robot.id`. (Override the calibration directory with `HF_LEROBOT_CALIBRATION` if you keep it elsewhere.)

## ⚠️ Gripper caveat — axis-6 sign is unverified on physical hardware

The gripper is the sixth joint. In the live `so101` contract, the gripper axis reports **negative-signed** values (observed `[-0.126, -0.038]` on a synthetic zero-state). **Whether negative means open or closed on a physical SO-101 has not been verified** — the contract was characterised from synthetic observations, not a moving gripper.

This is a known open question, not a polished claim. The first real-arm run is the designed resolution: watch the gripper as the arm runs, record whether it opens or closes and at what sign, and report it back so the answer lands in the record. Until then, **keep a hand near the power switch on your first run** — if the gripper moves opposite to what you expect, that is the caveat, not a malfunction. `run.py`'s end-of-run report prints `gripper_polarity_at_axis_6: unverified` to keep this in front of you.

## Eval-parity honesty note

This starter ships **no task-success benchmarks**. We do not yet publish a measured success rate for MolmoAct2 on the SO-101, and we make no eval-parity claim. The kit proves the path — clone, calibrate, infer, move — not a task-completion percentage. Treat the example prompts as a starting point for your own scene, and judge results by watching the arm. Eval-parity measurement is its own piece of work, tracked separately; when there is a number worth publishing, it will appear with the methodology behind it, not before.

## Your embodiment class

The hardware driver lives in `embodiment.py` as `SO101`. It implements the newt `Embodiment` protocol — `read_state()` and `execute(chunk)` — and `run.py` hands it straight to `newt.Robot`:

```python
from embodiment import SO101

rig = SO101.from_config()
robot = newt.Robot(embodiment=rig, model="so101")
```

`embodiment.py` is yours. Rename the class to match your robot, swap out the SO-101/lerobot wiring for your hardware, or subclass it. Anything that implements `read_state()` → dict and `execute(chunk)` → None works — no inheritance from a base class, no registration, no new name the SDK has to know.

`from_config()` is the sole config reader: it loads `~/.config/nt/nt.toml`, resolves the serial port and the two camera entries, and hands explicit values to `SO101.__init__`. `__init__` reads no files. If your hardware has a different config convention, edit `from_config()` to match — that's the intended seam.

**A note on lerobot (tenet T4 — build on the community where one exists).** The SO-101's community *is* [lerobot](https://github.com/huggingface/lerobot). This starter drives the arm *through* lerobot's `SO101Follower` driver rather than around it — `from_config()` is our thin abstraction over lerobot, not a replacement for it. We pin a specific lerobot revision (`lerobot-nt @ 50168c2a`) that carries the `SO101Follower` driver; do not bump it to upstream lerobot ≥ v0.4.4, where the `so101_follower` module was refactored away.

## `nt.toml` schema

`run.py` reads `~/.config/nt/nt.toml`. The SO-101 config is far simpler than the Trossen one — a serial port and two webcam indices, no depth cameras, no intrinsics, no extrinsics:

```toml
[[robot_config.arms]]
id   = "so101_a"           # also names the lerobot calibration file
port = "/dev/ttyACM0"      # Linux; macOS looks like /dev/tty.usbmodem*

[[camera_config.cameras]]
id            = "top"
index_or_path = 1
width         = 640
height        = 480
fps           = 30

[[camera_config.cameras]]
id            = "side"
index_or_path = 2
width         = 640
height        = 480
fps           = 30
```

**Camera id naming is load-bearing.** The strings `top` and `side` are the camera keys the so101 inference server names — they set the order frames are presented to the model, they are not display names. Keep them exactly as shown; only fill in the device indices.

**Which view is "top" vs "side"?** This is the one honest open question on the camera side. The live contract names the cameras `top` and `side` but does not pin the exact mount geometry the fine-tune was trained with. Mount one camera looking down at the workspace (`top`) and one looking across it (`side`), as close to the training rig as you can. Both cameras are `required: []` server-side: a missing camera is zero-filled with a `DegradationWarning` rather than a hard close, but the model was trained with both views, so motion quality degrades if one is absent — configure both for real runs. If motion looks confused after a run, try swapping the two `index_or_path` values (not the ids).

## Troubleshooting

**`uv sync` fails with authentication errors**

Test SSH access before syncing: `ssh -T git@github.com` should print `Hi <username>! You've successfully authenticated`. If it fails, your key isn't registered with the `new-theory-research` org — verify the key is listed under your GitHub account settings, and ask your New Theory contact to confirm you're a collaborator on `newt-python` and `lerobot-nt`. Or use the HTTPS path above.

**Can't find the serial port**

Run `ls /dev/ttyACM*` (Linux) or `ls /dev/tty.usbmodem*` (macOS). If more than one device appears, unplug the arm, re-run the command, and note which entry disappeared — that is the arm.

**Camera view looks wrong / motion looks confused**

Swap the `index_or_path` values for `top` and `side` in `~/.config/nt/nt.toml`. The camera ids (`top`, `side`) must stay as-is — the model was trained with those names. Only the indices move.

**First run hangs for ~50 seconds**

The inference container is cold-starting. The call returns once a container is warm, in a few seconds on subsequent calls. If it still fails after that, check your `NT_API_KEY` and network access.

**`NT_API_KEY` error on startup**

`run.py` exits immediately if no API key is found. Either set it in the environment, or run `newt login` once to store it in `~/.nt/credentials`:

```bash
export NT_API_KEY=your_key_here  # env-var path
# or
newt login                        # stores key in ~/.nt/credentials
uv run python3 run.py --task "..."
```

**Ctrl+H doesn't work in an agent-driven session (stdin is not a TTY)**

The keyboard listener requires a real TTY. In piped or agent-driven invocations it is skipped — the script prints a notice on startup when this happens. From a non-TTY context, send `SIGINT` to trigger the same home-and-release cleanup:

```bash
timeout --signal=INT <seconds> uv run python3 run.py --task "..."
```

## Manual setup

This is what `uv run python3 run.py setup` does under the hood. Use it if the script can't autodetect your hardware, or if you just prefer doing it by hand.

**1. Copy the config template**

```bash
mkdir -p ~/.config/nt
cp conf/nt.toml.example ~/.config/nt/nt.toml
```

**2. Find your SO-101 serial port**

```bash
ls /dev/ttyACM*        # Linux
ls /dev/tty.usbmodem*  # macOS
```

**3. Find your camera indices**

USB webcams are addressed by an integer index. `0` is usually the built-in camera (laptops); your external webcams are typically `1` and `2`. Plug them in one at a time and note the index each takes.

**4. Edit `~/.config/nt/nt.toml`**

Fill in:
- `port` — the serial port of your SO-101 arm
- `index_or_path` for each camera — match each index to the right slot (`top`, `side`)

**5. Calibrate** (see §Calibration above):

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=<port> --robot.id=so101_a
```

**6. Set your API key**

```bash
export NT_API_KEY=your_key_here  # env-var path
# or: newt login                  # stores key in ~/.nt/credentials
```

## What's next

- [New Theory getting started](https://newtheory-docs.vercel.app/docs/getting-started) — the SDK golden path: install → login → models → first inference
- [Set up your embodiment](https://newtheory-docs.vercel.app/docs/set-up-your-embodiment) — the full embodiment walkthrough
- [lerobot SO-101 docs](https://huggingface.co/docs/lerobot/so101) — the upstream driver this kit builds on (calibration, motors, teleoperation)
