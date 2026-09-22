"""whisper-server on demand: up for a Record session, down after it idles.

No whisper-server is started or stopped: both are stubbed and recorded.

    .venv-app/bin/python deadinternet/tests/test_whisper_ondemand.py
"""
import argparse
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
os.environ["DEADINTERNET_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="qwentts-test-"), "roster.json")


def _app(alive=False):
    from app import DeadInternetApp
    app = DeadInternetApp(argparse.Namespace(tts=None, ollama=None, model=None))
    app.calls = []
    app.up = alive
    app.whisper_alive = lambda timeout=1.5: app.up
    def start():
        app.calls.append("start"); app.up = True
        return True, "whisper-server up."
    def stop():
        app.calls.append("stop"); app.up = False
        return True, "Stopped whisper-server."
    app.start_whisper_server = start
    app.stop_whisper_server = stop
    app.log = lambda msg: None
    return app


def test_boot_does_not_start_it():
    app = _app()
    app.boot_whisper()
    assert app.calls == []
    app.state.settings.whisper_server_at_boot = True
    app.boot_whisper()
    assert app.calls == ["start"]


def test_record_starts_and_idle_stops():
    app = _app()
    app.state.settings.whisper_idle_seconds = 0.05
    msg = app.mic_session(True)
    assert app.calls == ["start"] and "up" in msg
    assert app._whisper_idle is None          # no clock while recording
    assert app.mic_session(False) == ""
    assert app._whisper_idle is not None      # Stop starts the clock
    time.sleep(0.3)
    assert app.calls == ["start", "stop"]


def test_record_again_cancels_the_clock():
    app = _app(alive=True)
    app.state.settings.whisper_idle_seconds = 0.05
    app.mic_session(False)
    app.mic_session(True)                     # pressed Record before it fired
    time.sleep(0.2)
    assert app.calls == []                    # already up, never stopped


def test_file_clip_outside_a_session_starts_and_arms():
    app = _app()
    app.state.settings.whisper_idle_seconds = 0.05
    assert app.whisper_for_clip() is True
    app.clip_done()
    time.sleep(0.3)
    assert app.calls == ["start", "stop"]


def test_off_means_off():
    app = _app()
    app.state.settings.whisper_server = False
    app.boot_whisper()
    assert app.mic_session(True) == ""
    assert app.whisper_for_clip() is False
    assert app.calls == []


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
