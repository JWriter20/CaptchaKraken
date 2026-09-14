"""The LGPL notice has to reach whoever installs the package, in both languages.

`cursory` and `cursory-js` are LGPL-3.0-or-later, used as ordinary installed
dependencies — declared in `pyproject.toml` and `package.json`, resolved by the
user's own package manager, never vendored or bundled. That arrangement is the
one the licence is written for and is what lets this product keep its own terms,
but it still carries an obligation: convey the notice.

Three copies exist because the two packages publish separately and npm/PyPI both
take files from their own directory only — the same reason LICENSE is duplicated.
Three copies are also three chances to drift, which is what this pins.

If this fails because someone edited one of them, copy the root NOTICE over the
other two rather than hand-merging: the root is the canonical one.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COPIES = [ROOT / "NOTICE", ROOT / "js" / "NOTICE", ROOT / "python" / "NOTICE"]


def test_every_package_carries_the_notice():
    missing = [str(p.relative_to(ROOT)) for p in COPIES if not p.is_file()]
    assert not missing, f"the LGPL notice is missing from: {missing}"


def test_the_copies_have_not_drifted():
    texts = {p: p.read_text() for p in COPIES}
    first = texts[COPIES[0]]
    differ = [str(p.relative_to(ROOT)) for p, t in texts.items() if t != first]
    assert not differ, (
        f"these NOTICE copies differ from the root one: {differ}. Copy the root "
        f"over them; a notice that says different things in different packages "
        f"is worse than one that says nothing.")


def test_it_names_what_it_is_for():
    """A notice that does not name the component or its licence is not a notice."""
    text = COPIES[0].read_text()
    for needle in ("cursory", "cursory-js", "LGPL-3.0-or-later", "Vinyzu"):
        assert needle in text, f"the notice does not mention {needle!r}"


def test_it_records_that_we_do_not_bundle():
    """The whole legal position rests on these being installed dependencies
    rather than vendored code. If someone ever bundles them, this sentence is
    the thing that should stop them — so it has to still be here."""
    text = COPIES[0].read_text()
    assert "vendor" in text.lower() and "bundle" in text.lower(), (
        "the notice no longer records that these are installed dependencies "
        "rather than vendored code, which is the arrangement the licence "
        "analysis depends on")
