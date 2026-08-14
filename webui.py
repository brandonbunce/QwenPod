"""Gradio front-end for the qwentts.cpp tts-server API.

The server is the only thing doing inference here; this file just talks HTTP to
it. Start the server first (see SERVER_CMD in the README section below), then:

    ~/Documents/Qwen3TTS/.venv/bin/python webui.py

Endpoints used (from src/tts-server.h):
    GET    /v1/models               -> model id
    GET    /v1/audio/voices         -> model speakers + registered clones
    POST   /v1/audio/voices         -> {name, ref_text, wav_b64}
    DELETE /v1/audio/voices/{name}
    POST   /v1/audio/speech         -> OpenAI-compatible TTS

Note: the server takes its language from the --lang startup flag. There is no
per-request language field, so this UI does not offer one.
"""
import base64
import io
import os

import gradio as gr
import requests
import soundfile as sf

API = os.environ.get("QWENTTS_API", "http://127.0.0.1:8080")
TIMEOUT = 600


def _err(resp):
    """Pull the message out of the server's OpenAI-style error envelope."""
    try:
        return resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"


def model_id():
    r = requests.get(f"{API}/v1/models", timeout=10)
    r.raise_for_status()
    return r.json()["data"][0]["id"]


def list_voices():
    r = requests.get(f"{API}/v1/audio/voices", timeout=10)
    r.raise_for_status()
    # Shape: {"voices":[{"name":"x","kind":"registered"|"speaker"}, ...]}
    # Model speakers and registered clones share the one list, tagged by "kind".
    names = []
    for v in r.json().get("voices", []):
        names.append(v.get("name") or v.get("speaker") if isinstance(v, dict) else v)
    return sorted({n for n in names if n})


def refresh_voices():
    try:
        return gr.update(choices=list_voices()), "Voices refreshed."
    except Exception as e:
        return gr.update(), f"Could not reach {API}: {e}"


def register_clone(name, ref_audio, ref_text):
    if not name or not name.strip():
        return gr.update(), "Give the voice a name."
    if not ref_audio:
        return gr.update(), "Upload a reference clip."

    # The server decodes the payload at 24 kHz mono; normalise here so any
    # input Gradio hands us (mp3, stereo, 44.1k) is accepted.
    data, sr = sf.read(ref_audio, always_2d=True)
    mono = data.mean(axis=1)
    buf = io.BytesIO()
    sf.write(buf, mono, sr, format="WAV", subtype="PCM_16")

    payload = {
        "name": name.strip(),
        "wav_b64": base64.b64encode(buf.getvalue()).decode(),
        "ref_text": (ref_text or "").strip(),
    }
    r = requests.post(f"{API}/v1/audio/voices", json=payload, timeout=TIMEOUT)
    if r.status_code != 200:
        return gr.update(), f"Registration failed - {_err(r)}"

    mode = "ICL (with transcript)" if payload["ref_text"] else "speaker-embedding only"
    return (
        gr.update(choices=list_voices(), value=payload["name"]),
        f"Registered '{payload['name']}' - {mode}. Pick it in the Generate tab.",
    )


def delete_clone(name):
    if not name:
        return gr.update(), "Nothing selected."
    r = requests.delete(f"{API}/v1/audio/voices/{name}", timeout=30)
    if r.status_code != 200:
        return gr.update(), f"Delete failed - {_err(r)}"
    return gr.update(choices=list_voices(), value=None), f"Removed '{name}'."


def synth(text, voice, instructions, temperature, top_k, top_p, rep_pen, seed, max_new):
    if not text or not text.strip():
        return None, "Enter some text."
    if not voice:
        return None, "Pick a voice."

    payload = {
        "model": model_id(),
        "input": text,
        "voice": voice,
        "response_format": "wav",  # without this the server returns headerless PCM
        "temperature": temperature,
        "top_k": int(top_k),
        "top_p": top_p,
        "repetition_penalty": rep_pen,
        "max_new_tokens": int(max_new),
    }
    if instructions and instructions.strip():
        payload["instructions"] = instructions.strip()
    if int(seed) >= 0:
        payload["seed"] = int(seed)

    r = requests.post(f"{API}/v1/audio/speech", json=payload, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, f"Generation failed - {_err(r)}"

    out = "/tmp/qwentts_webui_out.wav"
    with open(out, "wb") as f:
        f.write(r.content)
    info = sf.info(out)
    return out, f"{info.duration:.2f}s @ {info.samplerate} Hz"


try:
    VOICES = list_voices()
    BANNER = f"Connected to {API} - model `{model_id()}`"
except Exception as e:
    VOICES = []
    BANNER = f"**Cannot reach {API}** - start tts-server first. ({e})"

with gr.Blocks(title="qwentts.cpp") as demo:
    gr.Markdown(f"# qwentts.cpp\n{BANNER}")

    with gr.Tab("Generate"):
        with gr.Row():
            with gr.Column():
                text = gr.Textbox(label="Text", lines=4, value="Hello, this is a test.")
                voice = gr.Dropdown(choices=VOICES, label="Voice", value=VOICES[0] if VOICES else None)
                instructions = gr.Textbox(
                    label="Style instruction (CustomVoice/VoiceDesign only, rejected by Base)",
                    placeholder="e.g. speak slowly and warmly",
                )
                with gr.Accordion("Sampling", open=False):
                    temperature = gr.Slider(0.1, 2.0, value=0.9, step=0.05, label="Temperature")
                    top_k = gr.Slider(0, 200, value=50, step=1, label="Top-k (0 disables)")
                    top_p = gr.Slider(0.1, 1.0, value=1.0, step=0.05, label="Top-p")
                    rep_pen = gr.Slider(1.0, 2.0, value=1.05, step=0.01, label="Repetition penalty")
                    seed = gr.Number(value=-1, precision=0, label="Seed (-1 = random)")
                    max_new = gr.Number(value=2048, precision=0, label="Max new frames")
                go = gr.Button("Generate", variant="primary")
            with gr.Column():
                audio_out = gr.Audio(label="Output", type="filepath")
                status = gr.Markdown()

        go.click(
            synth,
            [text, voice, instructions, temperature, top_k, top_p, rep_pen, seed, max_new],
            [audio_out, status],
        )

    with gr.Tab("Clone a voice"):
        gr.Markdown(
            "Register a voice from a reference clip. Supplying the transcript enables "
            "ICL mode, which tracks the reference more closely; leaving it blank falls "
            "back to speaker-embedding only. Cloning requires a **Base** model."
        )
        with gr.Row():
            with gr.Column():
                clone_name = gr.Textbox(label="Voice name", placeholder="my-voice")
                ref_audio = gr.Audio(label="Reference clip", type="filepath")
                ref_text = gr.Textbox(
                    label="Reference transcript (optional)",
                    lines=2,
                    placeholder="exact words spoken in the clip",
                )
                with gr.Row():
                    register_btn = gr.Button("Register", variant="primary")
                    refresh_btn = gr.Button("Refresh list")
            with gr.Column():
                existing = gr.Dropdown(choices=VOICES, label="Registered voices")
                delete_btn = gr.Button("Delete selected")
                clone_status = gr.Markdown()

        register_btn.click(register_clone, [clone_name, ref_audio, ref_text], [voice, clone_status])
        register_btn.click(lambda: gr.update(choices=list_voices()), None, existing)
        refresh_btn.click(refresh_voices, None, [voice, clone_status])
        refresh_btn.click(lambda: gr.update(choices=list_voices()), None, existing)
        delete_btn.click(delete_clone, existing, [existing, clone_status])
        delete_btn.click(lambda: gr.update(choices=list_voices()), None, voice)

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)
