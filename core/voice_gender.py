"""
core/voice_gender.py — rolling voice-pitch heuristic for the ADDRESS fallback.

This is NOT a biometric identifier and does not try to recognise who is
speaking. It only estimates whether the CURRENT speaker's voice sounds
lower-pitched or higher-pitched, so that when no name is known yet (see
save_memory's identity.name in main.py) the assistant has something better
than a coin flip to pick between a masculine and a feminine respectful form.

Pitch ranges overlap between voices — a rough classification here is
expected to be occasionally wrong. That is exactly why a name, once known,
always outranks this (main.py._build_config), and why a manual override
exists in Settings (memory.config_manager.get_user_gender) for anyone who
would rather not rely on a guess at all.
"""
from __future__ import annotations

from collections import deque

import numpy as np

_MIN_HZ, _MAX_HZ = 70, 300          # covers the human voiced-speech F0 range
_SPLIT_HZ        = 165              # below -> "male", at/above -> "female"
_MIN_RMS         = 200.0            # quieter frames carry no trustworthy pitch
_MIN_PERIODICITY = 0.3              # peak/zero-lag autocorrelation ratio to accept a read


def estimate_pitch(pcm_int16, sample_rate: int) -> float | None:
    """Autocorrelation-based F0 estimate in Hz for one mono int16 PCM frame,
    or None if the frame is too quiet or not periodic enough to trust."""
    x = np.asarray(pcm_int16, dtype=np.float32).reshape(-1)
    min_lag = sample_rate // _MAX_HZ
    max_lag = sample_rate // _MIN_HZ
    if x.size < max_lag * 2:
        return None

    rms = float(np.sqrt(np.mean(x * x)))
    if rms < _MIN_RMS:
        return None

    x = x - x.mean()
    corr = np.correlate(x, x, mode="full")[x.size - 1:]
    if corr[0] <= 0 or max_lag >= corr.size:
        return None

    window = corr[min_lag:max_lag]
    if window.size == 0:
        return None
    peak_lag = int(np.argmax(window)) + min_lag
    if corr[peak_lag] <= _MIN_PERIODICITY * corr[0]:
        return None

    return sample_rate / peak_lag


class GenderEstimator:
    """Feed it raw mic frames continuously; call gender() when you need the
    current best guess. A rolling median over recent voiced frames keeps a
    single noisy read (or one word of background noise) from flipping the
    result mid-conversation."""

    def __init__(self, history: int = 25, min_samples: int = 6):
        self._pitches = deque(maxlen=history)
        self._min_samples = min_samples

    def feed(self, pcm_int16, sample_rate: int) -> None:
        try:
            hz = estimate_pitch(pcm_int16, sample_rate)
        except Exception:
            hz = None
        if hz is not None:
            self._pitches.append(hz)

    def gender(self) -> str | None:
        """'male' | 'female' | None (not enough voiced audio collected yet)."""
        if len(self._pitches) < self._min_samples:
            return None
        return "female" if float(np.median(self._pitches)) >= _SPLIT_HZ else "male"

    def reset(self) -> None:
        self._pitches.clear()
