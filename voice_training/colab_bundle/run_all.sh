#!/bin/bash
# Runs both steps on a Colab T4: IndicF5 makes the dataset, then Piper trains on it.
# Piper is installed only after step 1 because the two pin different library versions
# (IndicF5 needs numpy 1.26; the "incompatible" pip warnings about other Colab packages are harmless).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
export USE_TF=0 USE_FLAX=0 USE_JAX=0 TRANSFORMERS_NO_TF=1 PYTHONWARNINGS=ignore

echo "=== step 1/4: installing IndicF5 ==="
pip install -q git+https://github.com/ai4bharat/IndicF5.git soundfile scipy librosa "transformers<4.50" 2>&1 \
    | grep -v "requires\|dependency resolver\|dependency conflicts" || true
python -c "import f5_tts, transformers, numpy; print('IndicF5 ok — transformers', transformers.__version__, '— numpy', numpy.__version__)"

echo "=== step 2/4: making the voice dataset (1-3 hours) ==="
python -u "$HERE/make_data.py"

echo "=== step 3/4: installing Piper ==="
cd /content
[ -d piper1-gpl ] || git clone -q https://github.com/OHF-Voice/piper1-gpl.git
cd piper1-gpl
pip install -q scikit-build cmake ninja
pip install -q -e ".[train]" 2>&1 | grep -v "requires\|dependency resolver\|dependency conflicts" || true
bash ./build_monotonic_align.sh
python setup.py build_ext --inplace -q
python -c "import piper.train; print('Piper ok')"

echo "=== step 4/4: training the voice (${TRAIN_HOURS:-4} hours) ==="
python -u "$HERE/train.py"
