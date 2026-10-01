# JUDO ki awaaz — Google Colab guide

JUDO ki apni Hindi awaaz banane ke liye: Colab ke free GPU pe, **ek zip + do cells**.
Google Drive ki zarurat nahi.

| Kya | Kitna time |
|---|---|
| Awaaz ka data banana (IndicF5, 1,932 sentences) | 1–3 ghante |
| Voice model train karna (Piper) | 4 ghante (badal sakte ho) |
| **Kul** | **~5–7 ghante** |

Result: **`judo_voice_model.zip`** (`judo_voice.onnx` + `judo_voice.onnx.json`) — yeh model JUDO
ke CPU pe bina internet ke, ek second se kam mein bolta hai.

---

## 1. Ek baar ki tayyari

1. **Hugging Face token:** https://huggingface.co/settings/tokens → **Create new token** → type **Read**
   → copy karo (`hf_...`)
2. **IndicF5 access:** https://huggingface.co/ai4bharat/IndicF5 kholo → **Agree** pe click
   (iske bina model download nahi hota)
3. **Zip:** `voice_training/judo_voice_colab_all.zip` — isme sab hai:

   | File | Kaam |
   |---|---|
   | `judo.mp3` | reference awaaz (14 sec) |
   | `sentences_hi.txt` | 1,932 Hindi sentences |
   | `make_data.py` | step 1 — IndicF5 har sentence us awaaz mein bolta hai |
   | `train.py` | step 2 — Piper voice train + export |
   | `run_all.sh` | dono steps ek ke baad ek chalata hai |
   | `README.md` | yahi guide |

   (Zip dobara banana ho: `python voice_training/build_notebooks.py`)

## 2. Colab setup

1. https://colab.research.google.com → **New notebook**
2. **Runtime → Change runtime type → T4 GPU** → Save
3. Left side 🔑 **Secrets** → **Add new secret**
   - Name: `HF_TOKEN`
   - Value: Hugging Face token
   - **Notebook access**: On

## 3. Cell 1 — zip upload

Chalao → **Choose files** → `judo_voice_colab_all.zip` chuno.
"Grant access to HF_TOKEN?" pooche to **Grant access**.

```python
import os, zipfile
from google.colab import files, userdata
os.environ["HF_TOKEN"] = userdata.get("HF_TOKEN")
for name in files.upload():
    zipfile.ZipFile(name).extractall("/content/judo_voice")
```

## 4. Cell 2 — sab kuch

```python
!bash /content/judo_voice/run_all.sh
from google.colab import files
for f in ("judo_voice_model.zip", "judo_voice_dataset.zip", "judo_voice_last.ckpt"):
    if os.path.exists(f"/content/judo_voice/{f}"):
        files.download(f"/content/judo_voice/{f}")
```

Output mein 4 steps dikhenge:

```
=== step 1/4: installing IndicF5 ===
=== step 2/4: making the voice dataset (1-3 hours) ===
weights: .../... loaded            <- model sahi load hua
GPU: Tesla T4
50/1932 kept, 0 dropped, 4 min elapsed
...
=== step 3/4: installing Piper ===
=== step 4/4: training the voice (4 hours) ===
saved /content/judo_voice/judo_voice_model.zip
```

Khatam hone pe 3 files download hongi:

| File | Kya karna hai |
|---|---|
| **`judo_voice_model.zip`** | **JUDO ke liye yahi chahiye** |
| `judo_voice_dataset.zip` | awaaz ka data — backup rakh lo |
| `judo_voice_last.ckpt` | aage aur training karni ho to |

---

## Options

**Training ke ghante badalna** — Cell 2 se pehle ek cell chalao:
```python
os.environ["TRAIN_HOURS"] = "3"
```

**Aur training (awaaz behtar karni ho)** — naye session mein Cell 1 ke baad yeh chalao aur
`judo_voice_last.ckpt` chuno; `train.py` wahin se aage train karega:
```python
for name in files.upload():
    os.rename(name, f"/content/judo_voice/{name}")
```
(Step 1 ka data phir bhi dobara banega — 1–3 ghante.)

---

## Dhyan rakhne wali baatein

- **Browser tab khula rakho** — bina activity ke Colab session band kar deta hai
- Session kat gaya to Colab ki saari files mit jaati hain → **dono cells dobara** chalao
- Free Colab mein GPU ka daily limit hai — "GPU not available" aaye to kuch ghante baad try karo
- Koi step fail ho to script wahin ruk jaata hai — poora output copy karke Claude ko bhejo
- Zip mein ek insaan ki awaaz hai — **public share mat karo** (git mein bhi ignore hai)

## Common errors

| Error | Matlab | Fix |
|---|---|---|
| `... requires numpy>=2, but you have numpy 1.26.4` | sirf warning — Colab ke doosre packages ke baare mein | kuch nahi, ignore karo |
| `SecretNotFoundError` | `HF_TOKEN` secret nahi mila | Secrets mein naam bilkul `HF_TOKEN`, Notebook access On |
| `401` / `gated repo` | IndicF5 pe Agree nahi kiya | https://huggingface.co/ai4bharat/IndicF5 → Agree |
| `No such file ... run_all.sh` | Cell 1 mein galat zip | `judo_voice_colab_all.zip` hi upload karo |
| `weights did not match the model` | IndicF5 model badal gaya | output Claude ko bhejo |
| `CUDA out of memory` (training) | GPU memory kam | `train.py` mein `--data.batch_size` 32 → 16 |
