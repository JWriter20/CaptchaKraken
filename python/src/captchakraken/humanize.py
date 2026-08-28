"""
How the driver MOVES. One pluggable object per input device.

Humanisation used to be wired straight into `page_solver`: every gesture was a
`page.mouse.*` call with a Bezier trajectory in front of it and a `random()`
sleep behind it, and there was no way to ask for anything else. That is wrong in
three directions at once —

  - a caller driving a MOBILE page has no cursor. Dispatching mousemove at a
    touch-only widget is not weak humanisation, it is the wrong event type: the
    page's touch handlers never fire, and a vendor that scores pointer telemetry
    sees a desktop mouse on a phone.
  - a caller who has their OWN humanisation (a hardware pointer, a proxied
    device farm, a model of their own users) was composing two of them. That is
    not hypothetical — camoufox's `humanize` juggler re-humanises every
    `mouse.move()` it is handed, and running both measured 82.1s against 13.4s
    on one geetest_v4_slide solve, because each of the 60 trajectory points
    became its own humanised sub-trajectory.
  - a caller on their own infrastructure, against their own fixtures, is paying
    for texture nobody is measuring.

So the vocabulary of gestures is an INTERFACE and the humanisation is an
implementation of it. `page_solver` says "tap here", "drag this there", "type
that"; a `Humanizer` decides what events that is and how long it takes.

Four modes, selected by `PageSolverConfig.humanization`:

    "mouse"   MouseHumanizer   — the default, and byte-for-byte what the driver
                                 did before this module existed.
    "mobile"  MobileHumanizer  — touch events, finger kinematics. Dispatches
                                 through a `TouchBackend`, so it drives either a
                                 Chromium-family page (CDP) or an Appium/W3C
                                 driver on a real device.
    "none"    NullHumanizer    — the shortest legal path to the same DOM effect.
    custom    anything satisfying `Humanizer`, passed as
                                 `PageSolverConfig.humanizer`.

THE POINTER POSITION LIVES HERE, not in the solver. A humanizer that dispatches
no motion at all (mobile, between taps) still has to answer "where is the
pointer", because that is what the next gesture starts from and what
`SolveResult.final_mouse_position` reports.

The TypeScript side is `js/src/humanize.ts`; the two are one design in two
languages, and `tests/test_humanizer_parity.py` in CaptchaKrakenFinetune pins
that the mode names and the pause vocabulary have not drifted.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Dict, Optional, Sequence, Tuple

from .trajectory import generate_swipe, generate_trajectory

Point = Tuple[float, float]


def _delay(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _log(message: str) -> None:
    print(f"[captchakraken] {message}", flush=True)


def _same_point(a: Point, b: Point) -> bool:
    """A move to where the pointer already is emits nothing.

    `_move_and_click` travels to the element and THEN clicks at the point it
    landed on, so without this every click would dispatch one redundant move at
    the coordinate the trajectory just finished on.
    """
    return abs(float(a[0]) - float(b[0])) < 1e-6 and abs(float(a[1]) - float(b[1])) < 1e-6


#: Every inter-gesture wait the driver takes, named.
#:
#: A vocabulary rather than a number at each call site, because the whole point
#: of the mode switch is that these differ per device — a finger dwells on a tap
#: about four times as long as a mouse button is held, and neither number means
#: anything to the caller who turned humanisation off. Each mode supplies its own
#: table below; an unknown name yields no wait rather than raising, so adding a
#: pause site cannot break a custom humanizer written against an older release.
#:
#:   tap      inside a click/tap, between press and release
#:   between  between two taps of one batch (grid tiles)
#:   grab     after pressing, before a drag starts moving
#:   drop     after a drag arrives, before releasing
#:   probe    between the slider's measurement nudges
#:   settle   before releasing a slider — the release IS the submit, and some
#:            vendors sample the final milliseconds of the gesture
#:   key      between two typed characters
PAUSE_KINDS = ("tap", "between", "grab", "drop", "probe", "settle", "key")


class Humanizer:
    """The gesture vocabulary `page_solver` drives. Subclass or duck-type.

    A custom implementation only has to provide `move`, `press`, `release`,
    `type_text` and `_pause_ms`; `click` and `drag` are composed from those and
    are almost never worth overriding.
    """

    #: Mode name, for logs and for `SolveResult` reporting.
    name = "custom"
    #: Whether this device has a cursor that can rest somewhere without
    #: touching. False switches off every hover-for-realism behaviour — the
    #: reCAPTCHA tile hover and the idle drift during inference — because on a
    #: touchscreen those are not weak mimicry, they are impossible.
    hovers = False

    def __init__(self, start: Point = (0.0, 0.0)) -> None:
        #: Where the pointer is now. Read by the solver for
        #: `SolveResult.final_mouse_position`.
        self.at: Point = (float(start[0]), float(start[1]))

    # -- lifecycle ---------------------------------------------------------
    def reset(self, page: Any) -> None:
        """Called once at the top of each `solve()`. Drop per-page caches."""

    # -- primitives --------------------------------------------------------
    def move(self, page: Any, to: Point) -> None:
        raise NotImplementedError

    def press(self, page: Any) -> None:
        raise NotImplementedError

    def release(self, page: Any) -> None:
        raise NotImplementedError

    def type_text(self, page: Any, field: Any, text: str) -> bool:
        raise NotImplementedError

    def _pause_ms(self, kind: str) -> float:
        return 0.0

    # -- composed ----------------------------------------------------------
    def pause(self, kind: str) -> None:
        _delay(self._pause_ms(kind))

    def click(self, page: Any, to: Point) -> None:
        self.move(page, to)
        self.press(page)
        self.pause("tap")
        self.release(page)

    def drag(self, page: Any, src: Point, dst: Point) -> None:
        self.move(page, src)
        self.press(page)
        self.pause("grab")
        self.move(page, dst)
        self.pause("drop")
        self.release(page)


# ---------------------------------------------------------------------------
# Mouse — the historical behaviour, moved here unchanged
# ---------------------------------------------------------------------------


class MouseHumanizer(Humanizer):
    """A hand on a mouse. Bezier arcs, Fitts's-law durations, overshoot.

    Everything in here was `page_solver._smooth_move` / `_trace_path` /
    `_seed_cursor` / `_viewport` before this module existed, and is unchanged:
    the constants were measured, the camoufox workarounds were expensive to
    find, and a refactor is not the place to revisit either.
    """

    name = "mouse"
    hovers = True

    #: Inclusive ranges, in ms. See PAUSE_KINDS.
    PAUSES: Dict[str, Tuple[float, float]] = {
        "tap": (20.0, 50.0),
        "between": (80.0, 160.0),
        "grab": (50.0, 100.0),
        "drop": (50.0, 100.0),
        "probe": (40.0, 80.0),
        "settle": (90.0, 210.0),
        "key": (45.0, 135.0),
    }

    def __init__(self, start: Point = (0.0, 0.0), frequency: int = 60) -> None:
        super().__init__(start)
        self._frequency = frequency
        self._viewport_cache: Optional[Dict[str, float]] = None
        # See `_seed_cursor`: the (0, 0) origin wedges camoufox's humanised
        # mouse, so the first move of each solve must step off it plainly.
        self._cursor_seeded = False

    def reset(self, page: Any) -> None:
        self._viewport_cache = None
        self._cursor_seeded = False

    def _pause_ms(self, kind: str) -> float:
        lo, hi = self.PAUSES.get(kind, (0.0, 0.0))
        return random.uniform(lo, hi)

    # -- viewport ----------------------------------------------------------
    def _viewport(self, page: Any) -> Optional[Dict[str, float]]:
        """
        The window we must keep the cursor inside, cached per solve.

        MUST NOT be skipped and MUST NOT be guessed, because a mouse move to a
        coordinate outside the window WEDGES camoufox. Its juggler humanises
        each move into a trajectory, guards the intermediate points against the
        bounds, and then dispatches the requested destination unguarded
        ("Always finish exactly on the requested destination"). An out-of-window
        destination fires as an exit event instead of eMouseMove, so no
        hit-renderer ack comes back; dispatch is serialised on a process-global
        activation chain, so that one missing ack hangs every later input event
        forever. Symptom: `page.mouse.move()` never returns, 0% CPU, solve
        appears dead. Same failure family as camoufox #225.

        `page.viewport_size` is None under camoufox (it uses the real window
        rather than a spoofed viewport), which is why this falls back to asking
        the page itself instead of assuming a size.
        """
        if self._viewport_cache is not None:
            return self._viewport_cache
        try:
            vp = page.viewport_size
            if callable(vp):  # some adapters expose it as a method
                vp = vp()
            if vp and vp.get("width") and vp.get("height"):
                self._viewport_cache = {"width": float(vp["width"]), "height": float(vp["height"])}
                return self._viewport_cache
        except Exception:
            pass
        try:
            inner = page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
            if inner and inner.get("width") and inner.get("height"):
                self._viewport_cache = {
                    "width": float(inner["width"]),
                    "height": float(inner["height"]),
                }
                return self._viewport_cache
        except Exception:
            pass
        return None

    def _seed_cursor(self, page: Any) -> None:
        """Move the cursor off its (0, 0) origin once per solve, in ONE plain
        move, before any humanised trajectory runs.

        Without this, the first trajectory of a solve begins at (0, 0) — the
        exact window corner, because that is where the pointer starts and
        nothing has moved it. Under camoufox's `humanize` juggler a short move
        from that origin never returns: `page.mouse.move()` blocks forever at 0%
        CPU, and since dispatch is serialised on a process-global activation
        chain, every later input event blocks behind it too. The solve looks
        hung with no error anywhere and the Tier 3 fixture run times out on all
        three attempts.

        Reduced to four lines against camoufox directly:

            with Camoufox(headless=True, humanize=True) as b:
                p = b.new_page(); p.goto("about:blank")
                p.mouse.move(1.0, 1.0)      # never returns

        The same move after ANY interior move completes in ~1.1s, which is what
        makes this the fix rather than a workaround: it is the ORIGIN that is
        poisoned, not the destination. Same failure family as camoufox #225.

        Deliberately not routed through `move`: that would generate a trajectory
        from (0, 0) and reintroduce exactly the move being avoided.
        """
        if self._cursor_seeded:
            return
        self._cursor_seeded = True
        if self.at != (0.0, 0.0):
            # The caller named a starting position. Stepping plainly to THAT
            # satisfies this just as well as the centre does, and honouring it
            # is the whole reason the option exists.
            cx, cy = self.at
        else:
            vp = self._viewport(page)
            # Centre when the window is known, else a modest interior point —
            # any coordinate comfortably off the corner will do.
            cx, cy = (vp["width"] / 2, vp["height"] / 2) if vp else (200.0, 200.0)
        try:
            page.mouse.move(cx, cy)
            self.at = (cx, cy)
        except Exception:  # noqa: BLE001 — an adapter without a mouse must not fail the solve
            pass

    # -- primitives --------------------------------------------------------
    def move(self, page: Any, to: Point) -> None:
        self._seed_cursor(page)
        if _same_point(self.at, to):
            return
        points, timings = generate_trajectory(self.at, to, self._frequency)
        self._trace(page, points, timings)

    def press(self, page: Any) -> None:
        page.mouse.down()

    def release(self, page: Any) -> None:
        page.mouse.up()

    def type_text(self, page: Any, field: Any, text: str) -> bool:
        # A retry round arrives with the previous attempt still in the box, and
        # typing would APPEND to it — submitting a string the model never read.
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        # Per character rather than one `type(text, delay=…)` call: a constant
        # inter-key delay is itself a signal, and these are the vendors that
        # score typing cadence.
        for ch in text:
            try:
                page.keyboard.type(ch)
            except Exception as exc:
                _log(f"could not type into the captcha field: {exc}")
                return False
            self.pause("key")
        return True

    def _trace(self, page: Any, points: Sequence[Point], timings: Sequence[float]) -> None:
        # Clamp ONLY when the viewport is actually known.
        #
        # camoufox reports `viewport_size is None` (it uses the real window
        # rather than a spoofed viewport). The obvious fallback — assume
        # 1920x1080 — is actively harmful: it clamps coordinates to the edge of
        # a viewport that is not the real one, and a coordinate sitting EXACTLY
        # on the boundary is the input that deadlocks camoufox's humanised-mouse
        # juggler patch (upstream #225, "humanize edge deadlock"). The symptom is
        # a `page.mouse.move()` that never returns: the process sits at 0% CPU
        # with no in-flight work and the solve appears hung, which is precisely
        # what this driver did against camoufox until this was found.
        #
        # A guessed clamp buys nothing anyway — the points come from a
        # trajectory between two on-screen elements, so they are already in
        # range. When we do clamp, we inset by a pixel so a legitimately
        # off-screen point lands just inside the edge instead of exactly on it.
        viewport = self._viewport(page)

        start = time.monotonic() * 1000.0
        for i, (x, y) in enumerate(points):
            try:
                if viewport is None:
                    cx, cy = float(x), float(y)
                else:
                    cx = max(1.0, min(float(x), float(viewport["width"]) - 1.0))
                    cy = max(1.0, min(float(y), float(viewport["height"]) - 1.0))
                page.mouse.move(cx, cy)
                self.at = (cx, cy)
                if i < len(timings):
                    target = start + timings[i]
                    _delay(target - time.monotonic() * 1000.0)
            except Exception as exc:
                msg = str(exc)
                if "Target closed" in msg or "Session closed" in msg:
                    _log("could not move mouse; page or session closed")
                    return
                # Any other per-sample failure is skipped rather than fatal —
                # losing one mousemove must not lose the solve.


# ---------------------------------------------------------------------------
# Touch dispatch backends
# ---------------------------------------------------------------------------


class TouchBackend:
    """Where touch events actually go.

    Three methods rather than one per sample, because the two backends want
    opposite things: CDP dispatches one event per call and we pace it locally,
    while a W3C/Appium driver takes a whole timed action chain in one round trip
    and paces it on the device. Handing `move` the entire leg lets each do what
    it is good at — and on a real device, pacing over the wire is not pacing at
    all.
    """

    #: Human name for logs / errors.
    name = "touch"

    def down(self, x: float, y: float) -> None:
        raise NotImplementedError

    def move(self, path: Sequence[Tuple[float, float, float]]) -> None:
        """`path` is (x, y, dt_ms) — dt is the wait BEFORE that sample."""
        raise NotImplementedError

    def up(self, x: float, y: float) -> None:
        raise NotImplementedError


class CdpTouchBackend(TouchBackend):
    """`Input.dispatchTouchEvent` over a CDP session on a Playwright page.

    Chromium-family only, and that is checked at construction rather than
    discovered per gesture: WebKit and Firefox (camoufox included) expose no
    touch dispatch through Playwright at all, so the alternatives there are to
    fail loudly or to quietly emit MOUSE events at a touch-only widget. The
    second is worse than not running — the page's touch handlers never fire, the
    solve fails for a reason nothing reports, and the report reads as a model
    that cannot solve mobile puzzles.

    The context must also have been created with `has_touch=True`, or the page
    advertises no touch support to feature detection and a mobile widget renders
    its desktop branch.
    """

    name = "cdp"

    def __init__(self, page: Any) -> None:
        self._session = self._open(page)

    @staticmethod
    def _open(page: Any) -> Any:
        try:
            context = page.context
            if callable(context):
                context = context()
            return context.new_cdp_session(page)
        except Exception as exc:  # noqa: BLE001 — the message is the whole value here
            raise RuntimeError(
                "mobile humanisation needs touch dispatch, and this page offers "
                f"none ({exc}). Use a Chromium-family Playwright browser launched "
                "with has_touch=True, or pass an Appium driver as "
                "PageSolverConfig.touch_driver."
            ) from exc

    def _send(self, kind: str, points: Sequence[Tuple[float, float]]) -> None:
        self._session.send(
            "Input.dispatchTouchEvent",
            {
                "type": kind,
                "touchPoints": [
                    # radiusX/Y and force are what a real digitizer reports and
                    # a synthetic tap does not. A vendor reading
                    # `Touch.radiusX === 0` has a free bot signal otherwise.
                    {
                        "x": float(x),
                        "y": float(y),
                        "radiusX": random.uniform(8.0, 14.0),
                        "radiusY": random.uniform(8.0, 14.0),
                        "force": random.uniform(0.35, 0.75),
                        "id": 1,
                    }
                    for x, y in points
                ],
            },
        )

    def down(self, x: float, y: float) -> None:
        self._send("touchStart", [(x, y)])

    def move(self, path: Sequence[Tuple[float, float, float]]) -> None:
        for x, y, dt_ms in path:
            _delay(dt_ms)
            self._send("touchMove", [(x, y)])

    def up(self, x: float, y: float) -> None:
        # touchEnd carries no touchPoints: the point being lifted is identified
        # by its absence, which is what the protocol says and what a real
        # release looks like.
        self._send("touchEnd", [])


class AppiumTouchBackend(TouchBackend):
    """W3C pointer actions with `pointerType: touch`, for Appium and Selenium.

    Batches a whole leg into ONE action chain with per-sample durations, so the
    kinematics are reproduced BY THE DEVICE rather than by a Python loop
    round-tripping over the wire — over which a 90Hz path is not 90Hz at all.

    Press and release are separate `perform()` calls on purpose. W3C input state
    is per SESSION, so a pointer put down in one chain stays down across later
    chains until it is lifted; that is what lets the puzzle-slider driver press,
    screenshot, steer, screenshot and only then release. (It is also why the
    spec has a "release actions" endpoint at all.)

    COORDINATES. Everything upstream of here is CSS pixels in the page's
    viewport, because that is what `bounding_box()` returns. A real device wants
    screen pixels, and the two differ by the device pixel ratio and by whatever
    chrome sits above the webview. Neither is guessable from here, so both are
    parameters:

        AppiumTouchBackend(driver, scale=3.0, origin=(0, 132))

    `scale` is usually `window.devicePixelRatio`; `origin` is the top-left of
    the webview in screen coordinates. Defaults are the identity transform,
    which is correct for browser mobile emulation and for any caller who has
    already mapped the coordinates themselves.
    """

    name = "appium"

    def __init__(
        self,
        driver: Any,
        scale: Optional[float] = None,
        origin: Optional[Point] = None,
        page: Any = None,
    ) -> None:
        self._driver = driver
        # UNSET is not the same fact as an explicit 1.0. The first is a caller
        # who has not thought about the transform; the second is one who has and
        # says the coordinates are already mapped. Only the first is checked.
        self._scale_given = scale is not None
        self._scale = 1.0 if scale is None else float(scale)
        self._origin = (0.0, 0.0) if origin is None else (float(origin[0]), float(origin[1]))
        self._page = page
        self._checked = False

    def _check_scale(self) -> None:
        """Refuse an unset scale on a session that reports it is not 1.

        A wrong transform fails SILENTLY on both sides of the wire: the chain is
        valid W3C, the device performs it, and the finger lands somewhere else.
        The solve then fails looking exactly like a model that cannot read the
        puzzle. Same failure the fixture preflight and `_shot_scale` exist to
        prevent, one seam over.

        Read once, not per gesture — it is a round trip into the page and a
        solve makes hundreds of these. An unreadable ratio is absent evidence,
        not evidence of a mismatch, so it falls through to the identity.
        `origin` cannot be measured from here at all, so it is named in the
        refusal rather than guessed at.
        """
        self._checked = True
        if self._scale_given or self._page is None:
            return
        evaluate = getattr(self._page, "evaluate", None)
        if evaluate is None:
            return
        try:
            dpr = float(evaluate("() => window.devicePixelRatio"))
        except Exception:  # noqa: BLE001 — cannot measure is not a mismatch
            return
        if abs(dpr - 1.0) < 1e-6:
            return
        raise RuntimeError(
            f"the touch driver maps CSS pixels onto a device reporting "
            f"devicePixelRatio {dpr:g}, and no scale was given. Every gesture "
            f"would land at {1.0 / dpr:.2f}x the intended offset, and nothing "
            f"would report it — the chain is valid, the device performs it, and "
            f"the solve fails looking like a weak model.\n"
            f"  Fix: touch_transform={{'scale': {dpr:g}, 'origin': (x, y)}}, "
            f"where origin is the webview's top-left in SCREEN coordinates. "
            f"That half cannot be measured from here.\n"
            f"  Or pass scale=1 to assert the coordinates are already mapped."
        )

    def _map(self, x: float, y: float) -> Tuple[int, int]:
        if not self._checked:
            self._check_scale()
        return (
            int(round(self._origin[0] + x * self._scale)),
            int(round(self._origin[1] + y * self._scale)),
        )

    def _perform(self, actions: Sequence[Dict[str, Any]]) -> None:
        """One W3C action chain on a single touch pointer.

        Sent as the raw protocol payload rather than through Selenium's
        `ActionBuilder`, so this needs no selenium import and works against any
        client that speaks WebDriver — Appium's Python client, plain Selenium 4,
        or a thin HTTP wrapper of someone's own.
        """
        payload = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "ck-finger",
                    "parameters": {"pointerType": "touch"},
                    "actions": list(actions),
                }
            ]
        }
        # `execute` is the low-level WebDriver command hook every Selenium-family
        # client exposes; `execute_actions` is what some Appium clients call the
        # same thing. Try the specific name first so a client that validates the
        # payload gets to.
        for attempt in ("execute_actions", "execute"):
            fn = getattr(self._driver, attempt, None)
            if fn is None:
                continue
            if attempt == "execute_actions":
                fn(payload["actions"])
            else:
                fn("actions", payload)
            return
        raise RuntimeError(
            "the touch driver speaks neither execute_actions() nor "
            "execute('actions', …); pass a WebDriver-compatible driver or a "
            "custom TouchBackend."
        )

    def down(self, x: float, y: float) -> None:
        mx, my = self._map(x, y)
        self._perform([
            {"type": "pointerMove", "duration": 0, "origin": "viewport", "x": mx, "y": my},
            {"type": "pointerDown", "button": 0},
        ])

    def move(self, path: Sequence[Tuple[float, float, float]]) -> None:
        actions = []
        for x, y, dt_ms in path:
            mx, my = self._map(x, y)
            # The per-sample gap becomes the move's DURATION, not a pause before
            # it: the device then interpolates over that window and reports
            # intermediate samples of its own, which is what a finger sliding
            # across a digitizer actually produces.
            actions.append({
                "type": "pointerMove",
                "duration": max(0, int(round(dt_ms))),
                "origin": "viewport",
                "x": mx,
                "y": my,
            })
        if actions:
            self._perform(actions)

    def up(self, x: float, y: float) -> None:
        if not self._checked:
            self._check_scale()
        self._perform([{"type": "pointerUp", "button": 0}])


class TouchscreenTouchBackend(TouchBackend):
    """Playwright's `page.touchscreen.tap()` — taps only, no travel.

    The fallback for a browser with touch support but no CDP (WebKit). A tap
    still lands correctly; a DRAG cannot be expressed at all, so it raises
    rather than approximating one with a mouse, for the reason in
    `CdpTouchBackend`.
    """

    name = "touchscreen"

    def __init__(self, page: Any) -> None:
        self._page = page
        self._pending: Optional[Point] = None

    def down(self, x: float, y: float) -> None:
        self._pending = (x, y)

    def move(self, path: Sequence[Tuple[float, float, float]]) -> None:
        raise RuntimeError(
            "this browser exposes taps but not touch travel, so a drag/slide "
            "puzzle cannot be driven on it. Use a Chromium-family browser (CDP "
            "touch dispatch) or an Appium driver."
        )

    def up(self, x: float, y: float) -> None:
        at = self._pending or (x, y)
        self._pending = None
        self._page.touchscreen.tap(float(at[0]), float(at[1]))


def touch_backend_for(page: Any, driver: Any = None, **kwargs: Any) -> TouchBackend:
    """Pick a backend. An explicit `driver` always wins.

    `driver` is what a caller passes when the thing under automation is not the
    thing being touched — an Appium session driving a real handset while the
    page object is a webview bridge over it.
    """
    if driver is not None:
        if isinstance(driver, TouchBackend):
            return driver
        # The page comes along so the backend can ask what it is mapping ONTO.
        return AppiumTouchBackend(driver, page=page, **kwargs)
    try:
        return CdpTouchBackend(page)
    except RuntimeError:
        if hasattr(page, "touchscreen"):
            _log("no CDP session; falling back to tap-only touch dispatch")
            return TouchscreenTouchBackend(page)
        raise


# ---------------------------------------------------------------------------
# Mobile
# ---------------------------------------------------------------------------


class MobileHumanizer(Humanizer):
    """A finger on glass.

    What differs from `MouseHumanizer`, and none of it is cosmetic:

      - **A move that is not touching dispatches nothing.** There is no hover on
        a touchscreen. The position is still RECORDED, because the next gesture
        starts from it, but no event is emitted — emitting one would be the
        desktop tell this mode exists to remove.
      - **Taps carry a contact wobble.** A finger held on glass for 90ms does not
        report one unchanging coordinate; the centroid of the contact patch
        rolls a pixel or two under pressure. A tap with zero movement between
        touchstart and touchend is a synthetic tap.
      - **Touch kinematics**, via `generate_swipe` — see its docstring for why
        that is a different model rather than the mouse one retuned.
      - **Longer, more variable pauses.** Every measured touch interaction is
        slower than its mouse equivalent, and a phone's are more variable
        because the hand holding the device is also moving.
    """

    name = "mobile"
    hovers = False

    PAUSES: Dict[str, Tuple[float, float]] = {
        # A tap's press-to-release. Measured human touch dwell clusters at
        # 60-120ms; a synthesised one is usually 0.
        "tap": (55.0, 130.0),
        "between": (140.0, 320.0),
        "grab": (90.0, 190.0),
        "drop": (80.0, 170.0),
        "probe": (70.0, 140.0),
        "settle": (140.0, 300.0),
        # Soft-keyboard typing, which is ~3x slower than a physical keyboard
        # and much more variable.
        "key": (110.0, 320.0),
    }

    def __init__(
        self,
        start: Point = (0.0, 0.0),
        backend: Any = None,
        driver: Any = None,
        frequency: int = 90,
        **backend_kwargs: Any,
    ) -> None:
        super().__init__(start)
        self._backend: Optional[TouchBackend] = backend
        self._driver = driver
        self._backend_kwargs = backend_kwargs
        self._frequency = frequency
        self._down = False

    def reset(self, page: Any) -> None:
        # A solve that ended mid-gesture (a timeout inside the slider) leaves a
        # pointer down in the SESSION's input state, and the next chain would
        # then start from a finger already on the glass. Lift it.
        if self._down:
            try:
                self._touch(page).up(*self.at)
            except Exception:  # noqa: BLE001 — best effort; a stale pointer must not fail a solve
                pass
            self._down = False

    def _touch(self, page: Any) -> TouchBackend:
        if self._backend is None:
            self._backend = touch_backend_for(page, self._driver, **self._backend_kwargs)
        return self._backend

    def _pause_ms(self, kind: str) -> float:
        lo, hi = self.PAUSES.get(kind, (0.0, 0.0))
        return random.uniform(lo, hi)

    def move(self, page: Any, to: Point) -> None:
        to = (float(to[0]), float(to[1]))
        if not self._down:
            # No contact, no events. Just remember where the next touch lands.
            self.at = to
            return
        points, timings = generate_swipe(self.at, to, self._frequency)
        path = [
            (x, y, timings[i] - (timings[i - 1] if i else 0.0))
            for i, (x, y) in enumerate(points)
        ]
        self._touch(page).move(path)
        self.at = to

    def press(self, page: Any) -> None:
        self._touch(page).down(*self.at)
        self._down = True

    def release(self, page: Any) -> None:
        self._touch(page).up(*self.at)
        self._down = False

    def click(self, page: Any, to: Point) -> None:
        """Tap. The wobble is the point — see the class docstring."""
        self.move(page, to)
        self.press(page)
        held = self._pause_ms("tap")
        _delay(held * 0.5)
        try:
            self._touch(page).move([
                (self.at[0] + random.gauss(0.0, 0.9), self.at[1] + random.gauss(0.0, 0.9), 0.0)
            ])
        except Exception:  # noqa: BLE001 — a tap-only backend cannot wobble; the tap still lands
            pass
        _delay(held * 0.5)
        self.release(page)

    def type_text(self, page: Any, field: Any, text: str) -> bool:
        """Into the field, at soft-keyboard pace.

        The mouse path clears with Control+A. There is no Control on a phone
        keyboard, so the box is cleared through the element instead — which also
        works on an Appium `WebElement`, where `page.keyboard` does not exist.
        """
        for clear in ("clear", "fill"):
            fn = getattr(field, clear, None)
            if fn is None:
                continue
            try:
                fn() if clear == "clear" else fn("")
                break
            except Exception:  # noqa: BLE001 — an uncleared box is recoverable; a crash is not
                continue

        send = getattr(field, "send_keys", None)
        for ch in text:
            try:
                if send is not None:
                    send(ch)          # Appium / Selenium WebElement
                else:
                    page.keyboard.type(ch)   # Playwright mobile emulation
            except Exception as exc:
                _log(f"could not type into the captcha field: {exc}")
                return False
            self.pause("key")
        return True


# ---------------------------------------------------------------------------
# None
# ---------------------------------------------------------------------------


class NullHumanizer(Humanizer):
    """No humanisation: the shortest legal path to the same DOM effect.

    One mousemove per gesture instead of sixty, no dwell, no jitter, and text
    goes in with a single `fill()`. Roughly an order of magnitude faster on a
    slider puzzle, and it will be detected by anything that scores pointer
    telemetry — which is the whole trade, stated plainly. For fixtures, for
    self-hosted targets, and for callers whose stack humanises somewhere else.

    Still moves the mouse and still presses and releases: the events are what
    make the widget respond at all, and a click dispatched with no preceding
    move fails on the vendors that require a hover state first.
    """

    name = "none"
    hovers = False

    def reset(self, page: Any) -> None:
        pass

    def move(self, page: Any, to: Point) -> None:
        if _same_point(self.at, to):
            return
        self.at = (float(to[0]), float(to[1]))
        try:
            page.mouse.move(self.at[0], self.at[1])
        except Exception as exc:  # noqa: BLE001
            if "Target closed" in str(exc) or "Session closed" in str(exc):
                _log("could not move mouse; page or session closed")

    def press(self, page: Any) -> None:
        page.mouse.down()

    def release(self, page: Any) -> None:
        page.mouse.up()

    def type_text(self, page: Any, field: Any, text: str) -> bool:
        try:
            field.fill(text)     # replaces, so no Control+A round is needed
            return True
        except Exception:
            pass
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        try:
            page.keyboard.type(text)
            return True
        except Exception as exc:
            _log(f"could not type into the captcha field: {exc}")
            return False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

MODES = {"mouse": MouseHumanizer, "mobile": MobileHumanizer, "none": NullHumanizer}


def resolve(config: Any) -> Humanizer:
    """The humanizer one `PageSolver` will use, from its config.

    Precedence, and the reasoning for it:

      1. `config.humanizer` — a caller who handed us an OBJECT has already
         decided; there is nothing left to select.
      2. `config.humanization` — an explicit mode set in code.
      3. `CAPTCHA_HUMANIZATION` — for a caller who cannot edit the code.
      4. "mouse".

    Note that the env var loses to code, which is the opposite of this package's
    model-identity settings. Deliberate: which mode is right is a property of
    the PAGE the caller is driving, and an env var flipping a desktop solve to
    touch dispatch would break every one of them silently. Pinning a model is a
    deployment decision; picking an input device is not.
    """
    custom = getattr(config, "humanizer", None)
    if custom is not None:
        return custom

    mode = getattr(config, "humanization", None) or os.getenv("CAPTCHA_HUMANIZATION") or "mouse"
    mode = str(mode).strip().lower()
    if mode not in MODES:
        raise ValueError(
            f"unknown humanization mode {mode!r}; expected one of "
            f"{', '.join(sorted(MODES))}, or pass your own object as "
            f"PageSolverConfig.humanizer"
        )

    start = getattr(config, "starting_mouse_position", None) or (0.0, 0.0)
    if mode == "mobile":
        return MobileHumanizer(
            start,
            driver=getattr(config, "touch_driver", None),
            **(getattr(config, "touch_transform", None) or {}),
        )
    return MODES[mode](start)
