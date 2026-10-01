"""
voice_training/build_notebooks.py — writes the two Kaggle notebooks (plus Colab
twins, bundled as judo_voice_colab.zip) that give JUDO its own voice, with the
reference recording and sentences packed inside.

    python voice_training/build_notebooks.py

  1_make_voice_data.ipynb   IndicF5 (Kaggle GPU) speaks every sentence in the
                            reference voice -> judo_voice_dataset.zip
  2_train_voice.ipynb       fine-tunes a Piper voice on that dataset
                            -> judo_voice_model.zip (model.onnx + .json)

The Piper model then runs on JUDO's own CPU, offline, in well under a second.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REF_AUDIO = ROOT / "judo.mp3"
REF_TEXT = ("हेलो बॉस मैं आपसे गुस्सा हूं आप तीन दिन से मुझसे बात नहीं कर रहे थे, "
            "कोई बात नहीं, आज बस आप मेरे से बात कीजिए।")


def _cell(kind: str, text: str) -> dict:
    c = {"cell_type": kind, "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


def _notebook(cells: list[dict]) -> dict:
    return {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                         "language_info": {"name": "python"}}}


def _reference_wav_b64() -> str:
    """Reference voice as 24 kHz mono FLAC (IndicF5's native rate), base64 — FLAC, not WAV,
    keeps the notebook under Kaggle's 1 MB import limit."""
    data, sr = sf.read(str(REF_AUDIO), always_2d=True)
    mono = data.mean(axis=1)
    if sr != 24000:
        from scipy.signal import resample_poly
        g = np.gcd(sr, 24000)
        mono = resample_poly(mono, 24000 // g, sr // g)
    buf = io.BytesIO()
    sf.write(buf, mono.astype(np.float32), 24000, format="FLAC", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


KAGGLE = dict(work="/kaggle/working")
COLAB = dict(work="/content/drive/MyDrive/judo_voice")      # Drive, so a cut-off session loses nothing


def _data_intro(n: int, colab: bool) -> str:
    if colab:
        return f"""
# JUDO voice — step 1: make the training data (Google Colab)

IndicF5 (AI4Bharat) speaks **{n:,} Hindi sentences** in the reference voice packed
inside this notebook. Everything is saved in **Google Drive → `judo_voice/`**, so a cut-off
session loses nothing.

**Before Run all:**
1. **Runtime → Change runtime type → T4 GPU**
2. **🔑 Secrets** (left sidebar) → **Add new secret** — name `HF_TOKEN`, value = your Hugging Face
   token; switch on **Notebook access**
3. You must have clicked **Agree** once on https://huggingface.co/ai4bharat/IndicF5

Then **Runtime → Run all** and allow Google Drive access when asked. Takes roughly 1–3 hours.
If the session disconnects, just **Run all** again — it skips sentences already made.
"""
    return f"""
# JUDO voice — step 1: make the training data

IndicF5 (AI4Bharat) speaks **{n:,} Hindi sentences** in the reference voice packed
inside this notebook. The result is the dataset step 2 trains on.

**Before Run All** (right panel):
1. **Settings → Accelerator: GPU T4 x2** (or P100) and **Internet: On**
2. **Add-ons → Secrets → Add** — label `HF_TOKEN`, value = your Hugging Face token; tick it for this notebook
3. You must have clicked **Agree** once on https://huggingface.co/ai4bharat/IndicF5
4. Keep this notebook **Private** — it contains someone's voice

Takes roughly 1–3 hours. It skips sentences already made, so re-running continues.
When it ends: **Save Version → Save & Run All** is not needed again — just use this notebook's
**Output** as the input of step 2 (*Add Input → Your Work → this notebook*).
"""


def build_data_notebook(sentences: list[str], colab: bool = False) -> dict:
    p = COLAB if colab else KAGGLE
    if colab:
        setup = """
from google.colab import drive, userdata
drive.mount("/content/drive")
os.environ["HF_TOKEN"] = userdata.get("HF_TOKEN")                 # inherited by the script below
"""
    else:
        setup = """
from kaggle_secrets import UserSecretsClient
os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN")    # inherited by the script below
"""
    return _notebook([
        _cell("markdown", _data_intro(len(sentences), colab)),
        _cell("code", """
!pip install -q git+https://github.com/ai4bharat/IndicF5.git soundfile scipy "transformers<4.50"
"""),
        # The pip install above swaps numpy (IndicF5 pins numpy<=1.26.4) under the running
        # kernel, so numpy must not be imported here — the work runs in a fresh process below.
        _cell("code", f"""
import base64, json, os, pathlib
{setup.strip()}

WORK = pathlib.Path("{p['work']}"); WORK.mkdir(parents=True, exist_ok=True)
(WORK / "reference.flac").write_bytes(base64.b64decode("{_reference_wav_b64()}"))
(WORK / "ref_text.txt").write_text({json.dumps(REF_TEXT, ensure_ascii=False)}, encoding="utf-8")
SENTENCES = json.loads({json.dumps(json.dumps(sentences, ensure_ascii=False), ensure_ascii=False)})
(WORK / "sentences.json").write_text(json.dumps(SENTENCES, ensure_ascii=False), encoding="utf-8")
print(len(SENTENCES), "sentences; reference voice unpacked")
"""),
        _cell("code", f"%%writefile {p['work']}/make_data.py" + """
import json, os, pathlib, shutil, time
# IndicF5 pins numpy 1.26, but preinstalled TensorFlow/JAX need numpy 2 and transformers imports
# them if present — keep transformers PyTorch-only, and skip torch.compile (recompiles per length).
os.environ.update(USE_TF="0", USE_TORCH="1", USE_FLAX="0", USE_JAX="0", TRANSFORMERS_NO_TF="1",
                  TORCHDYNAMO_DISABLE="1")
import numpy as np, soundfile as sf, torch
from scipy.signal import resample_poly
from huggingface_hub import hf_hub_download, list_repo_files
from safetensors.torch import load_file
from transformers import AutoConfig
from transformers.dynamic_module_utils import get_class_from_dynamic_module

WORK = pathlib.Path(__file__).resolve().parent; DS = WORK / "judo_voice_dataset"; (DS / "wavs").mkdir(parents=True, exist_ok=True)
REF = WORK / "reference.wav"
ref_audio, ref_sr = sf.read(WORK / "reference.flac")
sf.write(REF, ref_audio, ref_sr, subtype="PCM_16")              # packed as FLAC; IndicF5 gets plain WAV
REF_TEXT = (WORK / "ref_text.txt").read_text(encoding="utf-8")
SENTENCES = json.loads((WORK / "sentences.json").read_text(encoding="utf-8"))

# Built by hand, not AutoModel.from_pretrained: that builds models on the "meta" device, where
# IndicF5's vocoder (Vocos) crashes ("Tensor on device cpu is not on the expected device meta").
REPO = "ai4bharat/IndicF5"
config = AutoConfig.from_pretrained(REPO, trust_remote_code=True)
model = get_class_from_dynamic_module(config.auto_map["AutoModel"], REPO)(config)
own = model.state_dict()
plain = lambda k: k.replace("_orig_mod.", "")                    # torch.compile renames keys
by_plain = {plain(k): k for k in own}
state = {}
for f in (f for f in list_repo_files(REPO) if f.endswith(".safetensors")):
    state.update(load_file(hf_hub_download(REPO, f)))
mapped = {by_plain[plain(k)]: v for k, v in state.items() if plain(k) in by_plain}
print(f"weights: {len(mapped)}/{len(own)} loaded, {len(state) - len(mapped)} checkpoint keys unused")
assert not state or len(mapped) > len(own) // 2, "weights did not match the model — send this log"
model.load_state_dict(mapped, strict=False)
model.eval()
try:
    model = model.to("cuda")
except Exception as e:
    print("could not move model to GPU explicitly:", e)
print("GPU:", torch.cuda.is_available() and torch.cuda.get_device_name(0))

meta_path = DS / "metadata.csv"
done = {l.split("|", 1)[0] for l in meta_path.read_text(encoding="utf-8").splitlines()} if meta_path.exists() else set()
t0, kept, dropped = time.time(), len(done), 0
with meta_path.open("a", encoding="utf-8") as meta:
    for i, text in enumerate(SENTENCES):
        name = f"judo_{i:05d}.wav"
        if name in done:
            continue
        if time.time() - t0 > 11 * 3600:
            print("stopping before the session limit — re-run to continue"); break
        try:
            audio = np.asarray(model(text, ref_audio_path=str(REF), ref_text=REF_TEXT))
        except Exception as e:
            dropped += 1; print("skip", i, e); continue
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        audio = audio.astype(np.float32).reshape(-1)
        secs = len(audio) / 24000
        rate = len(text.replace(" ", "")) / max(secs, 1e-6)       # characters per second
        if not (0.8 <= secs <= 25 and 3 <= rate <= 30) or not np.isfinite(audio).all():
            dropped += 1; continue                               # garbled / cut-off takes are not training data
        audio = resample_poly(audio, 147, 160)                   # 24 kHz -> 22.05 kHz (Piper's rate)
        audio = 0.95 * audio / max(1e-6, np.abs(audio).max())
        sf.write(DS / "wavs" / name, audio, 22050, subtype="PCM_16")
        meta.write(f"{name}|{text}\\n"); meta.flush(); kept += 1
        if kept % 50 == 0:
            el = time.time() - t0
            print(f"{kept}/{len(SENTENCES)} kept, {dropped} dropped, {el/60:.0f} min elapsed", flush=True)
print(f"done: {kept} clips kept, {dropped} dropped")

total = sum(sf.info(p).duration for p in (DS / "wavs").glob("*.wav"))
print(f"dataset: {len(list((DS/'wavs').glob('*.wav')))} clips, {total/3600:.2f} hours of audio")
shutil.make_archive(str(WORK / "judo_voice_dataset"), "zip", DS)
print("saved", WORK / "judo_voice_dataset.zip")
"""),
        _cell("code", f"""
!python -u {p['work']}/make_data.py
"""),
    ])


def build_train_notebook() -> dict:
    return _notebook([
        _cell("markdown", """
# JUDO voice — step 2: train the voice model (Piper)

Fine-tunes a Piper voice (starting from the English *lessac medium* checkpoint — Piper has no
Hindi checkpoint to start from) on the dataset from step 1. Piper models run on a plain CPU in
well under a second per sentence, which is what JUDO needs.

**Before Run All:**
1. **Settings → Accelerator: GPU T4 x2** (or P100), **Internet: On**
2. **Add Input → Your Work →** the step-1 notebook (its output holds `judo_voice_dataset`)

Training stops by itself after ~10.5 hours and saves the best it has. To train longer, add this
notebook's own output as an input next time — it resumes from the last checkpoint.
When finished, download **`judo_voice_model.zip`** from the Output tab.

⚠️ First run of this notebook may need a small fix — if a cell errors, send the error.
"""),
        _cell("code", """
%cd /kaggle/working
!git clone -q https://github.com/OHF-Voice/piper1-gpl.git
%cd /kaggle/working/piper1-gpl
!pip install -q -e ".[train]"
!bash ./build_monotonic_align.sh
!python setup.py build_ext --inplace -q
"""),
        _cell("code", """
import glob, os, pathlib
meta = sorted(glob.glob("/kaggle/input/**/metadata.csv", recursive=True))
assert meta, "Add the step-1 notebook output as an input first"
DATA = pathlib.Path(meta[0]).parent
print("dataset:", DATA, "-", len(list((DATA / "wavs").glob("*.wav"))), "clips")

resume = sorted(glob.glob("/kaggle/input/**/judo_voice_last.ckpt", recursive=True))
if resume:
    CKPT = resume[0]; print("resuming from", CKPT)
else:
    from huggingface_hub import hf_hub_download
    CKPT = hf_hub_download("rhasspy/piper-checkpoints", "en/en_US/lessac/medium/epoch=2164-step=1355540.ckpt",
                           repo_type="dataset")
    print("fine-tuning from lessac medium")
"""),
        _cell("code", """
import subprocess, sys
subprocess.run([sys.executable, "-m", "piper.train", "fit",
                "--data.voice_name", "judo",
                "--data.csv_path", f"{DATA}/metadata.csv",
                "--data.audio_dir", f"{DATA}/wavs",
                "--model.sample_rate", "22050",
                "--data.espeak_voice", "hi",
                "--data.cache_dir", "/kaggle/working/cache",
                "--data.config_path", "/kaggle/working/judo_voice.onnx.json",
                "--data.batch_size", "32",
                "--trainer.max_time", "00:10:30:00",      # stop before Kaggle's 12h limit
                "--ckpt_path", CKPT],
               cwd="/kaggle/working/piper1-gpl", check=True)
"""),
        _cell("code", """
import shutil
ckpts = sorted(glob.glob("/kaggle/working/piper1-gpl/lightning_logs/**/*.ckpt", recursive=True), key=os.path.getmtime)
assert ckpts, "no checkpoint was written — see the training cell's log"
shutil.copy(ckpts[-1], "/kaggle/working/judo_voice_last.ckpt")    # keep for resuming next session
!python -m piper.train.export_onnx --checkpoint "{ckpts[-1]}" --output-file /kaggle/working/judo_voice.onnx
import zipfile
with zipfile.ZipFile("/kaggle/working/judo_voice_model.zip", "w") as z:
    for f in ("judo_voice.onnx", "judo_voice.onnx.json"):
        z.write(f"/kaggle/working/{f}", f)
print("saved /kaggle/working/judo_voice_model.zip — download it from the Output tab")
"""),
    ])


def build_colab_train_notebook() -> dict:
    """Step 2 for Colab: reads the dataset from Drive and keeps checkpoints there, since free
    Colab sessions get cut off — every Run all resumes from the newest checkpoint."""
    w = COLAB["work"]
    return _notebook([
        _cell("markdown", """
# JUDO voice — step 2: train the voice model (Piper, Google Colab)

Fine-tunes a Piper voice (starting from the English *lessac medium* checkpoint — Piper has no
Hindi checkpoint to start from) on the dataset step 1 saved in **Google Drive → `judo_voice/`**.

**Before Run all:** **Runtime → Change runtime type → T4 GPU**, then **Runtime → Run all**
and allow Google Drive access.

Checkpoints are saved to Drive as training goes. When the session ends or disconnects, just
**Run all** again — it resumes from the newest checkpoint. More hours of training = a better voice.
The last cell writes **`judo_voice/judo_voice_model.zip`** in your Drive — download that.

⚠️ First run of this notebook may need a small fix — if a cell errors, send the error.
"""),
        _cell("code", """
from google.colab import drive
drive.mount("/content/drive")
%cd /content
!git clone -q https://github.com/OHF-Voice/piper1-gpl.git
%cd /content/piper1-gpl
!pip install -q -e ".[train]"
!bash ./build_monotonic_align.sh
!python setup.py build_ext --inplace -q
"""),
        _cell("code", f"""
import glob, os, pathlib
WORK = pathlib.Path("{w}"); DATA = WORK / "judo_voice_dataset"; RUNS = WORK / "train"
assert (DATA / "metadata.csv").exists(), "Run step 1 first — Drive has no judo_voice/judo_voice_dataset yet"
print("dataset:", len(list((DATA / "wavs").glob("*.wav"))), "clips")

resume = sorted(glob.glob(f"{{RUNS}}/**/*.ckpt", recursive=True), key=os.path.getmtime)
if resume:
    CKPT = resume[-1]; print("resuming from", CKPT)
else:
    from huggingface_hub import hf_hub_download
    CKPT = hf_hub_download("rhasspy/piper-checkpoints", "en/en_US/lessac/medium/epoch=2164-step=1355540.ckpt",
                           repo_type="dataset")
    print("fine-tuning from lessac medium")
"""),
        _cell("code", """
import subprocess, sys
subprocess.run([sys.executable, "-m", "piper.train", "fit",
                "--data.voice_name", "judo",
                "--data.csv_path", f"{DATA}/metadata.csv",
                "--data.audio_dir", f"{DATA}/wavs",
                "--model.sample_rate", "22050",
                "--data.espeak_voice", "hi",
                "--data.cache_dir", "/content/cache",
                "--data.config_path", f"{WORK}/judo_voice.onnx.json",
                "--data.batch_size", "32",
                "--trainer.default_root_dir", str(RUNS),   # checkpoints land in Drive
                "--trainer.max_time", "00:11:00:00",
                "--ckpt_path", CKPT],
               cwd="/content/piper1-gpl", check=True)
"""),
        _cell("code", """
ckpts = sorted(glob.glob(f"{RUNS}/**/*.ckpt", recursive=True), key=os.path.getmtime)
assert ckpts, "no checkpoint was written — see the training cell's log"
!python -m piper.train.export_onnx --checkpoint "{ckpts[-1]}" --output-file "{WORK}/judo_voice.onnx"
import zipfile
with zipfile.ZipFile(WORK / "judo_voice_model.zip", "w") as z:
    for f in ("judo_voice.onnx", "judo_voice.onnx.json"):
        z.write(WORK / f, f)
print("saved", WORK / "judo_voice_model.zip", "— download it from Google Drive")
"""),
    ])


if __name__ == "__main__":
    import zipfile
    sentences = [s.strip() for s in (HERE / "sentences_hi.txt").read_text(encoding="utf-8").splitlines() if s.strip()]
    colab = {"1_make_voice_data_colab.ipynb": build_data_notebook(sentences, colab=True),
             "2_train_voice_colab.ipynb": build_colab_train_notebook()}
    for name, nb in (("1_make_voice_data.ipynb", build_data_notebook(sentences)),
                     ("2_train_voice.ipynb", build_train_notebook()), *colab.items()):
        path = HERE / name
        path.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{path}  ({path.stat().st_size / 1e6:.1f} MB)")
    bundle = HERE / "judo_voice_colab.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for name in (*colab, "colab_commands.md"):
            z.write(HERE / name, name)
    print(f"{bundle}  ({bundle.stat().st_size / 1e6:.1f} MB)")
    # Everything for the two-cell Colab run (guide: colab_commands.md): scripts + voice + sentences.
    bundle = HERE / "judo_voice_colab_all.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((HERE / "colab_bundle").iterdir()):
            z.writestr(f.name, f.read_bytes().replace(b"\r\n", b"\n"))      # bash chokes on CRLF
        z.write(REF_AUDIO, "judo.mp3")
        z.write(HERE / "sentences_hi.txt", "sentences_hi.txt")
        z.write(HERE / "colab_commands.md", "README.md")
    print(f"{bundle}  ({bundle.stat().st_size / 1e6:.1f} MB)")
