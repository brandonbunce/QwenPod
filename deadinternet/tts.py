"""qwentts.cpp tts-server client.

Voices are registered once at boot and cached by name; registration is the
expensive step (it runs the speaker encoder) and the server keeps them only
in memory, so a server restart means re-registering the whole roster.
"""
import base64
import io
import os
import threading

import requests
import soundfile as sf

from .pipeline import NULL_PIPELINE, TTS


def upload_wav(wav_path: str) -> bytes:
    """The exact bytes register() sends for a reference clip.

    Mono 16-bit WAV at the clip's own rate; the server resamples to 24 kHz.
    A function of its own so voiceexport.py reproduces what the server heard
    from the same code, rather than from a copy that could drift.
    """
    data, sr = sf.read(wav_path, always_2d=True)
    buf = io.BytesIO()
    sf.write(buf, data.mean(axis=1), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class TTSClient:
    def __init__(self, base_url: str, timeout: int = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self._registered = set()
        self._lock = threading.Lock()
        # Set by app.py. Synthesis is the single most likely thing to be the
        # reason nothing is coming out, so it is timed at the client rather
        # than at each of the director's five call sites.
        self.pipeline = NULL_PIPELINE

    # ---- introspection -----------------------------------------------
    def model_id(self) -> str:
        r = self.http.get(f"{self.base_url}/v1/models", timeout=10)
        r.raise_for_status()
        return r.json()["data"][0]["id"]

    def server_voices(self):
        r = self.http.get(f"{self.base_url}/v1/audio/voices", timeout=10)
        r.raise_for_status()
        out = []
        for v in r.json().get("voices", []):
            out.append((v.get("name") or v.get("speaker")) if isinstance(v, dict) else v)
        return [v for v in out if v]

    @staticmethod
    def _error(resp) -> str:
        try:
            return resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            return f"HTTP {resp.status_code}: {resp.text[:200]}"

    # ---- registration --------------------------------------------------
    def register(self, name: str, wav_path: str, ref_text: str = "", force: bool = False):
        """Register a clone. Returns (ok, message)."""
        with self._lock:
            if name in self._registered and not force:
                return True, "cached"
        if not wav_path or not os.path.exists(wav_path):
            return False, f"reference clip missing: {wav_path}"

        payload = {
            "name": name,
            "wav_b64": base64.b64encode(upload_wav(wav_path)).decode(),
            "ref_text": ref_text or "",
        }
        try:
            r = self.http.post(f"{self.base_url}/v1/audio/voices", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            return False, f"tts-server unreachable: {e}"
        if r.status_code != 200:
            return False, self._error(r)
        with self._lock:
            self._registered.add(name)
        return True, "registered"

    def ensure_registered(self, speakers) -> list:
        """Re-register every speaker that the server doesn't already know."""
        try:
            live = set(self.server_voices())
        except requests.RequestException as e:
            return [f"tts-server unreachable: {e}"]
        problems = []
        for sp in speakers:
            if sp.name in live:
                with self._lock:
                    self._registered.add(sp.name)
                continue
            ok, msg = self.register(sp.name, sp.ref_wav, sp.ref_text, force=True)
            if not ok:
                problems.append(f"{sp.name}: {msg}")
        return problems

    def forget(self, name: str):
        with self._lock:
            self._registered.discard(name)
        try:
            self.http.delete(f"{self.base_url}/v1/audio/voices/{name}", timeout=30)
        except requests.RequestException:
            pass

    # ---- synthesis -----------------------------------------------------
    def synth(self, text: str, voice: str, **gen) -> bytes:
        """Return WAV bytes. response_format is mandatory -- without it the
        server replies with headerless PCM."""
        with self.pipeline.track(TTS):
            return self._synth(text, voice, **gen)

    def _synth(self, text: str, voice: str, **gen) -> bytes:
        payload = {
            "model": self.model_id(),
            "input": text,
            "voice": voice,
            "response_format": "wav",
        }
        payload.update({k: v for k, v in gen.items() if v is not None})
        r = self.http.post(f"{self.base_url}/v1/audio/speech", json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(self._error(r))
        return r.content
