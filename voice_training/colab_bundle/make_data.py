"""Step 1 — IndicF5 speaks every sentence in the reference voice -> judo_voice_dataset/."""
import os, pathlib, shutil, time
# IndicF5 pins numpy 1.26, but Colab's TensorFlow/JAX need numpy 2 and transformers imports them
# if present — keep transformers PyTorch-only. Eager mode: torch.compile would recompile for
# every new sentence length, and nothing here needs it.
os.environ.update(USE_TF="0", USE_TORCH="1", USE_FLAX="0", USE_JAX="0", TRANSFORMERS_NO_TF="1",
                  TORCHDYNAMO_DISABLE="1")
import librosa, numpy as np, soundfile as sf, torch
from scipy.signal import resample_poly
from huggingface_hub import hf_hub_download, list_repo_files
from safetensors.torch import load_file
from transformers import AutoConfig
from transformers.dynamic_module_utils import get_class_from_dynamic_module

WORK = pathlib.Path(__file__).resolve().parent; DS = WORK / "judo_voice_dataset"; (DS / "wavs").mkdir(parents=True, exist_ok=True)
REF = WORK / "reference.wav"
ref_audio, _ = librosa.load(WORK / "judo.mp3", sr=24000, mono=True)   # IndicF5's native rate
sf.write(REF, ref_audio, 24000, subtype="PCM_16")
REF_TEXT = ("हेलो बॉस मैं आपसे गुस्सा हूं आप तीन दिन से मुझसे बात नहीं कर रहे थे, "
            "कोई बात नहीं, आज बस आप मेरे से बात कीजिए।")
SENTENCES = [s.strip() for s in (WORK / "sentences_hi.txt").read_text(encoding="utf-8").splitlines() if s.strip()]
print(len(SENTENCES), "sentences")

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
        meta.write(f"{name}|{text}\n"); meta.flush(); kept += 1
        if kept % 50 == 0:
            print(f"{kept}/{len(SENTENCES)} kept, {dropped} dropped, {(time.time() - t0) / 60:.0f} min elapsed", flush=True)
print(f"done: {kept} clips kept, {dropped} dropped")

total = sum(sf.info(p).duration for p in (DS / "wavs").glob("*.wav"))
print(f"dataset: {len(list((DS / 'wavs').glob('*.wav')))} clips, {total / 3600:.2f} hours of audio")
shutil.make_archive(str(WORK / "judo_voice_dataset"), "zip", DS)
print("saved", WORK / "judo_voice_dataset.zip")
