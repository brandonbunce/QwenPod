"""Choosing where speech is generated.

The URL is the whole setting: there is no separate "am I remote" flag, so what
is under test is that everything which only makes sense on this machine --
autostart, Restart, the backend radio -- reads it the same way, and that
switching servers re-points the client and drops a voice cache that belonged
to the old one.

No tts-server is started: the one code path that would launch one is stubbed,
and the remote address in these tests answers nothing.

    .venv-app/bin/python deadinternet/tests/test_ttsserver.py
    .venv-app/bin/python -m pytest deadinternet/tests/
"""
import argparse
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# Before deadinternet.config is imported: see test_ui_build.py.
os.environ["DEADINTERNET_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="qwentts-test-"), "roster.json")

from deadinternet.config import (LOCAL_TTS_URL, TTS_HERE,  # noqa: E402
                                 TTS_REMOTE, is_local_tts, normalize_tts_url)

REMOTE = "http://voice.example.invalid:8080"


def _app():
    from app import DeadInternetApp
    app = DeadInternetApp(argparse.Namespace(tts=None, ollama=None, model=None))
    # Nothing in these tests may launch a server or reach the network.
    app.started = []
    app.start_tts_boot = lambda autostart=True: app.started.append(autostart)
    app.tts_alive = lambda timeout=2.0: False
    # The app log is a real file in the repo; a test has no business in it.
    app.log = lambda msg: None
    return app


def test_normalize():
    assert normalize_tts_url("http://voice.example.invalid/") == "http://voice.example.invalid"
    assert normalize_tts_url("  voice.example.invalid  ") == "http://voice.example.invalid"
    assert normalize_tts_url("https://a.b:8443") == "https://a.b:8443"
    for bad in ("", "   ", "ftp://x", "http:///v1", "http://a/v1/audio", "http://a?x=1"):
        try:
            normalize_tts_url(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_is_local():
    assert is_local_tts(LOCAL_TTS_URL)
    assert is_local_tts("http://localhost:8080")
    assert not is_local_tts("http://voice.example.invalid")
    # A hostname that merely starts with the loopback spelling is not it.
    assert not is_local_tts("http://localhost.evil.example")


def test_switch_to_remote_and_back():
    app = _app()
    ok, msg = app.set_tts_server(TTS_REMOTE, "voice.example.invalid:8080/")
    s = app.state.settings
    # Nothing answered there, so this reports a problem -- but it is saved and
    # in use, because the alternative is silently speaking through the old one.
    assert not ok and "voice.example.invalid" in msg
    assert s.tts_url == REMOTE == s.tts_remote_url
    assert app.tts.base_url == REMOTE
    assert app.started == []          # nothing to start on this machine

    # Registrations belonged to the old server.
    app.tts._registered.add("Big Dave")
    ok, msg = app.set_tts_server(TTS_HERE, "ignored")
    assert ok and s.tts_url == LOCAL_TTS_URL
    assert app.tts.base_url == LOCAL_TTS_URL
    assert not app.tts._registered
    assert app.started == [True]      # local and down: autostart it
    # The remote address is remembered for switching back.
    assert s.tts_remote_url == REMOTE


def test_rejected_url_changes_nothing():
    app = _app()
    ok, msg = app.set_tts_server(TTS_REMOTE, "ftp://nope")
    assert not ok and "http://" in msg
    assert app.state.settings.tts_url == LOCAL_TTS_URL
    assert app.tts.base_url == LOCAL_TTS_URL


def test_local_only_actions_refuse_a_remote_server():
    app = _app()
    app.set_tts_server(TTS_REMOTE, REMOTE)

    ok, msg = app._start_tts_server()
    assert not ok and REMOTE in msg
    ok, msg = app.restart_tts_server("cpu")
    assert not ok and REMOTE in msg
    # The refusal must not have changed the backend on the way out.
    assert app.state.settings.tts_device == "gpu"


def test_voice_name_resolution():
    from deadinternet.config import Speaker
    assert Speaker(name="Bjork").voice_name() == "Bjork"
    assert Speaker(name="Warden (Deadlock)",
                   server_voice="deadlock_warden").voice_name() == "deadlock_warden"
    # Whitespace is not a server name.
    assert Speaker(name="Bjork", server_voice="  ").voice_name() == "Bjork"


def _client(base_url, live=()):
    from deadinternet.tts import TTSClient
    c = TTSClient(base_url)
    c.server_voices = lambda: list(live)
    c.deleted = []
    c.http.delete = lambda url, timeout=30: c.deleted.append(url)
    return c


def test_remote_server_is_read_only():
    from deadinternet.config import Speaker
    c = _client(REMOTE, live=["deadlock_warden"])
    assert c.read_only

    ok, msg = c.register("anything", "/nonexistent.wav")
    assert not ok and "managed on that server" in msg

    # A voice that is there is used; one that is not is reported, not uploaded.
    problems = c.ensure_registered([
        Speaker(name="Warden (Deadlock)", server_voice="deadlock_warden"),
        Speaker(name="Bjork", ref_wav="/nonexistent.wav"),
    ])
    assert problems == ["Bjork: no voice called 'Bjork' on " + REMOTE]
    assert "deadlock_warden" in c._registered

    # Deleting a speaker here must not delete the voice over there.
    c.forget("deadlock_warden")
    assert c.deleted == [] and "deadlock_warden" not in c._registered


def test_local_server_still_uploads():
    from deadinternet.config import Speaker
    c = _client(LOCAL_TTS_URL, live=[])
    assert not c.read_only
    uploaded = []
    c.register = lambda name, wav, text="", force=False: (
        uploaded.append((name, wav)) or (True, "registered"))
    c.ensure_registered([Speaker(name="Big Dave", server_voice="irl_big_dave",
                                 ref_wav="/tmp/a.wav")])
    # Even locally, the server name is the one registered -- otherwise the
    # voice would be uploaded under one name and asked for under another.
    assert uploaded == [("irl_big_dave", "/tmp/a.wav")]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
