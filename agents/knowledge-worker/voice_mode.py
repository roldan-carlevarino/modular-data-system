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

WAKEWORD_MODEL = os.environ.get("WAKEWORD_MODEL", "hey_jarvis")
WAKEWORD_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))
WAKEWORD_FRAMEWORK = os.environ.get("WAKEWORD_FRAMEWORK", "onnx")
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
_IS_MAC = platform.system() == "Darwin"
SPEAK = os.environ.get("SPEAK", "1" if _IS_MAC else "0") == "1"
SPEAK_VOICE = os.environ.get("SPEAK_VOICE", "")

_FRAME = 1280  # openWakeWord expects 80 ms frames at 16 kHz
_FRAME_MS = 80
_SPEAK_CLEAN = re.compile(r"\[U\d+[^\]]*\]|📊")


class VoiceMode:
    """Background wake-word listener + local STT that answers in-process."""

    def __init__(self, pause_event, get_session, answer_fn):
        self._pause = pause_event
        self._get_session = get_session
        self._answer = answer_fn
        self._history = []
        self._whisper = None
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
        self._thread.start()

    # -- internals ---------------------------------------------------------- #

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
                                dtype="int16", blocksize=_FRAME)
        stream.start()
        print("[voice] escuchando... (di la palabra de activación)")
        try:
            while True:
                frame, _ = stream.read(_FRAME)
                frame = frame.flatten()
                scores = self._oww.predict(frame)
                score = scores.get(self._wake_key, 0.0)
                if score >= WAKEWORD_THRESHOLD:
                    self._handle_activation(stream)
        except Exception as e:  # noqa: BLE001
            print(f"[voice] listener stopped: {e}", file=sys.stderr)
        finally:
            stream.stop()
            stream.close()

    def _handle_activation(self, stream):
        self._pause.set()  # main loop yields Ollama to us
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
            self._speak(answer)
            self._history.append({"question": question, "answer": answer})
            self._history = self._history[-VOICE_HISTORY_TURNS:]
        except Exception as e:  # noqa: BLE001
            print(f"[voice] error answering: {e}", file=sys.stderr)
        finally:
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
