"""Smoke test: the Gradio page can be constructed.

This is the safety net for refactoring deadinternet/ui.py. Building the Blocks
tree is not a trivial import check -- gradio validates component types and
callable-ness at wire time, so constructing the page exercises every panel
builder and every .click/.change/.load registration in one go. A handler bound
to a component that no longer exists, a panel that forgot to create one, or a
callable gradio cannot introspect all fail here rather than in the browser.

What it deliberately does NOT check: whether a handler is wired to the *right*
outputs. Gradio accepts a wrong-but-well-typed output list without complaint;
only the manual checklist in the refactor plan catches that.

No tts-server, Ollama or Discord token is needed. Every network call on the
build path already swallows its own exception -- see voice_choices() in
deadinternet/ui.py, DeadInternetApp.banner() and OllamaClient.models() -- so an
offline machine takes the same code path a booting one does.

Runs two ways, because pytest is not currently a dependency of this project:

    .venv-app/bin/python deadinternet/tests/test_ui_build.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import argparse
import os
import sys
import tempfile

# Both of these must happen before deadinternet.config is imported anywhere:
# CONFIG_PATH is resolved at module import time, and the repo root has to be
# importable for `import app` to work regardless of the working directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# Point the roster at a scratch file. deadinternet/config.py reads this env var
# for exactly this reason, and State.load() returns early when the file does not
# exist, so we get default Settings() and an empty roster. Without it a test run
# could overwrite the real roster -- names, personas and reference transcripts
# for every cloned speaker.
os.environ["DEADINTERNET_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="qwentts-test-"), "roster.json")


def _app():
    from app import DeadInternetApp
    return DeadInternetApp(argparse.Namespace(tts=None, ollama=None, model=None))


def test_build_blocks():
    """The whole page constructs, with every event handler registered."""
    from deadinternet.ui import build

    demo = build(_app())          # never .launch()
    assert demo.blocks, "Blocks tree is empty"

    # Nothing on the build path may write the roster. autosave() only saves
    # inside its inner handler, so a save here means a state.save() has leaked
    # onto the construction path and would rewrite deadinternet.json on every
    # page load.
    assert not os.path.exists(os.environ["DEADINTERNET_CONFIG"]), \
        "building the page wrote the roster file"


def test_roster_untouched_by_import():
    """The real roster is never the config path under test."""
    from deadinternet.config import CONFIG_PATH
    assert CONFIG_PATH == os.environ["DEADINTERNET_CONFIG"]
    assert not CONFIG_PATH.endswith(os.path.join(_ROOT, "deadinternet.json"))


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as e:
            failures += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print("PASS" if not failures else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
