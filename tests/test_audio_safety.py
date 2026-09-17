"""
Tests for the mic callback's exception safety in main.py.

The callback is a closure nested inside JudoLive._listen_audio, and JudoLive's
__init__ does heavy real-world setup (Gemini client, Qt UI, audio device
enumeration) that isn't practical to construct in a unit test without
rewriting working architecture — out of scope for this change. Instead this
verifies the actual property that matters: the callback function's body is
wrapped in a top-level try/except, so sounddevice can never see an
unhandled exception from it (which would silently stop the whole input
stream) — a structural/regression guard on the source itself.

Also documents, via a direct re-check of the reverted AGC feature, that the
previous broken per-block gain implementation has not been reintroduced.
"""
import ast
from pathlib import Path

import main as judo_main


def _get_listen_audio_source() -> ast.FunctionDef:
    tree = ast.parse(Path(judo_main.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_listen_audio":
            return node
    raise AssertionError("_listen_audio not found in main.py")


def _get_callback_source(listen_audio_node: ast.FunctionDef) -> ast.FunctionDef:
    for node in ast.walk(listen_audio_node):
        if isinstance(node, ast.FunctionDef) and node.name == "callback":
            return node
    raise AssertionError("callback() not found inside _listen_audio")


def test_mic_callback_body_is_wrapped_in_try_except():
    """Regression guard: the callback's entire body must be one try/except
    (or start with one) so any unexpected exception drops a frame instead of
    killing the whole mic stream."""
    listen_audio = _get_listen_audio_source()
    callback = _get_callback_source(listen_audio)

    assert len(callback.body) >= 1
    assert isinstance(callback.body[0], ast.Try), (
        "callback() no longer starts with a try/except — an exception from "
        "any statement inside it (e.g. loop.call_soon_threadsafe on a "
        "closing loop) would now propagate to sounddevice and silently stop "
        "the mic stream."
    )


def test_mic_callback_try_has_a_bare_except_that_does_not_reraise():
    listen_audio = _get_listen_audio_source()
    callback = _get_callback_source(listen_audio)
    try_node = callback.body[0]
    assert isinstance(try_node, ast.Try)
    assert len(try_node.handlers) >= 1
    for handler in try_node.handlers:
        for stmt in ast.walk(handler):
            assert not isinstance(stmt, ast.Raise), (
                "the callback's outer except must not re-raise — that would "
                "defeat the whole point of the safety net"
            )


def test_agc_per_block_gain_boost_not_reintroduced():
    """The reverted AGC (independent gain per 64ms block, no smoothing) broke
    speech recognition entirely. If voice capture is revisited it must use
    smoothed/attack-release gain — these symbols must not silently reappear."""
    source = Path(judo_main.__file__).read_text(encoding="utf-8")
    assert "_boost_quiet_speech" not in source
    assert "_AGC_TARGET_RMS" not in source
    assert "_AGC_MAX_GAIN" not in source
