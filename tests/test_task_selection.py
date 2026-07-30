"""Hardware-free task interface tests (brief-255 pattern, SO-101 port).

The Trossen starter inlined 13 fixed task keys and validated --task
against them. SO-101 / MolmoAct2 is OPEN-VOCABULARY (language-conditioned):
--task takes any natural-language instruction and there is no fixed key list to
validate against. So the goldens here are different in kind:

  - an EXPLICIT empty --task is a hard refusal (no silent default-swap, Rule 10)
  - --list-tasks exits 0, explains the open-vocab interface honestly, shows the
    example prompts, and does NOT fabricate a "trained task key" list it can't
    back up (the registry round-trip in --check is the real source of truth)

Why this matters: a developer must not be able to dispatch an empty instruction
to a language-conditioned model and get undefined behavior, and --list-tasks must
not lie about a fixed trained set when none is published.

Run:
    uv run pytest tests/test_task_selection.py -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Golden prompt strings, imported from run.py so the test fails if they drift.
from run import _EXAMPLE_PROMPTS, _DEFAULT_TASK

_REPO_ROOT = Path(__file__).parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run.py", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )


def test_empty_task_exits_nonzero():
    """An explicit empty --task exits non-zero (no silent default-swap)."""
    result = _run("--task", "")
    assert result.returncode != 0


def test_empty_task_error_names_the_problem():
    """The empty-task refusal explains itself and offers the fix paths."""
    result = _run("--task", "   ")  # whitespace-only is still empty
    err = result.stderr
    assert "empty" in err.lower(), f"expected 'empty' in refusal, got:\n{err}"
    # Offers both the give-an-instruction and the omit-for-example escape hatches.
    assert "--task" in err
    assert "omit" in err.lower()


def test_list_tasks_exits_zero():
    """--list-tasks exits 0."""
    result = _run("--list-tasks")
    assert result.returncode == 0


def test_list_tasks_explains_open_vocab():
    """--list-tasks names the open-vocabulary interface, honestly."""
    out = _run("--list-tasks").stdout
    assert "open-vocabulary" in out.lower()
    # Honesty: it must say there is no fixed key list, not invent one.
    assert "no fixed task-key list" in out.lower()


def test_list_tasks_includes_example_prompts():
    """--list-tasks shows each illustrative example prompt verbatim."""
    out = _run("--list-tasks").stdout
    for prompt in _EXAMPLE_PROMPTS:
        assert prompt in out, f"expected example prompt {prompt!r} in --list-tasks, got:\n{out}"


def test_list_tasks_points_at_registry_as_source_of_truth():
    """--list-tasks points at --check / the registry as the trained-set source."""
    out = _run("--list-tasks").stdout
    assert "--check" in out
    assert "registry" in out.lower()


def test_default_task_is_an_example_prompt():
    """The omitted-task default is one of the documented example prompts.

    Guards against the default and the published examples drifting apart — a
    developer who reads --list-tasks sees the same prompt the no-arg run uses.
    """
    assert _DEFAULT_TASK in _EXAMPLE_PROMPTS
