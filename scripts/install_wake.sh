#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL=sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01
mkdir -p "$ROOT/models" "$ROOT/runtime"
ARCHIVE="$ROOT/runtime/kws.tar.bz2"
if [[ ! -f "$ARCHIVE" ]]; then
 curl -fL --retry 3 "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$MODEL.tar.bz2" -o "$ARCHIVE.part"
 mv "$ARCHIVE.part" "$ARCHIVE"
fi
python3 - "$ARCHIVE" <<'PY'
import hashlib,sys
with open(sys.argv[1],'rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else None
if digest is None:
    digest=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()
if digest!='b2f7c89690dc8ce4c6ed6afeab7cd800c36ad1421fb6b6302b4a4b194cf7f35f':
    raise SystemExit('Wake model checksum mismatch')
PY
tar -xjf "$ARCHIVE" -C "$ROOT/models"
cp "$ROOT/deploy/xiaolan-keywords.txt" "$ROOT/models/$MODEL/xiaolan-keywords.txt"
echo 'Streaming wake model installed. Restart enmotion-listener after deploying listener.py.'
