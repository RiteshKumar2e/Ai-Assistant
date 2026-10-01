"""Step 2 — fine-tunes a Piper voice on judo_voice_dataset/ -> judo_voice_model.zip.

Starts from the English lessac-medium checkpoint (Piper has no Hindi one), or from
judo_voice_last.ckpt when that file sits next to this script (to keep training longer).
"""
import glob, os, pathlib, shutil, subprocess, sys, zipfile

WORK = pathlib.Path(__file__).resolve().parent
DATA, RUNS = WORK / "judo_voice_dataset", WORK / "train"
PIPER = "/content/piper1-gpl"
HOURS = os.environ.get("TRAIN_HOURS", "4")      # free Colab sessions don't last much longer

assert (DATA / "metadata.csv").exists(), "dataset missing — step 1 (make_data.py) did not finish"
print("dataset:", len(list((DATA / "wavs").glob("*.wav"))), "clips")

if (WORK / "judo_voice_last.ckpt").exists():
    ckpt = str(WORK / "judo_voice_last.ckpt"); print("resuming from", ckpt)
else:
    from huggingface_hub import hf_hub_download
    ckpt = hf_hub_download("rhasspy/piper-checkpoints", "en/en_US/lessac/medium/epoch=2164-step=1355540.ckpt",
                           repo_type="dataset")
    print("fine-tuning from lessac medium")

subprocess.run([sys.executable, "-m", "piper.train", "fit",
                "--data.voice_name", "judo",
                "--data.csv_path", f"{DATA}/metadata.csv",
                "--data.audio_dir", f"{DATA}/wavs",
                "--model.sample_rate", "22050",
                "--data.espeak_voice", "hi",
                "--data.cache_dir", "/content/cache",
                "--data.config_path", f"{WORK}/judo_voice.onnx.json",
                "--data.batch_size", "32",
                "--trainer.default_root_dir", str(RUNS),
                "--trainer.max_time", f"00:{int(HOURS):02d}:00:00",
                "--ckpt_path", ckpt],
               cwd=PIPER, check=True)

ckpts = sorted(glob.glob(f"{RUNS}/**/*.ckpt", recursive=True), key=os.path.getmtime)
assert ckpts, "no checkpoint was written — see the training log above"
shutil.copy(ckpts[-1], WORK / "judo_voice_last.ckpt")
subprocess.run([sys.executable, "-m", "piper.train.export_onnx", "--checkpoint", ckpts[-1],
                "--output-file", str(WORK / "judo_voice.onnx")], cwd=PIPER, check=True)
with zipfile.ZipFile(WORK / "judo_voice_model.zip", "w") as z:
    for f in ("judo_voice.onnx", "judo_voice.onnx.json"):
        z.write(WORK / f, f)
print("saved", WORK / "judo_voice_model.zip")
