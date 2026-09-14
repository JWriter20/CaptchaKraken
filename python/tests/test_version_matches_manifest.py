import re
from pathlib import Path

import pytest

import captchakraken

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_dunder_version_matches_pyproject():
    if not _PYPROJECT.exists():
        pytest.skip("pyproject.toml is not next to the tests in this install")
    declared = re.search(
        r'^version\s*=\s*"([^"]+)"', _PYPROJECT.read_text(encoding="utf-8"), re.M
    )
    assert declared, "no version field in pyproject.toml"
    assert captchakraken.__version__ == declared.group(1)
