"""setup.sh once shipped a repo that 401s on the Hub for everyone outside the org; its env file overrides the client defaults, so it must name what the registry serves."""

import re
from pathlib import Path

from captchakraken import prompts

_SETUP_SH = Path(__file__).resolve().parents[2] / "setup.sh"


def _shell_const(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"', _SETUP_SH.read_text(encoding="utf-8"), re.M)
    assert match, f"setup.sh no longer defines {name}"
    return match.group(1)


def test_setup_sh_exists():
    assert _SETUP_SH.is_file(), f"expected setup.sh at {_SETUP_SH}"


def test_setup_sh_adapter_is_registered():
    adapter = _shell_const("LORA_ADAPTER")
    registered = prompts.registered_models()
    assert adapter in registered, (
        f"setup.sh installs {adapter!r}, which is not registered in models.json. "
        f"Registered: {sorted(registered)}"
    )


def test_setup_sh_serves_the_name_that_model_declares():
    adapter = _shell_const("LORA_ADAPTER")
    entry = prompts.registered_models().get(adapter, {})
    assert entry.get("lora_name") == _shell_const("LORA_NAME"), (
        f"setup.sh serves {adapter!r} as {_shell_const('LORA_NAME')!r}, but "
        f"models.json declares {entry.get('lora_name')!r}"
    )


def test_setup_sh_installs_the_current_default():
    assert _shell_const("LORA_ADAPTER") == prompts.latest_model(), (
        f"setup.sh installs {_shell_const('LORA_ADAPTER')!r} but models.json "
        f"`latest` is {prompts.latest_model()!r}"
    )
