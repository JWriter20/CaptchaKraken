"""The CLI's stdout is a wire protocol, not a user interface.

The TypeScript driver does not reimplement grid detection: it shells out to this
CLI and `JSON.parse`s stdout. That makes three things load-bearing, and none of
them is obvious from reading either port alone:

  1. stdout carries exactly one JSON document and nothing else. A usage line, a
     warning or a traceback printed there is parsed as a solve result, and the
     driver reports a malformed answer rather than the real problem.
  2. Diagnostics go to stderr, where the driver reads them for the message.
  3. A refusal exits non-zero, so the caller can tell "no answer" from "the
     answer is null".

The handlers read `sys.argv` directly, so these drive them exactly as the shell
would, in-process and with no subprocess or network.
"""

import json

import cv2
import numpy as np
import pytest

from captchakraken import cli


def _png(path, value=255, size=(80, 60)):
    img = np.full((size[1], size[0], 3), value, dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _run(monkeypatch, capsys, handler, argv):
    monkeypatch.setattr("sys.argv", ["captchakraken", *argv])
    status = None
    try:
        handled = handler()
    except SystemExit as e:
        handled, status = True, e.code
    out, err = capsys.readouterr()
    return handled, status, out, err


def test_identical_frames_report_no_movement(tmp_path, monkeypatch, capsys):
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png")

    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_movement_commands, ["check-movement", a, b]
    )

    assert handled and status is None
    assert json.loads(out) == {"has_movement": False}


def test_a_changed_frame_reports_movement(tmp_path, monkeypatch, capsys):
    a = _png(tmp_path / "a.png", value=255)
    b = _png(tmp_path / "b.png", value=0)

    _, _, out, _ = _run(
        monkeypatch, capsys, cli._handle_movement_commands, ["check-movement", a, b]
    )

    assert json.loads(out) == {"has_movement": True}


def test_stdout_stays_empty_when_the_arguments_are_wrong(tmp_path, monkeypatch, capsys):
    """The failure this prevents: a usage string on stdout is valid text and
    invalid JSON, so the driver reports "could not parse the solver's answer"
    for what is really a caller bug two layers up."""
    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_movement_commands, ["check-movement", "only-one.png"]
    )

    assert status == 1, "a usage error must exit non-zero"
    assert out == "", f"usage text reached stdout: {out!r}"
    assert json.loads(err)["error"].startswith("Usage:")


def test_a_missing_image_is_refused_on_stderr_with_a_non_zero_exit(tmp_path, monkeypatch, capsys):
    """`null` on stdout would mean "no checkbox in this image". A missing file is
    a different answer and has to look different."""
    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_tool_commands,
        ["find-checkbox", str(tmp_path / "absent.png")],
    )

    assert status == 1
    assert out == ""
    assert "Image not found" in json.loads(err)["error"]


def test_a_readable_image_answers_with_one_json_document(tmp_path, monkeypatch, capsys):
    """A blank page has no checkbox, and `null` is the right answer for that —
    what matters is that it is JSON, alone, on stdout."""
    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_tool_commands, ["find-checkbox", _png(tmp_path / "p.png")]
    )

    assert handled and status is None
    assert json.loads(out) is None
    assert out.count("\n") == 1, "more than one line reached stdout"


def test_an_unparseable_threshold_falls_back_instead_of_crashing(tmp_path, monkeypatch, capsys):
    """The driver passes the threshold through from config. A bad value must not
    take the whole poll down mid-solve — the documented default is used."""
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png")

    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_movement_commands,
        ["check-movement", a, b, "not-a-number"],
    )

    assert status is None
    assert json.loads(out) == {"has_movement": False}


def test_an_unrelated_command_is_declined_rather_than_swallowed(monkeypatch, capsys):
    """Each handler returns False for a command that is not its own, so `main`
    can try the next one. Returning True would make the first handler eat every
    subcommand after it."""
    handled, status, out, err = _run(
        monkeypatch, capsys, cli._handle_movement_commands, ["server", "status"]
    )

    assert handled is False
    assert out == "" and err == ""


def test_no_arguments_at_all_is_declined_by_every_handler(monkeypatch, capsys):
    """`captchakraken` with no argv[1] must fall through to the image parser,
    not be claimed by a subcommand handler reading argv[1] that is not there."""
    for handler in (cli._handle_movement_commands, cli._handle_tool_commands,
                    cli._handle_move_commands):
        handled, status, out, err = _run(monkeypatch, capsys, handler, [])
        assert handled is False
