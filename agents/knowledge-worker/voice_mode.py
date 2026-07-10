#!/usr/bin/env python3
"""Always-on wake-word voice mode for the knowledge-worker (Mac Mini).

Runs as a background thread inside worker.py when VOICE_ENABLED=1. It keeps a
tiny wake-word model (openWakeWord) listening on the mic continuously. When it
hears the wake word it:

  1. sets a pause Event so the worker's main loop stops claiming background
     jobs (extraction / embeddings / web chats) — giving the spoken request
     exclusive use of the local LLM;
  2. records the command with simple energy-based endpointing (stops on
     silence);
  3. transcribes locally with faster-whisper (loaded lazily on first use);
  4. answers IN-PROCESS via the worker's shared `answer_question` engine
     (same router + RAG + personal-data domains + conversational memory);
  5. prints and optionally speaks the answer (macOS `say`);
  6. clears the pause Event so the worker resumes background work.

Nothing here talks to Ollama directly or exposes anything to the network — it
reuses the worker's authenticated session via the `get_session` callable.

Wake word: openWakeWord ships only a few PRETRAINED models (hey_jarvis, alexa,
hey_mycroft, ...). A custom word like "mini" needs a trained model file; point
WAKEWORD_MODEL at its .onnx path. Until then, use a built-in name to test.

Config via environment variables:
  WAKEWORD_MODEL     Built-in name or path to .onnx/.tflite (default hey_jarvis)
  WAKEWORD_THRESHOLD Activation score 0..1                  (default 0.5)
  WAKEWORD_FRAMEWORK onnx | tflite                          (default onnx)
  WHISPER_MODEL      faster-whisper size                    (default medium)
  WHISPER_DEVICE     cpu | cuda | auto                      (default cpu)
  WHISPER_COMPUTE    CTranslate2 compute type               (default int8)
  WHISPER_LANG       Spoken language code                   (default es)
  SAMPLE_RATE        Mic sample rate (Hz)                   (default 16000)
  VOICE_HISTORY_TURNS   Conversational memory turns         (default 3)
  VOICE_SILENCE_MS      Silence to end a command (ms)       (default 900)
  VOICE_MAX_SECONDS     Hard cap per command                (default 12)
  VOICE_SILENCE_RMS     Energy below this counts as silence (default 0.012)
  VOICE_MIN_SPEECH_MS   Min speech before endpointing (ms)  (default 300)
  CHAT_TOP_K            RAG neighbours to retrieve          (default 6)
  AUDIO_INPUT_DEVICE    Mic index/name (empty = system default)
  SHOW_POPUP         1 to serve a fullscreen answer page     (default 1 on macOS)
  POPUP_PORT         Local port for the answer page          (default 8765)
  POPUP_OPEN         1 to auto-open the browser once          (default 1)
  SPEAK              1 to speak answers via `say`           (default 1 on macOS)
  SPEAK_VOICE        macOS voice name                       (default system)
"""

import os
import platform
import re
import subprocess
import sys
import threading
import time
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WAKEWORD_MODEL = os.environ.get("WAKEWORD_MODEL", "hey_jarvis")
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))
WAKEWORD_FRAMEWORK = os.environ.get("WAKEWORD_FRAMEWORK", "onnx")
WAKEWORD_DEBUG = os.environ.get("WAKEWORD_DEBUG", "0") == "1"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
WHISPER_LANG = os.environ.get("WHISPER_LANG", "es")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
VOICE_HISTORY_TURNS = int(os.environ.get("VOICE_HISTORY_TURNS", "3"))
VOICE_SILENCE_MS = int(os.environ.get("VOICE_SILENCE_MS", "900"))
VOICE_MAX_SECONDS = float(os.environ.get("VOICE_MAX_SECONDS", "12"))
VOICE_SILENCE_RMS = float(os.environ.get("VOICE_SILENCE_RMS", "0.012"))
VOICE_MIN_SPEECH_MS = int(os.environ.get("VOICE_MIN_SPEECH_MS", "300"))
CHAT_TOP_K = int(os.environ.get("CHAT_TOP_K", "6"))
# Mic selection: leave empty to use the system default input device, or set to
# the device index / name shown by `python -m sounddevice`.
_dev = os.environ.get("AUDIO_INPUT_DEVICE", "").strip()
AUDIO_INPUT_DEVICE = (int(_dev) if _dev.lstrip("-").isdigit() else _dev) or None
_IS_MAC = platform.system() == "Darwin"
SPEAK = os.environ.get("SPEAK", "1" if _IS_MAC else "0") == "1"
SPEAK_VOICE = os.environ.get("SPEAK_VOICE", "")
# Fullscreen pop-up showing the answer text, served as a local web page (works
# on any macOS; avoids Tkinter's macOS-version dependency). Opened once in the
# default browser; the page polls and updates on each answer.
SHOW_POPUP = os.environ.get("SHOW_POPUP", "1" if _IS_MAC else "0") == "1"
POPUP_PORT = int(os.environ.get("POPUP_PORT", "8765"))
POPUP_OPEN = os.environ.get("POPUP_OPEN", "1") == "1"

