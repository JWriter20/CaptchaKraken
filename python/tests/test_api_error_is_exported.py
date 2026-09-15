import captchakraken


def test_api_error_importable_from_package_root():
    from captchakraken import CaptchaKrakenAPIError

    assert issubclass(CaptchaKrakenAPIError, Exception)


def test_api_error_is_in_dunder_all():
    assert "CaptchaKrakenAPIError" in captchakraken.__all__
