"""
Local wake-word detection for JUDO ("Hey Judo").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword only ships a fixed set of PRETRAINED phrases (alexa, hey_mycroft,
hey_jarvis, ...) — there is no pretrained "hey_judo" model. WAKE_MODEL below is
set to "hey_judo" to match JUDO's own name, but that model does not exist yet:
is_ready()/install_and_download() will simply report the feature as not
available until a custom "hey_judo*.onnx" model is trained (openWakeWord's own
custom-model pipeline: synthetic TTS positive samples + their negative dataset,
trained outside this app — see their docs) and dropped into openwakeword's
resources/models directory. Until then, wake word is effectively disabled
rather than silently listening for the wrong word.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# Model name JUDO listens for. NOT one of openwakeword's pretrained models
# (see module docstring) — a "hey_judo*.onnx" file must be trained and placed
# in openwakeword's models directory before this actually detects anything.
WAKE_MODEL = "hey_judo"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def models_dir() -> Path | None:
    """Where openwakeword looks for .onnx model files, or None if not installed."""
    if not is_installed():
        return None
    try:
        import openwakeword
        return Path(openwakeword.__file__).resolve().parent / "resources" / "models"
    except Exception:
        return None


def _has_model(name: str) -> bool:
    d = models_dir()
    if not d or not d.is_dir():
        return False
    return any(d.glob(f"{name}*.onnx")) or any(d.glob(f"{name}*.tflite"))


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    return _has_model(WAKE_MODEL) and _has_model("melspectrogram") and _has_model("embedding_model")


def install_and_download(logger: Callable[[str], None] = print) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the SHARED feature-extractor models (melspectrogram + embedding —
    every wake phrase needs these, pretrained or custom). WAKE_MODEL itself
    ("hey_judo") is not one of openwakeword's pretrained phrases, so it is never
    fetched here — see the module docstring for how to get a real one.
    Returns (ok, message). Never raises — every failure is reported through the
    returned message and the logger.
    """
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"

        logger("Wake word: downloading shared feature-extractor models…")
        try:
            import openwakeword.utils as _u
            _u.download_models()   # mel + embedding models, not phrase-specific
        except Exception as e:
            return False, f"model download failed: {e}"

        if not _has_model("melspectrogram") or not _has_model("embedding_model"):
            return False, "installed, but the shared feature-extractor models could not be loaded."
        if not _has_model(WAKE_MODEL):
            d = models_dir()
            return False, (
                f"Shared models ready. No '{WAKE_MODEL}' model yet — train a custom "
                f"one (see the module docstring) and drop it into {d} to finish setup."
            )
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception as e:
            self._logger(f"Wake word: could not load model — {e}")
            self._model = None
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger("Wake word: listening for 'Hey Judo'.")
        return True

    def stop(self) -> None:
        self._running = False
        # unblock the thread if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                scores = self._model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    # match the judo model regardless of exact key suffix
                    for k, v in scores.items():
                        if "judo" in k.lower():
                            score = max(score, float(v))
                    if score == 0.0 and scores:
                        score = max(float(v) for v in scores.values())
                if score >= self._threshold:
                    # drain any backlog so we don't double-fire on the same utterance
                    self._drain()
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