_FRAME = 1280  # openWakeWord expects 80 ms frames at 16 kHz
_FRAME_MS = 80
_SPEAK_CLEAN = re.compile(r"\[U\d+[^\]]*\]|📊")

# Fullscreen kiosk page: dark background, huge centered text, polls /answer.
_POPUP_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Asistente</title>
<style>
  html,body{margin:0;height:100%;background:#0b0f1a;color:#e8eef7;
    font-family:-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}
  #wrap{position:fixed;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;padding:5vw;box-sizing:border-box;
    text-align:center;cursor:pointer}
  #head{color:#6ea8fe;font-size:2.4vw;letter-spacing:.35em;font-weight:700;
    text-transform:uppercase;margin-bottom:3vh}
  #q{color:#8a93a6;font-size:2.2vw;margin-bottom:3vh;max-width:88vw}
  #a{font-size:4vw;line-height:1.35;font-weight:600;max-width:90vw}
  #idle{color:#5b6472;font-size:3vw}
  .hint{position:fixed;bottom:2vh;width:100%;text-align:center;color:#3a4150;
    font-size:1.4vw}
</style></head>
<body><div id="wrap" onclick="fs()">
  <div id="head">Asistente</div>
  <div id="q"></div>
  <div id="a"><span id="idle">Escuchando\u2026 di la palabra de activaci\u00f3n</span></div>
</div>
<div class="hint">clic para pantalla completa</div>
<script>
  let last=0;
  function fs(){const e=document.documentElement;
    if(e.requestFullscreen)e.requestFullscreen().catch(()=>{});}
  async function poll(){try{
    const r=await fetch('/answer',{cache:'no-store'});const d=await r.json();
    if(d.ts&&d.ts!==last){last=d.ts;
      document.getElementById('q').textContent=d.question||'';
      document.getElementById('a').textContent=d.text||'';}
  }catch(e){}finally{setTimeout(poll,800);}}
  poll();
