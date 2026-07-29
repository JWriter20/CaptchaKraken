"""What the hosted API's refusals mean, in words a user can act on.

The solver talks to an OpenAI-compatible endpoint that may be a local vLLM the
user started themselves or the hosted CaptchaKraken gateway. Those two fail very
differently, and until this module existed both were reported the same way:
`vLLM 402 Payment Required at https://api.captchakraken.com/v1/chat/completions`.
A camoufox user who has never heard of vLLM, run vLLM, or intended to run vLLM
would hit that on the day their credits ran out.

Two rules, both inherited from the gateway's own `src/errors.ts`:

 1. BRANCH ON `error.code`, NEVER ON PROSE. The messages get reworded; the codes
    are the contract. A client that pattern-matches "Out of credits" breaks the
    day someone improves the wording.

 2. AN UNRECOGNISED CODE MUST STILL PRODUCE A USEFUL MESSAGE. There are ten
    codes today and there will be more. Enumerating them exhaustively here means
    the eleventh is reported worse than the ten — so the fallback carries the
    server's own `message` and `resolution_url` through, and only the phrasing
    is lost.

The self-hosted path is deliberately untouched: a local vLLM does not send this
envelope, so `from_response` returns a message shaped like the old one and the
401/403 hint about the bearer token survives. Nobody self-hosting sees a word
about credits.
"""

from typing import Any, Dict, Optional

# Where a user with no `resolution_url` in hand should be sent. Only used as a
# fallback — the server almost always supplies a deep link that is better than
# this, and when it does we prefer it.
_DASHBOARD = "https://captchakraken.com/dashboard"
_SUPPORT = "https://captchakraken.com/support"


class CaptchaKrakenAPIError(RuntimeError):
    """A refusal from the hosted API, already translated.

    Carries the machine-readable parts alongside the message so a caller that
    wants to react programmatically (retry on `rate_limited`, stop on
    `insufficient_credits`) can do so without re-parsing anything. `str(e)` is
    the human sentence and is what reaches a console.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        resolution_url: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.resolution_url = resolution_url
        self.retry_after_seconds = retry_after_seconds

    def to_payload(self) -> Dict[str, Any]:
        """The shape the CLI puts on stderr for the JS driver to pick up.

        The JS half shells out to this CLI and can only see stdout/stderr, so
        the structured fields would otherwise be flattened to a string at the
        process boundary and have to be re-parsed out of prose — exactly the
        thing rule 1 forbids.
        """
        return {
            "error": str(self),
            "ck_error": {
                "status": self.status,
                "code": self.code,
                "resolution_url": self.resolution_url,
                "retry_after_seconds": self.retry_after_seconds,
            },
        }


def _retry_after(headers: Any) -> Optional[float]:
    """Seconds from the `Retry-After` header, if it is the numeric form.

    HTTP also permits an HTTP-date here. The gateway only ever sends deltas, and
    guessing wrong about a date would produce a confidently incorrect "wait 3
    seconds", so anything unparseable is dropped rather than approximated.
    """
    try:
        raw = headers.get("Retry-After")
    except Exception:  # noqa: BLE001 — a mapping-ish object is all we assume
        return None
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _sentence(code: str, message: str, url: Optional[str], retry: Optional[float]) -> str:
    """The user-facing line for a known code.

    Every branch names CaptchaKraken. That is the entire point of this module:
    the person reading it needs to know which product is refusing them before
    anything else in the sentence can help.
    """
    if code == "insufficient_credits":
        return (
            "CaptchaKraken: your account is out of credits, so this solve was refused. "
            f"Top up at {url or _DASHBOARD} and retry."
        )

    if code == "solve_abandoned":
        return (
            "CaptchaKraken: this captcha attempt was served too many times without "
            "settling and has been abandoned. That usually means the IP reputation or "
            "the browser fingerprint is being rejected — not that the answers were "
            "wrong. Start a fresh attempt with a new x-ck-session (the driver mints "
            "one per solve() call, so a new solve is enough)."
        )

    if code == "rate_limited":
        wait = f" Retry in about {retry:g}s." if retry else " Back off and retry."
        return f"CaptchaKraken: too many requests.{wait}"

    if code == "account_suspended":
        return (
            "CaptchaKraken: this account is suspended, so solving is disabled. "
            f"Contact support at {url or _SUPPORT}."
        )

    if code == "request_too_large":
        return (
            "CaptchaKraken: the screenshot sent for this solve exceeded the request "
            f"size limit. ({message}) Capture the captcha element rather than the "
            "whole page if you are not already."
        )

    if code in ("missing_api_key", "invalid_api_key"):
        return (
            "CaptchaKraken: the API key was missing or not accepted. Set "
            "CAPTCHA_KRAKEN_API_KEY, or run the CaptchaKraken MCP server's "
            "`create_api_key` tool to write one to ~/.captchakraken/credentials. "
            f"Manage keys at {url or _DASHBOARD}."
        )

    if code == "upstream_unavailable":
        return (
            "CaptchaKraken: the solver fleet is temporarily unreachable. This is on "
            "our side, not yours — retry shortly."
        )

    # `unrecognized_prompt`, `invalid_request`, and anything added after this was
    # written. The server's own message is the best thing available, so it is
    # carried through verbatim rather than replaced with a guess.
    tail = f" See {url}." if url else ""
    return f"CaptchaKraken: {message}{tail}"


def from_response(resp: Any, url: str) -> Exception:
    """Build the exception for a non-OK response, hosted or self-hosted.

    Returns rather than raises so the caller's `raise` keeps the traceback
    anchored at the request site, where it is useful.
    """
    body_text = (getattr(resp, "text", "") or "")[:300]
    status = getattr(resp, "status_code", None)

    error: Dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            error = parsed["error"]
    except Exception:  # noqa: BLE001 — a non-JSON body is the self-hosted case
        error = {}

    code = error.get("code")
    if isinstance(code, str) and code:
        retry = _retry_after(getattr(resp, "headers", {}) or {})
        resolution = error.get("resolution_url") or None
        message = str(error.get("message") or "").strip() or "the request was refused"
        return CaptchaKrakenAPIError(
            _sentence(code, message, resolution, retry),
            status=status,
            code=code,
            resolution_url=resolution,
            retry_after_seconds=retry,
        )

    # No gateway envelope: a local vLLM, a proxy in between, or an HTML error
    # page. Keep the pre-existing message so self-hosted debugging is unchanged.
    hint = ""
    if status in (401, 403):
        hint = " — check CAPTCHA_KRAKEN_API_KEY is set and forwarded to the CLI"
    reason = getattr(resp, "reason", "") or ""
    return RuntimeError(f"vLLM {status} {reason} at {url}{hint}. Body: {body_text}")
