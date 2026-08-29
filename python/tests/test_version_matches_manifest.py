"""`captchakraken.__version__` must equal the version pip installs.

It drifted once already: 2.6.1 shipped on PyPI and on npm while
`__init__.py` still said 2.6.0, so every caller that branches on
`captchakraken.__version__` — and every bug report that quotes it —
named a release that was not the one running.

Nothing else checks it. `pyproject.toml` is the manifest pip reads, so it
is the one this compares against; a mismatch is a release-hygiene bug, not
a matter of opinion.
"""

import re
from pathlib import Path

import pytest

import captchakraken

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_dunder_version_matches_pyproject():
    if not _PYPROJECT.exists():  # installed without the sdist's manifest
        pytest.skip("pyproject.toml is not next to the tests in this install")
    declared = re.search(
        r'^version\s*=\s*"([^"]+)"', _PYPROJECT.read_text(encoding="utf-8"), re.M
    )
    assert declared, "no version field in pyproject.toml"
    assert captchakraken.__version__ == declared.group(1)
