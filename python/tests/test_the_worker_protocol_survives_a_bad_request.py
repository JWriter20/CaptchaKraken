"""`captchakraken serve` is a long-lived worker, and the driver depends on it
staying alive.

The reCAPTCHA poll loop cannot afford ~0.4s of interpreter and OpenCV import per
poll, so the TypeScript driver starts ONE worker and streams JSON lines at it
while the mouse is held down. Three properties make that safe, and all three are
invisible from either side alone:

  1. It announces readiness before it reads anything, so the driver knows the
     imports are done rather than timing its first poll against them.
  2. Every answer echoes the request's `id`, so answers can be matched to
     requests on a pipe that is inherently ordered but not labelled.
  3. **A bad request must not kill it.** One malformed line mid-drag would
     otherwise take the worker down with the mouse still pressed, and every
     later poll fails against a dead pipe — reported as a solve timeout, a long
     way from the line that caused it.

Driven here by feeding stdin directly, so no process is spawned and no image
needs to exist except the ones written to tmp_path.
"""

import io
import json

import cv2
import numpy as np
import pytest

from captchakraken import cli


def _png(path, value=255, size=(60, 40)):
    cv2.imwrite(str(path), np.full((size[1], size[0], 3), value, dtype=np.uint8))
    return str(path)


def _serve(monkeypatch, capsys, lines):
    """Run the worker over a fixed list of request lines and return its replies."""
    monkeypatch.setattr("sys.argv", ["captchakraken", "serve"])
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(f"{l}\n" for l in lines)))
    assert cli._handle_serve() is True
    out = capsys.readouterr().out.strip().split("\n")
    return [json.loads(l) for l in out if l]


def test_it_announces_readiness_before_reading_a_request(monkeypatch, capsys):
    replies = _serve(monkeypatch, capsys, [])
    assert replies == [{"ready": True}], "the worker must say ready even with no work"


def test_every_answer_carries_the_id_it_was_asked_with(monkeypatch, capsys, tmp_path):
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png")
    replies = _serve(monkeypatch, capsys, [
        json.dumps({"id": 11, "cmd": "check-movement", "a": a, "b": b}),
        json.dumps({"id": 12, "cmd": "check-movement", "a": a, "b": b}),
    ])

    assert replies[0] == {"ready": True}
    assert [r["id"] for r in replies[1:]] == [11, 12]
    assert all(r["ok"] for r in replies[1:])


def test_a_malformed_line_is_answered_and_the_worker_keeps_going(monkeypatch, capsys, tmp_path):
    """The property that matters most: the request after the bad one is served."""
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png")
    replies = _serve(monkeypatch, capsys, [
        "{not json at all",
        json.dumps({"id": 2, "cmd": "check-movement", "a": a, "b": b}),
    ])

    assert replies[1]["ok"] is False, "a malformed line must be answered, not ignored"
    assert replies[1]["error"]
    assert replies[2] == {"id": 2, "ok": True, "result": {"has_movement": False}}, \
        "the worker died on a bad line instead of serving the next request"


def test_an_unknown_command_is_refused_by_name_without_dying(monkeypatch, capsys, tmp_path):
    a = _png(tmp_path / "a.png")
    replies = _serve(monkeypatch, capsys, [
        json.dumps({"id": 1, "cmd": "polish-the-brass"}),
        json.dumps({"id": 2, "cmd": "check-movement", "a": a, "b": a}),
    ])

    assert replies[1] == {"id": 1, "ok": False, "error": "unknown cmd: 'polish-the-brass'"}
    assert replies[2]["ok"] is True


def test_a_request_missing_an_argument_fails_only_that_request(monkeypatch, capsys, tmp_path):
    """A driver bug in one poll must not end the drag."""
    a = _png(tmp_path / "a.png")
    replies = _serve(monkeypatch, capsys, [
        json.dumps({"id": 1, "cmd": "check-movement"}),          # no images
        json.dumps({"id": 2, "cmd": "check-movement", "a": a, "b": a}),
    ])

    assert replies[1]["ok"] is False and replies[1]["id"] == 1
    assert replies[2]["ok"] is True


def test_blank_lines_are_skipped_rather_than_answered(monkeypatch, capsys):
    """A flush that ends in a newline must not produce a spurious reply the
    driver would match against the wrong request."""
    replies = _serve(monkeypatch, capsys, ["", "   ", ""])
    assert replies == [{"ready": True}]


def test_the_wait_gate_answers_a_match_for_an_unchanged_region(monkeypatch, capsys, tmp_path):
    """`match-region` is the poll that decides when the mouse goes down."""
    ref = _png(tmp_path / "ref.png", value=120)
    live = _png(tmp_path / "live.png", value=120)
    replies = _serve(monkeypatch, capsys, [
        json.dumps({"id": 1, "cmd": "match-region", "ref": ref, "live": live,
                    "cx": 0.5, "cy": 0.5}),
    ])

    result = replies[1]["result"]
    assert result["match"] is True
    assert result["diff"] == 0.0
    assert "tolerance" in result, "the caller has to be able to see what it was judged against"


def test_the_wait_gate_stays_shut_on_an_unreadable_frame(monkeypatch, capsys, tmp_path):
    """A screenshot that failed to write must not read as "the board has settled"
    and release a click into the wrong state."""
    ref = _png(tmp_path / "ref.png")
    replies = _serve(monkeypatch, capsys, [
        json.dumps({"id": 1, "cmd": "match-region", "ref": ref,
                    "live": str(tmp_path / "missing.png"), "cx": 0.5, "cy": 0.5}),
    ])

    result = replies[1]["result"]
    assert result["match"] is False
    assert result["diff"] == 1.0
