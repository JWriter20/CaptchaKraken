"""`CaptchaKrakenAPIError` must be importable from the package root.

Rule 1c: the two ports expose the same surface. The TypeScript port exports
`CaptchaKrakenAPIError` from its entry point, and docs/hosted-api.md tells
every caller to branch on its `.code` — but in Python it lived only in
`captchakraken.errors`, so the documented recipe did not work in one of the
two languages the docs claim parity for.

`errors` imports nothing but `typing`, so this export carries no dependency
floor and belongs outside the optional serving-stack guard.
"""

import captchakraken


def test_api_error_importable_from_package_root():
    from captchakraken import CaptchaKrakenAPIError

    assert issubclass(CaptchaKrakenAPIError, Exception)


def test_api_error_is_in_dunder_all():
    assert "CaptchaKrakenAPIError" in captchakraken.__all__