</script></body></html>"""


class VoiceMode:
    """Background wake-word listener + local STT that answers in-process."""

    def __init__(self, pause_event, get_session, answer_fn, interrupt_event=None):
        self._pause = pause_event
        self._get_session = get_session
        self._answer = answer_fn
        self._interrupt = interrupt_event
        self._history = []
        self._whisper = None
        self._stop = threading.Event()
        self._httpd = None
        self._popup_state = {"question": "", "text": "", "ts": 0}
        self._thread = threading.Thread(target=self._run, name="voice", daemon=True)

    def start(self):
        # Import heavy/native deps here so the worker runs fine without them
        # when voice is disabled.
        global np, sd
        import numpy as np  # noqa: F401
        import sounddevice as sd  # noqa: F401
        import openwakeword
        from openwakeword.model import Model

        # Ensure the shared feature-extractor models are present (first run only).
        try:
            openwakeword.utils.download_models()
        except Exception as e:  # noqa: BLE001
            print(f"[voice] could not pre-download models: {e}", file=sys.stderr)

        self._wake_key = os.path.splitext(os.path.basename(WAKEWORD_MODEL))[0]
        self._oww = Model(wakeword_models=[WAKEWORD_MODEL],
                          inference_framework=WAKEWORD_FRAMEWORK)
        print(f"[voice] wake word '{self._wake_key}' "
              f"(threshold={WAKEWORD_THRESHOLD}, whisper={WHISPER_MODEL})")
        if SHOW_POPUP:
            self._start_popup_server()
        self._thread.start()

    # -- internals ---------------------------------------------------------- #

    def _start_popup_server(self):
        """Serve the fullscreen kiosk page on localhost and open it once."""
        state = self._popup_state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence request logging
                pass

            def do_GET(self):
                if self.path.startswith("/answer"):
                    body = json.dumps(state).encode("utf-8")
                    ctype = "application/json"
                else:
                    body = _POPUP_HTML.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", POPUP_PORT), Handler)
        except OSError as e:  # noqa: BLE001
            print(f"[voice] popup server disabled (port {POPUP_PORT}: {e})",
                  file=sys.stderr)
            self._httpd = None
            return
        threading.Thread(target=self._httpd.serve_forever,
                         name="voice-popup", daemon=True).start()
        url = f"http://localhost:{POPUP_PORT}"
        print(f"[voice] answer screen at {url}")
        if POPUP_OPEN and _IS_MAC:
            try:
                subprocess.Popen(["open", url])
            except Exception as e:  # noqa: BLE001
                print(f"[voice] could not open browser: {e}", file=sys.stderr)

    def _load_whisper(self):
        if self._whisper is not None:
            return self._whisper
        from faster_whisper import WhisperModel
        print(f"[voice] loading whisper '{WHISPER_MODEL}' "
              f"(device={WHISPER_DEVICE}, compute={WHISPER_COMPUTE})...")
        self._whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                     compute_type=WHISPER_COMPUTE)
        return self._whisper

    def _rms(self, frame_int16):
        f = frame_int16.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(f * f))) if f.size else 0.0

    def _run(self):
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="int16", blocksize=_FRAME,
                                device=AUDIO_INPUT_DEVICE)
        stream.start()
        print("[voice] escuchando... (di la palabra de activación)")
        try:
            while not self._stop.is_set():
                frame, _ = stream.read(_FRAME)
                frame = frame.flatten()
                scores = self._oww.predict(frame)
                score = scores.get(self._wake_key, 0.0)
                if WAKEWORD_DEBUG and score >= 0.1:
                    print(f"[voice] score={score:.2f} (rms={self._rms(frame):.3f})")
                if score >= WAKEWORD_THRESHOLD:
                    self._handle_activation(stream)
        except Exception as e:  # noqa: BLE001
            print(f"[voice] listener stopped: {e}", file=sys.stderr)
        finally:
            stream.stop()
            stream.close()

    def stop(self):
        """Signal the listener to finish its current frame and close the audio
        stream cleanly (avoids a native segfault on Ctrl+C)."""
        self._stop.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _handle_activation(self, stream):
        self._pause.set()  # main loop yields Ollama to us
        if self._interrupt is not None:
            self._interrupt.set()  # abort any in-flight background extraction now
        try:
            print("[voice] activado, escuchando la orden...")
            audio = self._record_command(stream)
            model = self._load_whisper()
            segments, _info = model.transcribe(
                audio, language=WHISPER_LANG, beam_size=5, vad_filter=True)
            question = "".join(s.text for s in segments).strip()
            if not question:
                print("[voice] (no he entendido nada)")
                return
            print(f"[voice] Tú: {question}")
            session = self._get_session()
            answer, _ctx, intent = self._answer(
                session, question, self._history[-VOICE_HISTORY_TURNS:], CHAT_TOP_K)
            print(f"[voice] intent -> {intent}")
            print(f"[voice] Asistente: {answer}")
            self._show_popup(question, answer)
            self._speak(answer)
            self._history.append({"question": question, "answer": answer})
            self._history = self._history[-VOICE_HISTORY_TURNS:]
        except Exception as e:  # noqa: BLE001
            print(f"[voice] error answering: {e}", file=sys.stderr)
        finally:
            if self._interrupt is not None:
                self._interrupt.clear()  # let background extraction resume
            self._oww.reset()  # clear wake-word buffers to avoid re-trigger
            self._pause.clear()

    def _record_command(self, stream):
        """Capture from the open stream until the user stops talking."""
        frames = []
        speech_ms = 0
        silence_ms = 0
        started = time.time()
        while True:
            frame, _ = stream.read(_FRAME)
            frame = frame.flatten()
            frames.append(frame)
            if self._rms(frame) >= VOICE_SILENCE_RMS:
                speech_ms += _FRAME_MS
                silence_ms = 0
            else:
                silence_ms += _FRAME_MS
            if speech_ms >= VOICE_MIN_SPEECH_MS and silence_ms >= VOICE_SILENCE_MS:
                break
            if time.time() - started >= VOICE_MAX_SECONDS:
                break
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return audio

    def _speak(self, text):
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
            print(f"[voice] TTS failed: {e}", file=sys.stderr)

    def _show_popup(self, question, text):
        """Push the latest answer to the kiosk page (the browser polls /answer)."""
        if not SHOW_POPUP or self._httpd is None:
            return
        clean = _SPEAK_CLEAN.sub("", text).strip()
        self._popup_state["question"] = question or ""
        self._popup_state["text"] = clean
        self._popup_state["ts"] = time.time()

