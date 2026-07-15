import os
from pathlib import Path


def pytest_configure():
    """
    Ensure .env is loaded for test runs so GEMINI_API_KEY (and others) are available
    even when tests import modules directly (bypassing src/__init__.py).
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        # override=False so exported env vars still win.
        load_dotenv(env_path, override=False)

    # Also support people running with a repo-local key name by mistake.
    if not os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]


