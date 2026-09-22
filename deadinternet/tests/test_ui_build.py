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


def _visible(demo, comp):
    """A component's visibility as the page will serve it."""
    return demo.get_config_file()["components"][
        [c["id"] for c in demo.get_config_file()["components"]].index(comp._id)
    ]["props"].get("visible", True)


def _local_only_handles(demo):
    """The Testing tab and the Backend/Restart/Stop box, found by their ids
    and class rather than by handle: build() keeps them on a private bag."""
    tab = box = None
    for b in demo.blocks.values():
        if getattr(b, "id", None) == "testing" and type(b).__name__ == "Tab":
            tab = b
    # The box is the only Column whose visibility is set at build time.
    cols = [b for b in demo.blocks.values()
            if type(b).__name__ == "Column" and b.visible is False]
    return tab, cols


def test_testing_tab_hidden_when_remote():
    """Everything on Testing speaks through tts-server on this machine, so the
    tab and the Backend/Restart/Stop box leave the page while speech is
    pointed at a remote server, and come back when it is local."""
    from deadinternet.ui import build

    app = _app()
    app.state.settings.tts_url = "http://voice.example.internal:8080"
    demo = build(app)
    tab, hidden_cols = _local_only_handles(demo)
    assert tab is not None, "no Testing tab"
    assert _visible(demo, tab) is False, "Testing visible while remote"
    assert hidden_cols, "local-only box not hidden while remote"

    app = _app()
    app.state.settings.tts_url = "http://127.0.0.1:8080"
    demo = build(app)
    tab, hidden_cols = _local_only_handles(demo)
    assert _visible(demo, tab) is True, "Testing hidden while local"
    assert not hidden_cols, "local-only box hidden while local"


def test_help_split():
    """help() joins the two halves with the separator exactly once, and a
    tooltip-less string carries none."""
    from deadinternet.ui.help import SEP, help

    assert help("a", "b").count(SEP) == 1
    assert help("a", "b") == "a" + SEP + "b"
    assert SEP not in help("a")
    assert help("", "b").startswith(SEP)
    assert help("a", "") == "a"


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
