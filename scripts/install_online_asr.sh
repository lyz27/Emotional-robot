#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL=sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16
mkdir -p "$ROOT/models" "$ROOT/runtime"
ARCHIVE="$ROOT/runtime/online-asr.tar.bz2"
if [[ ! -f "$ARCHIVE" ]]; then
 curl -fL --retry 3 "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$MODEL.tar.bz2" -o "$ARCHIVE.part"
 mv "$ARCHIVE.part" "$ARCHIVE"
fi
python3 - "$ARCHIVE" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
if h.hexdigest()!='2b7c63322b32e5e0f2526043a1103366119ca58dd615cd7105a37c01db9553d7':
    raise SystemExit('Online ASR archive checksum mismatch')
PY
tar -xjf "$ARCHIVE" -C "$ROOT/models" "$MODEL/tokens.txt" \
 "$MODEL/encoder-epoch-99-avg-1.int8.onnx" "$MODEL/decoder-epoch-99-avg-1.onnx" \
 "$MODEL/joiner-epoch-99-avg-1.int8.onnx"
echo 'Online ASR installed. Deploy Python files before restarting enmotion-listener.'
