#!/usr/bin/env python3
"""Local voice assistant for the Mac Mini.

Push-to-talk voice front-end for the Knowledge Engine chat. It runs entirely on
the private Mac Mini (no audio ever leaves the machine): it captures the mic,
transcribes speech locally with faster-whisper, then reuses the SAME chat
pipeline as the web dashboard — it POSTs the transcript to /kn/chat/ask and
polls for the answer. The knowledge-worker (already running on this Mac) is what
actually answers (RAG + personal-data domains + conversational memory), so this
process only needs a microphone and the Whisper model; it never talks to Ollama.

Pipeline:
    mic (push-to-talk)
      -> faster-whisper transcribe (local, Spanish)
        -> POST /kn/chat/ask {question, history}
          -> [knowledge-worker answers as usual]
        <- poll GET /kn/chat/{id} until done
      -> print answer (+ optional macOS `say` TTS)

Prerequisites on the Mac:
  brew install portaudio                 # native lib for sounddevice
  pip install -r requirements.txt        # faster-whisper, sounddevice, numpy
  # Ollama + the knowledge-worker must be running for answers to come back.

RAM note (8 GB M1): Ollama keeps qwen3.5:4b (~3.4 GB) + bge-m3 (~1.2 GB)
resident. faster-whisper "medium" at int8 adds ~1.5 GB. That is tight; if you
see swapping, set WHISPER_MODEL=small.

Config via environment variables:
  API_BASE        Backend base URL              (default http://localhost:8000)
  KN_USERNAME     Admin username for login      (required)
  KN_PASSWORD     Admin password for login      (required)
  WHISPER_MODEL   faster-whisper model size     (default medium)
  WHISPER_DEVICE  cpu | cuda | auto             (default cpu)
  WHISPER_COMPUTE CTranslate2 compute type      (default int8)
  WHISPER_LANG    Spoken language code          (default es)
  SAMPLE_RATE     Mic sample rate (Hz)          (default 16000)
  CHAT_TOP_K      RAG neighbours to retrieve    (default 6)
  HISTORY_TURNS   Conversational memory turns   (default 3)
  POLL_INTERVAL   Seconds between answer polls  (default 1.0)
  POLL_TIMEOUT    Max seconds to wait per turn  (default 120)
  SPEAK           1 to speak answers via `say`  (default 1 on macOS)
  SPEAK_VOICE     macOS voice name              (default system default)
"""

import os
import platform
import re
import subprocess
import sys
import time

import numpy as np
import requests
import sounddevice as sd

