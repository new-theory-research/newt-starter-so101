"""Hardware-free explicit-args construction test (brief-248 factory split, SO-101 port).

Verifies that SO101.__init__ accepts arm_id, port, and a cameras dict as
explicit values — no config file, no hardware, no real lerobot import. The
factory split (the 248 addendum) makes from_config() the sole config reader and
__init__ a pure explicit-value constructor; these tests pin that boundary.

The SO-101's __init__ does more than the Trossen one: it instantiates an
SO101Follower and calls connect() (which reads action_features to assert the
joint order). So unlike the Trossen explicit-construction test, we must mock the
follower class and give it action_features in the canonical joint order, or the
load-bearing joint-order assertion would fire. We patch the lazily-imported
hardware globals directly — the real lerobot is never touched.

Run:
    uv run pytest tests/test_explicit_construction.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import embodiment


def _full_cameras() -> dict:
    """A cameras dict in the shape _load_cameras() returns — both required keys."""
    return {
        "top":  {"index_or_path": 0, "width": 640, "height": 480, "fps": 30},
        "side": {"index_or_path": 1, "width": 640, "height": 480, "fps": 30},
    }


def _make_follower_cls() -> MagicMock:
    """A mock SO101Follower class whose instance reports the canonical joint order.

    action_features must carry `<motor>.pos` keys in _JOINT_ORDER, or __init__'s
    joint-order assertion (load-bearing for the model's state/action encoder)
    raises RuntimeError.
    """
    follower_cls = MagicMock()
    instance = follower_cls.return_value
    instance.action_features = {f"{m}.pos": None for m in embodiment._JOINT_ORDER}
    return follower_cls


def _hardware_patches():
    """Patch the lazily-imported hardware globals so no real lerobot is touched."""
    return (
        patch.object(embodiment, "_import_hardware_deps"),
        patch.object(embodiment, "_LEROBOT_AVAILABLE", True),
        patch.object(embodiment, "OpenCVCameraConfig", MagicMock()),
        patch.object(embodiment, "SO101FollowerConfig", MagicMock()),
        patch.object(embodiment, "SO101Follower", _make_follower_cls()),
    )


def test_explicit_construction_stores_arm_id():
    """__init__ stores arm_id and connects via the (mocked) follower — no config."""
    p1, p2, p3, p4, p5 = _hardware_patches()
    with p1, p2, p3, p4, p5:
        rig = embodiment.SO101(
            arm_id="so101_a",
            port="/dev/ttyACM0",
            cameras=_full_cameras(),
        )

    assert rig._arm_id == "so101_a"
    assert rig.max_actions_per_chunk == embodiment.MAX_ACTIONS_PER_CHUNK
    # connect() was called on the follower instance (the hardware handshake).
    rig._robot.connect.assert_called_once()


def test_explicit_construction_missing_camera_raises():
    """__init__ raises ValueError if a required camera key is absent.

    The missing-camera check fires before any follower instantiation, so this
    needs only the import-availability patches.
    """
    cameras = {"top": {"index_or_path": 0, "width": 640, "height": 480, "fps": 30}}
    with (
        patch.object(embodiment, "_import_hardware_deps"),
        patch.object(embodiment, "_LEROBOT_AVAILABLE", True),
    ):
        with pytest.raises(ValueError, match="side"):
            embodiment.SO101(
                arm_id="so101_a",
                port="/dev/ttyACM0",
                cameras=cameras,
            )


def test_wrong_joint_order_fails_loud():
    """If the follower reports a joint order != _JOINT_ORDER, __init__ refuses.

    This is the load-bearing invariant: the model's state/action encoder depends
    on the joint order, so a silent reorder would serve confidently-wrong motion.
    """
    follower_cls = MagicMock()
    # Reverse the canonical order to simulate a lerobot motor-order change.
    follower_cls.return_value.action_features = {
        f"{m}.pos": None for m in reversed(embodiment._JOINT_ORDER)
    }
    with (
        patch.object(embodiment, "_import_hardware_deps"),
        patch.object(embodiment, "_LEROBOT_AVAILABLE", True),
        patch.object(embodiment, "OpenCVCameraConfig", MagicMock()),
        patch.object(embodiment, "SO101FollowerConfig", MagicMock()),
        patch.object(embodiment, "SO101Follower", follower_cls),
    ):
        with pytest.raises(RuntimeError, match="joint order"):
            embodiment.SO101(
                arm_id="so101_a",
                port="/dev/ttyACM0",
                cameras=_full_cameras(),
            )


def test_no_config_file_read_in_init():
    """Constructing SO101 directly never reads any file.

    Even if a config file exists, __init__ ignores it entirely — only
    from_config() touches the filesystem. We patch open() to raise so any file
    access during construction turns into a loud failure.
    """
    import builtins  # noqa: F401  (kept for clarity of what we patch)

    def _no_file_open(path, *args, **kwargs):
        raise AssertionError(
            f"__init__ must not read any file; attempted open({path!r})"
        )

    p1, p2, p3, p4, p5 = _hardware_patches()
    with p1, p2, p3, p4, p5, patch("builtins.open", side_effect=_no_file_open):
        rig = embodiment.SO101(
            arm_id="test-arm",
            port="/dev/ttyACM0",
            cameras=_full_cameras(),
        )

    assert rig._arm_id == "test-arm"


def test_missing_hardware_deps_raises_importerror():
    """When lerobot is unavailable, __init__ raises a clear ImportError.

    Proves the lazy-import guard: the module imports clean without hardware, but
    constructing the rig without `uv sync --extra hardware` fails loud with the
    fix command, not an opaque AttributeError deep in the follower.
    """
    with (
        patch.object(embodiment, "_import_hardware_deps"),
        patch.object(embodiment, "_LEROBOT_AVAILABLE", False),
        patch.object(embodiment, "_LEROBOT_IMPORT_ERR", ModuleNotFoundError("lerobot")),
    ):
        with pytest.raises(ImportError, match="uv sync --extra hardware"):
            embodiment.SO101(
                arm_id="so101_a",
                port="/dev/ttyACM0",
                cameras=_full_cameras(),
            )
