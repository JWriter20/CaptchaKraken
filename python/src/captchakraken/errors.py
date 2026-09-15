"""What the hosted API's refusals mean, in words a user can act on.

Before this, a camoufox user out of credits read `vLLM 402 Payment Required at https://api.captchakraken.com/...`.
Branch on `code`, never on prose; an unknown code carries the server's own message through, so the eleventh
code is never reported worse than the ten. The self-hosted path (no envelope) is untouched.
"""

from typing import Any, Dict, Optional

from .kinds import ErrorCode

_DASHBOARD = "https://captchakraken.com/dashboard"
_SUPPORT = "https://captchakraken.com/support"


class CaptchaKrakenAPIError(RuntimeError):

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
    """Numeric form only: guessing wrong about an HTTP-date would produce a confident "wait 3 seconds"."""
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _sentence(code: str, message: str, url: Optional[str], retry: Optional[float]) -> str:
    if code == ErrorCode.INSUFFICIENT_CREDITS:
        return (
            "CaptchaKraken: your account is out of credits, so this solve was refused. "
            f"Top up at {url or _DASHBOARD} and retry."
        )

    if code == ErrorCode.SOLVE_ABANDONED:
        return (
            "CaptchaKraken: this captcha attempt was served too many times without "
            "settling and has been abandoned. That usually means the IP reputation or "
            "the browser fingerprint is being rejected — not that the answers were "
            "wrong. Start a fresh attempt with a new x-ck-session (the driver mints "
            "one per solve() call, so a new solve is enough)."
        )

    if code == ErrorCode.RATE_LIMITED:
        wait = f" Retry in about {retry:g}s." if retry else " Back off and retry."
        return f"CaptchaKraken: too many requests.{wait}"

    if code == ErrorCode.ACCOUNT_SUSPENDED:
        return (
            "CaptchaKraken: this account is suspended, so solving is disabled. "
            f"Contact support at {url or _SUPPORT}."
        )

    if code == ErrorCode.REQUEST_TOO_LARGE:
        return (
            "CaptchaKraken: the screenshot sent for this solve exceeded the request "
            f"size limit. ({message}) Capture the captcha element rather than the "
            "whole page if you are not already."
        )

    if code in (ErrorCode.MISSING_API_KEY, ErrorCode.INVALID_API_KEY):
        return (
            "CaptchaKraken: the API key was missing or not accepted. Set "
            "CAPTCHA_KRAKEN_API_KEY, or run the CaptchaKraken MCP server's "
            "`create_api_key` tool to write one to ~/.captchakraken/credentials. "
            f"Manage keys at {url or _DASHBOARD}."
        )

    if code == ErrorCode.UPSTREAM_UNAVAILABLE:
        return (
            "CaptchaKraken: the solver fleet is temporarily unreachable. This is on "
            "our side, not yours — retry shortly."
        )

    # Anything added after this was written: the server's message is the best thing available.
    tail = f" See {url}." if url else ""
    return f"CaptchaKraken: {message}{tail}"


def from_response(resp: Any, url: str) -> Exception:
    body_text = (getattr(resp, "text", "") or "")[:300]
    status = getattr(resp, "status_code", None)

    error: Dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            error = parsed["error"]
    except Exception:
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

    # No gateway envelope: a local vLLM, a proxy, or an HTML error page. Self-hosted debugging is unchanged.
    hint = ""
    if status in (401, 403):
        hint = " — check CAPTCHA_KRAKEN_API_KEY is set and forwarded to the CLI"
    reason = getattr(resp, "reason", "") or ""
    return RuntimeError(f"vLLM {status} {reason} at {url}{hint}. Body: {body_text}")
