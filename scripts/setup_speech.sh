#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
model_dir="${ENMOTION_MODEL_DIR:-${project_dir}/models}"
venv_dir="${project_dir}/.venv"

cd "${project_dir}"
mkdir -p "${model_dir}"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  python3 -m venv "${venv_dir}"
fi

"${venv_dir}/bin/pip" install --upgrade \
  "numpy>=1.24,<3" \
  "sherpa-onnx>=1.12,<3" \
  "sherpa-onnx-bin>=1.12,<3" \
  "python-multipart>=0.0.20,<1"

download_extract() {
  local url="$1"
  local expected_dir="$2"
  if [[ -d "${model_dir}/${expected_dir}" ]]; then
    echo "Model already present: ${expected_dir}"
    return
  fi
  local archive
  archive="$(mktemp "${model_dir}/download.XXXXXX.tar.bz2")"
  curl --fail --location --retry 3 --output "${archive}" "${url}"
  tar -xjf "${archive}" -C "${model_dir}"
  rm -f "${archive}"
}

asr_dir="${model_dir}/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
asr_base="https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"
mkdir -p "${asr_dir}/test_wavs"
if [[ ! -f "${asr_dir}/model.int8.onnx" ]]; then
  curl --fail --location --retry 3 \
    --output "${asr_dir}/model.int8.onnx" \
    "${asr_base}/model.int8.onnx"
fi
for relative_path in tokens.txt test_wavs/zh.wav; do
  if [[ ! -f "${asr_dir}/${relative_path}" ]]; then
    curl --fail --location --retry 3 \
      --output "${asr_dir}/${relative_path}" \
      "${asr_base}/${relative_path}"
  fi
done

if [[ ! -f "${model_dir}/silero_vad.onnx" ]]; then
  curl --fail --location --retry 3 \
    --output "${model_dir}/silero_vad.onnx" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
fi

download_extract \
  "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-vits-zh-ll.tar.bz2" \
  "sherpa-onnx-vits-zh-ll"

"${venv_dir}/bin/python" - <<'PY'
from pathlib import Path
from enmotion_robot.speech import SpeechModelPaths

root = Path(__import__("os").environ.get("ENMOTION_MODEL_DIR", "models"))
status = SpeechModelPaths(root).readiness()
print(f"Speech model readiness: {status}")
if not all(status.values()):
    raise SystemExit("One or more speech models are incomplete")
PY

echo "Offline speech runtime is ready."
