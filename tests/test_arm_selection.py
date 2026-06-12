"""Hardware-free arm selection tests (brief-248 pattern, SO-101 port).

Covers the cases the brief requires, adapted for the SO-101's USB serial port
(the Trossen starter selected by IP; the SO-101 selects by `/dev/tty*` port):
  - single-arm config: default to arms[0], zero behavior change
  - multi-arm config, no selection: refuses with named error (ids + ports + --arm)
  - --arm <id> selection on a two-arm config: correct entry
  - unknown --arm id: error names the id + configured ids
  - [robot_config]\narm = "..." config key selects without --arm; --arm wins.

Why this matters: arm selection happens before any hardware import, so a
misconfigured nt.toml fails loud with the right port named — the developer never
gets an opaque connect error against the wrong device.

Run:
    uv run pytest tests/test_arm_selection.py -v
"""
from __future__ import annotations

import tomllib
import pytest

from embodiment import _load_arm_port


_SINGLE_ARM = b"""
[[robot_config.arms]]
id   = "so101"
port = "/dev/ttyACM0"
"""

_DUAL_ARM = b"""
[[robot_config.arms]]
id   = "left-arm"
port = "/dev/ttyACM0"

[[robot_config.arms]]
id   = "right-arm"
port = "/dev/ttyACM1"
"""

_DUAL_ARM_WITH_CONFIG_KEY = b"""
[robot_config]
arm = "right-arm"

[[robot_config.arms]]
id   = "left-arm"
port = "/dev/ttyACM0"

[[robot_config.arms]]
id   = "right-arm"
port = "/dev/ttyACM1"
"""


def _parse(raw: bytes) -> dict:
    return tomllib.loads(raw.decode())


def test_single_arm_default():
    """Single-arm config with no --arm: returns arms[0]'s id + port unchanged."""
    raw = _parse(_SINGLE_ARM)
    arm_id, port = _load_arm_port(raw)
    assert arm_id == "so101"
    assert port == "/dev/ttyACM0"


def test_single_arm_explicit_selection():
    """Single-arm config with matching --arm: same result."""
    raw = _parse(_SINGLE_ARM)
    arm_id, port = _load_arm_port(raw, "so101")
    assert arm_id == "so101"
    assert port == "/dev/ttyACM0"


def test_multi_arm_no_selection_refuses():
    """Multi-arm config with no --arm: ValueError naming ids + ports + --arm."""
    raw = _parse(_DUAL_ARM)
    with pytest.raises(ValueError) as exc_info:
        _load_arm_port(raw)
    msg = str(exc_info.value)
    assert "left-arm" in msg
    assert "right-arm" in msg
    assert "/dev/ttyACM0" in msg
    assert "/dev/ttyACM1" in msg
    assert "--arm" in msg


def test_arm_selection_right():
    """--arm right-arm selects port /dev/ttyACM1."""
    raw = _parse(_DUAL_ARM)
    arm_id, port = _load_arm_port(raw, "right-arm")
    assert arm_id == "right-arm"
    assert port == "/dev/ttyACM1"


def test_arm_selection_left():
    """--arm left-arm selects port /dev/ttyACM0."""
    raw = _parse(_DUAL_ARM)
    arm_id, port = _load_arm_port(raw, "left-arm")
    assert arm_id == "left-arm"
    assert port == "/dev/ttyACM0"


def test_unknown_arm_id_names_options():
    """Unknown --arm id: ValueError naming the unknown id + configured ids."""
    raw = _parse(_DUAL_ARM)
    with pytest.raises(ValueError) as exc_info:
        _load_arm_port(raw, "nonexistent-arm")
    msg = str(exc_info.value)
    assert "nonexistent-arm" in msg
    assert "left-arm" in msg
    assert "right-arm" in msg


def test_config_key_selects_arm():
    """[robot_config]\narm = '...' in nt.toml selects without --arm."""
    raw = _parse(_DUAL_ARM_WITH_CONFIG_KEY)
    arm_id, port = _load_arm_port(raw)
    assert arm_id == "right-arm"
    assert port == "/dev/ttyACM1"


def test_flag_wins_over_config_key():
    """--arm flag overrides the [robot_config]\narm config key."""
    raw = _parse(_DUAL_ARM_WITH_CONFIG_KEY)
    arm_id, port = _load_arm_port(raw, "left-arm")
    assert arm_id == "left-arm"
    assert port == "/dev/ttyACM0"


def test_no_arms_configured_refuses():
    """Empty config (no [[robot_config.arms]]): ValueError naming the port key."""
    raw = _parse(b"")
    with pytest.raises(ValueError) as exc_info:
        _load_arm_port(raw)
    assert "port" in str(exc_info.value)
