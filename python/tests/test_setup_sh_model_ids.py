"""`setup.sh` must install a model the client actually knows about.

setup.sh hardcodes the adapter it downloads and writes into `captchakraken.env`,
and that env file OVERRIDES the client's own defaults — so a stale id there is
not a cosmetic drift, it is the model every self-hosting user ends up running.

The bug this pins: setup.sh shipped `CaptchaKraken/CaptchaKraken_v1`, a repo
that 401s on the Hub for everyone outside the org. `./setup.sh` — the one
command the README gives for self-hosting — failed at the download step, and
anyone who got past it had `CAPTCHA_LORA_ADAPTER` set to an id absent from
models.json, which resolves to generation-1 prompts. That is the exact silent
mispairing models.json exists to prevent (see its `_comment`), reached through
the installer instead of through training.

Hermetic: parses two files, no network and no Hub call.
"""
import re
from pathlib import Path

from captchakraken import prompts

_SETUP_SH = Path(__file__).resolve().parents[2] / "setup.sh"


def _shell_const(name: str) -> str:
    """Value of a top-level `NAME="value"` assignment in setup.sh."""
    match = re.search(rf'^{name}="([^"]*)"', _SETUP_SH.read_text(encoding="utf-8"), re.M)
    assert match, f"setup.sh no longer defines {name}"
    return match.group(1)


def test_setup_sh_exists():
    assert _SETUP_SH.is_file(), f"expected setup.sh at {_SETUP_SH}"


def test_setup_sh_adapter_is_registered():
    """The adapter setup.sh installs must be in models.json.

    An unregistered adapter cannot be mapped to the prompt generation it was
    trained on, so the client falls back to generation 1 and answers worse on
    every puzzle without erroring.
    """
    adapter = _shell_const("LORA_ADAPTER")
    registered = prompts.registered_models()
    assert adapter in registered, (
        f"setup.sh installs {adapter!r}, which is not registered in models.json. "
        f"Registered: {sorted(registered)}"
    )


def test_setup_sh_serves_the_name_that_model_declares():
    """The served name must match the adapter's entry.

    setup.sh passes LORA_NAME to `--lora-modules` AND writes it as
    CAPTCHA_LORA_NAME, so if the two disagree the client asks for a `model` the
    server does not serve.
    """
    adapter = _shell_const("LORA_ADAPTER")
    entry = prompts.registered_models().get(adapter, {})
    assert entry.get("lora_name") == _shell_const("LORA_NAME"), (
        f"setup.sh serves {adapter!r} as {_shell_const('LORA_NAME')!r}, but "
        f"models.json declares {entry.get('lora_name')!r}"
    )


def test_setup_sh_installs_the_current_default():
    """setup.sh and an unpinned client must land on the same model.

    They are two doors into the same install. If setup.sh lags `latest`, a user
    who ran the installer and a user who only `pip install`ed are running
    different weights while both believe they are on the default.
    """
    assert _shell_const("LORA_ADAPTER") == prompts.latest_model(), (
        f"setup.sh installs {_shell_const('LORA_ADAPTER')!r} but models.json "
        f"`latest` is {prompts.latest_model()!r}"
    )
