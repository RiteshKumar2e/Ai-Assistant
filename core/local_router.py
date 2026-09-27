"""
core/local_router.py — JUDO's own trained command-understanding model.

Trained on a Kaggle GPU (the training notebook lives on Kaggle, not in this
repo). Put the contents of the downloaded
judo_router_model.zip in models/judo_router/ (model.onnx, tokenizer.json,
meta.json). Runs on the CPU through onnxruntime in a few milliseconds and needs
no API or internet.

    from core import local_router
    local_router.predict("Rahul ko WhatsApp pe bol do main late aaunga")
    # -> {"label": "send_message", "confidence": 0.99,
    #     "args": {"receiver": "Rahul", "message_text": "main late aaunga"}}

CLI:
    python -m core.local_router "Spotify kholo"      # one sentence
    python -m core.local_router --eval               # score on the 20,000-task exam
    python -m core.local_router --install path/to/judo_router_model.zip
"""
from __future__ import annotations

import json
import sys
import zipfile
from functools import lru_cache
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "judo_router"


def available() -> bool:
    return all((MODEL_DIR / f).exists() for f in ("model.onnx", "tokenizer.json", "meta.json"))


@lru_cache(maxsize=1)
def _load():
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    tok.enable_truncation(meta.get("max_len", 64))
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2   # a background helper, not the thing hogging the CPU
    sess = ort.InferenceSession(str(MODEL_DIR / "model.onnx"), opts, providers=["CPUExecutionProvider"])
    return np, meta, tok, sess


def predict_many(texts: list[str]) -> list[dict]:
    if not available():
        raise FileNotFoundError(f"No trained router in {MODEL_DIR} — train it on Kaggle first.")
    np, meta, tok, sess = _load()
    enc = tok.encode_batch(texts)
    width = max(len(e.ids) for e in enc)
    pad = tok.token_to_id("<pad>") or 1
    ids = np.array([e.ids + [pad] * (width - len(e.ids)) for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask + [0] * (width - len(e.ids)) for e in enc], dtype=np.int64)
    intent, slots = sess.run(["intent", "slots"], {"input_ids": ids, "attention_mask": mask})
    probs = np.exp(intent - intent.max(-1, keepdims=True))
    probs /= probs.sum(-1, keepdims=True)
    out = []
    for text, e, p, st in zip(texts, enc, probs, slots.argmax(-1)):
        offsets = [o if not special else (0, 0) for o, special in zip(e.offsets, e.special_tokens_mask)]
        out.append({"label": meta["intents"][int(p.argmax())], "confidence": round(float(p.max()), 4),
                    "args": _decode(text, offsets, st[:len(offsets)].tolist(), meta["tags"])})
    return out


def predict(text: str) -> dict:
    return predict_many([text])[0]


def _decode(text: str, offsets, tag_ids, tags: list[str]) -> dict:
    """B-/I- slot tags back to {param: words from the sentence}."""
    out, cur, start, end = {}, None, 0, 0
    for (s, e), t in zip(offsets, tag_ids):
        name = tags[t] if s != e else "O"
        if name.startswith("I-") and cur == name[2:]:
            end = e
            continue
        if cur:
            out.setdefault(cur, text[start:end].strip())
        cur, start, end = (name[2:], s, e) if name != "O" else (None, 0, 0)
    if cur:
        out.setdefault(cur, text[start:end].strip())
    return out


def install(zip_path: str) -> str:
    """Unpack a downloaded judo_router_model.zip into models/judo_router/."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            base = Path(name).name
            if base in ("model.onnx", "tokenizer.json", "meta.json"):
                (MODEL_DIR / base).write_bytes(z.read(name))
    _load.cache_clear()
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    return (f"Installed router trained on {meta.get('examples_seen', 0):,} examples "
            f"({meta.get('trained', '?')}); Kaggle scores: {meta.get('scores')}")


def evaluate(limit: int = 20_000) -> str:
    """Score on the template exam: right intent, and every detail found."""
    from core import routing_trainer as rt
    items = rt.corpus()[:limit]
    ok_int = ok_all = 0
    for i in range(0, len(items), 128):
        chunk = items[i:i + 128]
        for (cmd, label, want), got in zip(chunk, predict_many([c for c, _, _ in chunk])):
            right = rt._is_correct(label, got["label"])
            ok_int += right
            args = {k: v for k, v in want.items() if v.lstrip("~#@").lower() in cmd.lower()}
            ok_all += right and rt._args_error(args, got["args"]) is None
    n = len(items)
    return f"{n:,} tasks: right intent {ok_int / n:.1%}, fully right {ok_all / n:.1%}"


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = sys.argv[1:]
    if a[:1] == ["--install"] and len(a) == 2:
        print(install(a[1]))
    elif a[:1] == ["--eval"]:
        print(evaluate(int(a[1]) if len(a) > 1 else 20_000))
    elif a:
        print(json.dumps(predict(" ".join(a)), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
