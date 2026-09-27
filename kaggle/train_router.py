"""
kaggle/train_router.py — train JUDO's own command-understanding model on a GPU.

Runs on Kaggle (packed into one notebook by `python -m core.kaggle_export`),
or anywhere with torch + transformers. It trains a multilingual encoder
(Multilingual MiniLM, Hindi/Hinglish/English) with two heads:

  intent  which tool + action the sentence means (open_app, send_message, …)
  slots   which words are the details (receiver "Rahul", message "main late
          aaunga", folder "Projects", city "Delhi") — BIO tags per token

Training data is generated on the fly — nothing is stored, so 40,000,000
examples cost no disk:
  * template tasks from training_corpus.sample() (fresh slot values each time)
  * the cross-verified seed tasks written by Gemini + Groq (seed_tasks.jsonl),
    with names/apps/cities swapped for new ones
  * spoken noise on top: lowercase, no punctuation, fillers, small typos
A held-out 10% of the seed tasks is never trained on and is the honest score.

Output: judo_router_model.zip (int8 ONNX model + tokenizer + labels + scores)
— download it and put its contents in JUDO's models/judo_router/.

Settings come from environment variables (defaults are the Kaggle run):
  JUDO_TOTAL   examples to train on        (40_000_000)
  JUDO_BATCH   batch size                  (256)
  JUDO_MODEL   encoder                     (microsoft/Multilingual-MiniLM-L12-H384)
  JUDO_TOKENIZER tokenizer                 (xlm-roberta-base — MiniLM uses XLM-R's)
  JUDO_HOURS   stop training after this    (11.0 — Kaggle kills sessions at 12h)
  JUDO_SRC     folder with training_corpus.py + seed_tasks.jsonl
  JUDO_OUT     output folder               (/kaggle/working or ./router_out)
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
import zipfile
import zlib
from pathlib import Path

MAX_LEN = 64
FILLERS = ["umm ", "acha ", "suno ", "arey yaar ", "haan ", "ok so ", "matlab "]
# (tool, param) -> which training_corpus.S pool a seed task's value can be swapped with
SWAP = {("send_message", "receiver"): "contact", ("open_app", "app_name"): "app",
        ("weather_report", "city"): "city", ("flight_finder", "origin"): "city",
        ("flight_finder", "destination"): "city", ("open_folder", "folder_path"): "folder",
        ("file_controller", "destination"): "folder", ("game_updater", "game_name"): "game",
        ("quiz_mode", "topic"): "quiz", ("manage_monitor", "topic"): "topic", ("send_email", "to"): "email"}


def env(name: str, default):
    raw = os.environ.get(name)
    return type(default)(raw) if raw not in (None, "") else default


# ── Data ─────────────────────────────────────────────────────────────────────

def load_sources(src: Path):
    sys.path.insert(0, str(src))
    import training_corpus as tc
    seeds = []
    path = src / "seed_tasks.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                t = json.loads(line)
            except ValueError:
                continue
            args = {k.rstrip("?").split("|")[0]: str(v).lstrip("~#@") for k, v in t.get("args", {}).items()}
            seeds.append((t["cmd"], t["label"], args))
    held = [s for s in seeds if zlib.crc32(s[0].encode()) % 10 == 0]
    train = [s for s in seeds if zlib.crc32(s[0].encode()) % 10 != 0]
    return tc, train, held


def build_labels(tc, seeds):
    intents = sorted(set(tc.T) | {l for _, l, _ in seeds})
    params = sorted({spec.rstrip("?").split("|")[0] for spec_map in tc.ARGS.values() for spec in spec_map}
                    | {p for _, _, a in seeds for p in a})
    tags = ["O"] + [f"{bi}-{p}" for p in params for bi in ("B", "I")]
    return intents, tags


def _swap(cmd: str, label: str, args: dict, tc, rng) -> tuple[str, dict]:
    tool = label.split(".")[0]
    args = dict(args)
    for param, value in list(args.items()):
        pool = SWAP.get((tool, param))
        if not pool or not value:
            continue
        m = re.search(re.escape(value), cmd, re.I)
        if not m:
            continue
        new = rng.choice(tc.S[pool])
        cmd = cmd[:m.start()] + new + cmd[m.end():]
        args[param] = new
    return cmd, args


def _typo(word: str, rng) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:] if rng.random() < .5 else word[:i] + word[i + 1:]


def noisy(cmd: str, args: dict, rng) -> str:
    """Spoken-input noise that never touches a detail the model must find."""
    protected = [v.lower() for v in args.values() if v]
    if rng.random() < .15:
        words = cmd.split()
        free = [i for i, w in enumerate(words) if not any(w.lower() in p or p in w.lower() for p in protected)]
        if free:
            i = rng.choice(free)
            words[i] = _typo(words[i], rng)
            cmd = " ".join(words)
    if rng.random() < .15:
        cmd = rng.choice(FILLERS) + cmd
    if rng.random() < .3:
        cmd = re.sub(r"[?!,]", "", cmd)
    if rng.random() < .3:
        cmd = cmd.lower()
    return cmd


def spans(cmd: str, args: dict) -> list[tuple[int, int, str]]:
    """Character spans of each detail in the sentence (case-insensitive);
    details that are not spelled out in the words get no span."""
    out, low = [], cmd.lower()
    for param, value in args.items():
        if not value:
            continue
        i = low.find(value.lower())
        if i >= 0:
            out.append((i, i + len(value), param))
    return out


def bio(offsets, spans_, tag_id: dict) -> list[int]:
    """Token-level B-/I- tags from character spans. Special/padding tokens
    (offset (0, 0)) get -100 so the loss ignores them."""
    tags, prev = [], None
    for s, e in offsets:
        if s == e:
            tags.append(-100)
            prev = None
            continue
        hit = next((p for a, b, p in spans_ if a <= s < b), None)
        if hit is None:
            tags.append(tag_id["O"])
            prev = None
        else:
            tags.append(tag_id[("I-" if prev == hit else "B-") + hit])
            prev = hit
    return tags


def example(tc, seeds, rng) -> tuple[str, str, dict]:
    if seeds and rng.random() < .5:
        cmd, label, args = rng.choice(seeds)
        if rng.random() < .7:
            cmd, args = _swap(cmd, label, args, tc, rng)
    else:
        cmd, label, args = tc.sample(rng)
    return noisy(cmd, args, rng), label, args


# ── Model ────────────────────────────────────────────────────────────────────

def make_model(name: str, n_intents: int, n_tags: int):
    import torch.nn as nn
    from transformers import AutoModel

    class Router(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc = AutoModel.from_pretrained(name)
            h = self.enc.config.hidden_size
            self.drop = nn.Dropout(0.1)
            self.intent = nn.Linear(h, n_intents)
            self.slot = nn.Linear(h, n_tags)

        def forward(self, input_ids, attention_mask):
            x = self.drop(self.enc(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state)
            return self.intent(x[:, 0]), self.slot(x)

    return Router()


def decode(text: str, offsets, tag_ids, tags: list[str]) -> dict:
    """Slot tags back to {param: words from the sentence}."""
    out, cur, start, end = {}, None, 0, 0
    for (s, e), t in zip(offsets, list(tag_ids) + [0]):
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


# ── Training ─────────────────────────────────────────────────────────────────

def main() -> None:
    import torch
    from torch.utils.data import DataLoader, IterableDataset, get_worker_info
    from transformers import AutoTokenizer

    total = env("JUDO_TOTAL", 40_000_000)
    batch = env("JUDO_BATCH", 256)
    model_name = env("JUDO_MODEL", "microsoft/Multilingual-MiniLM-L12-H384")
    tok_name = env("JUDO_TOKENIZER", "xlm-roberta-base")
    hours = env("JUDO_HOURS", 11.0)
    src = Path(env("JUDO_SRC", "/kaggle/working/judo_src"))
    out = Path(env("JUDO_OUT", "/kaggle/working" if Path("/kaggle").exists() else "router_out"))
    out.mkdir(parents=True, exist_ok=True)

    tc, seeds, held = load_sources(src)
    intents, tags = build_labels(tc, seeds + held)
    int_id, tag_id = {l: i for i, l in enumerate(intents)}, {t: i for i, t in enumerate(tags)}
    tok = AutoTokenizer.from_pretrained(tok_name)
    print(f"{len(intents)} intents, {len(tags)} slot tags, {len(seeds):,} seed tasks for training, "
          f"{len(held):,} held out", flush=True)

    def encode(items):
        enc = tok([t for t, _, _ in items], truncation=True, max_length=MAX_LEN, padding=True,
                  return_offsets_mapping=True, return_tensors="pt")
        slot = [bio(o.tolist(), spans(t, a), tag_id) for (t, _, a), o in zip(items, enc["offset_mapping"])]
        return (enc["input_ids"], enc["attention_mask"], torch.tensor([int_id[l] for _, l, _ in items]),
                torch.tensor(slot))

    class Stream(IterableDataset):
        def __iter__(self):
            info = get_worker_info()
            rng = random.Random(time.time_ns() + (info.id if info else 0))
            while True:
                yield encode([example(tc, seeds, rng) for _ in range(batch)])

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = make_model(model_name, len(intents), len(tags)).to(dev)
    net = torch.nn.DataParallel(model) if torch.cuda.device_count() > 1 else model
    steps = max(1, total // batch)
    opt = torch.optim.AdamW(model.parameters(), lr=8e-5, weight_decay=0.01)
    warm = max(1, steps // 50)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min((s + 1) / warm, max(0.0, (steps - s) / (steps - warm))))
    scaler = torch.amp.GradScaler("cuda", enabled=dev == "cuda")
    ce = torch.nn.CrossEntropyLoss(ignore_index=-100)
    loader = DataLoader(Stream(), batch_size=None, num_workers=min(4, os.cpu_count() or 1),
                        prefetch_factor=4 if (os.cpu_count() or 1) > 1 else None, persistent_workers=False)

    eval_sets = {"templates": [(c, l, {k.rstrip("?").split("|")[0]: v.lstrip("~#@") for k, v in a.items()})
                               for c, l, a in random.Random(7).sample(tc.build(), 2000)],
                 "held-out seeds": held[:3000]}

    def evaluate() -> dict:
        net.eval()
        scores = {}
        with torch.no_grad():
            for name, items in eval_sets.items():
                if not items:
                    continue
                ok_int = ok_all = 0
                for i in range(0, len(items), 256):
                    chunk = items[i:i + 256]
                    enc = tok([t for t, _, _ in chunk], truncation=True, max_length=MAX_LEN, padding=True,
                              return_offsets_mapping=True, return_tensors="pt")
                    with torch.autocast(dev, enabled=dev == "cuda"):
                        li, ls = net(enc["input_ids"].to(dev), enc["attention_mask"].to(dev))
                    for (text, label, args), p, st, off in zip(chunk, li.argmax(-1).tolist(),
                                                              ls.argmax(-1).tolist(), enc["offset_mapping"].tolist()):
                        right = intents[p] == label
                        ok_int += right
                        got = decode(text, off, st, tags)
                        want = {k: v for k, v in args.items() if v and v.lower() in text.lower()}
                        ok_all += right and all(got.get(k, "").lower() == v.lower() for k, v in want.items())
                scores[name] = {"intent": round(ok_int / len(items), 4), "full_task": round(ok_all / len(items), 4)}
        net.train()
        return scores

    print(f"training on {dev} ({torch.cuda.device_count()} GPU) for {steps:,} steps × {batch} = {steps * batch:,} "
          f"examples, time budget {hours}h", flush=True)
    t0, seen, best = time.time(), 0, {}
    net.train()
    for step, (ids, mask, yi, ys) in enumerate(loader):
        if step >= steps or time.time() - t0 > hours * 3600:
            break
        ids, mask, yi, ys = ids.to(dev), mask.to(dev), yi.to(dev), ys.to(dev)
        with torch.autocast(dev, enabled=dev == "cuda"):
            li, ls = net(ids, mask)
            loss = ce(li.float(), yi) + ce(ls.float().reshape(-1, len(tags)), ys.reshape(-1))
        opt.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(opt)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(opt)
        scaler.update()
        sched.step()
        seen += ids.shape[0]
        if step % 500 == 0:
            rate = seen / max(1e-9, time.time() - t0)
            print(f"step {step:,}/{steps:,}  loss {loss.item():.4f}  {seen:,} examples  {rate:,.0f}/s  "
                  f"ETA {(steps * batch - seen) / max(rate, 1) / 3600:.1f}h", flush=True)
        if step and step % 10_000 == 0:
            best = evaluate()
            print(f"eval @ {seen:,}: {best}", flush=True)

    best = evaluate()
    print(f"final eval after {seen:,} examples: {best}", flush=True)
    export(model, tok, intents, tags, best, seen, model_name, out)


def export(model, tok, intents, tags, scores, seen, model_name, out: Path) -> Path:
    import torch
    model = model.float().cpu().eval()
    pkg = out / "judo_router"
    pkg.mkdir(parents=True, exist_ok=True)
    sample = tok(["Rahul ko WhatsApp pe bol do main late aaunga"], return_tensors="pt")
    fp32 = pkg / "model_fp32.onnx"
    torch.onnx.export(model, (sample["input_ids"], sample["attention_mask"]), str(fp32),
                      input_names=["input_ids", "attention_mask"], output_names=["intent", "slots"],
                      dynamic_axes={"input_ids": {0: "b", 1: "t"}, "attention_mask": {0: "b", 1: "t"},
                                    "intent": {0: "b"}, "slots": {0: "b", 1: "t"}},
                      opset_version=17, dynamo=False)
    final = pkg / "model.onnx"
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        quantize_dynamic(str(fp32), str(final), weight_type=QuantType.QInt8)
        fp32.unlink()
    except Exception as e:   # onnxruntime missing — ship the full-precision model instead
        print(f"int8 quantization skipped ({e}); shipping fp32", flush=True)
        fp32.replace(final)
    tok.save_pretrained(str(pkg))
    (pkg / "meta.json").write_text(json.dumps({
        "intents": intents, "tags": tags, "max_len": MAX_LEN, "encoder": model_name,
        "examples_seen": seen, "scores": scores, "trained": time.strftime("%Y-%m-%d %H:%M")},
        indent=1, ensure_ascii=False), encoding="utf-8")
    zpath = out / "judo_router_model.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pkg.iterdir():
            if f.name in ("model.onnx", "tokenizer.json", "meta.json"):
                z.write(f, f"judo_router/{f.name}")
    print(f"saved {zpath} ({zpath.stat().st_size / 1e6:.0f} MB) — download it from the Output tab", flush=True)
    return zpath


if __name__ == "__main__":
    main()