API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
KN_USERNAME = os.environ.get("KN_USERNAME", "")
KN_PASSWORD = os.environ.get("KN_PASSWORD", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
WHISPER_LANG = os.environ.get("WHISPER_LANG", "es")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
CHAT_TOP_K = int(os.environ.get("CHAT_TOP_K", "6"))
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "3"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.0"))
POLL_TIMEOUT = float(os.environ.get("POLL_TIMEOUT", "120"))
_IS_MAC = platform.system() == "Darwin"
SPEAK = os.environ.get("SPEAK", "1" if _IS_MAC else "0") == "1"
SPEAK_VOICE = os.environ.get("SPEAK_VOICE", "")

# Strip chat citation markers ([U12], 📊) so the spoken answer sounds natural.
_SPEAK_CLEAN = re.compile(r"\[U\d+[^\]]*\]|📊")


class VoiceError(Exception):
    pass


def login():
    """Authenticate against the backend and return a bearer token."""
    if not KN_USERNAME or not KN_PASSWORD:
        raise VoiceError("KN_USERNAME and KN_PASSWORD must be set")
    r = requests.post(
        f"{API_BASE}/auth/login",
        data={"username": KN_USERNAME, "password": KN_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise VoiceError(f"Login response missing token: {data}")
    return token


def make_session(token):
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def record_push_to_talk():
    """Record mono audio from the default mic until the user presses Enter.

    Returns a float32 numpy array at SAMPLE_RATE, or an empty array if nothing
    was captured. Uses a callback so recording length is unbounded (push-to-talk)
    instead of a fixed duration.
    """
    frames = []

    def callback(indata, _frames, _time, status):
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        frames.append(indata.copy())

    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
    )
    with stream:
        input()  # blocks here while the mic records; Enter stops it
    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(frames, axis=0).flatten()


def transcribe(model, audio):
    """Run faster-whisper on an in-memory float32 array. Returns cleaned text."""
    if audio.size == 0:
        return ""
    segments, _info = model.transcribe(
        audio,
        language=WHISPER_LANG,
        beam_size=5,
        vad_filter=True,  # drop leading/trailing silence for shorter, cleaner input
    )
    return "".join(seg.text for seg in segments).strip()


def ask_chat(session, question, history):
    """Queue a question and poll until the worker answers. Returns the answer."""
    r = session.post(
        f"{API_BASE}/kn/chat/ask",
        json={"question": question, "top_k": CHAT_TOP_K, "history": history},
        timeout=30,
    )
    r.raise_for_status()
    chat_id = r.json().get("chat_id")
    if chat_id is None:
        raise VoiceError(f"ask response missing chat_id: {r.text}")

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        pr = session.get(f"{API_BASE}/kn/chat/{chat_id}", timeout=30)
        pr.raise_for_status()
        data = pr.json()
        status = data.get("status")
        if status == "done":
            return data.get("answer") or ""
        if status == "error":
            raise VoiceError(data.get("error") or "worker reported an error")
    raise VoiceError("timed out waiting for the answer")


def speak(text):
    """Speak the answer aloud on macOS via the built-in `say` command."""
    if not (SPEAK and _IS_MAC):
        return
    clean = _SPEAK_CLEAN.sub("", text).strip()
    if not clean:
        return
    cmd = ["say"]
    if SPEAK_VOICE:
        cmd += ["-v", SPEAK_VOICE]
    cmd.append(clean)
    try:
        subprocess.run(cmd, check=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] TTS failed: {e}", file=sys.stderr)


def load_whisper():
    """Load the faster-whisper model once (kept resident for fast turns)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise VoiceError(
            "faster-whisper is not installed. Run: pip install -r requirements.txt"
        ) from e
    print(f"loading whisper model '{WHISPER_MODEL}' "
          f"(device={WHISPER_DEVICE}, compute={WHISPER_COMPUTE})...")
    return WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                        compute_type=WHISPER_COMPUTE)


def main():
    print(f"voice-assistant starting: api={API_BASE} "
          f"whisper={WHISPER_MODEL} lang={WHISPER_LANG} speak={SPEAK}")
    token = login()
    session = make_session(token)
    model = load_whisper()
    history = []  # rolling [{question, answer}] for conversational memory

    print("\nListo. Pulsa Enter para hablar, luego Enter otra vez para parar. "
          "Ctrl+C para salir.\n")
    while True:
        try:
            input("[Enter] para grabar > ")
            print("  Grabando... (Enter para parar)")
            audio = record_push_to_talk()
            print("  Transcribiendo...")
            question = transcribe(model, audio)
            if not question:
                print("  (no he entendido nada, prueba otra vez)\n")
                continue
            print(f"  Tú: {question}")
            print("  Pensando...")
            answer = ask_chat(session, question, history[-HISTORY_TURNS:])
            print(f"\n  Asistente: {answer}\n")
            speak(answer)
            history.append({"question": question, "answer": answer})
            history = history[-HISTORY_TURNS:]
        except KeyboardInterrupt:
            print("\nAdiós.")
            return
        except VoiceError as e:
            print(f"  [error] {e}\n")
        except requests.RequestException as e:
            print(f"  [red] problema de conexión: {e}\n")


if __name__ == "__main__":
    try:
        main()
    except VoiceError as e:
        print(f"fatal: {e}", file=sys.stderr)
        sys.exit(1)
