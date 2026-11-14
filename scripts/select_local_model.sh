#!/bin/bash
# Run as root; stop the desktop listener during a model switch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHOICE="${1:-}"
case "$CHOICE" in 1.7b|4b|router) ;; *) echo 'Usage: sudo bash scripts/select_local_model.sh router|1.7b|4b'; exit 2;; esac
if [[ "$CHOICE" != 4b ]]; then
 python3 - "$ROOT/models/llm/Qwen3-1.7B-Q4_K_M.gguf" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    while data:=f.read(1024*1024):h.update(data)
if h.hexdigest()!='b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897':
    raise SystemExit('Model checksum mismatch')
PY
fi
configure() {
 python3 - "$ROOT" "$1" <<'PY'
from pathlib import Path
import sys
root,choice=Path(sys.argv[1]),sys.argv[2]
unit=(root/'deploy/enmotion-llm.service').read_text()
alias='qwen3-4b-q4'
work_alias=alias
if choice=='router':
    unit=(root/'deploy/enmotion-llm-router.service').read_text()
    alias='qwen3-1.7b-q4'
elif choice=='1.7b':
    unit=unit.replace('Qwen3-4B-Q4_K_M.gguf','Qwen3-1.7B-Q4_K_M.gguf').replace('qwen3-4b-q4','qwen3-1.7b-q4')
    alias='qwen3-1.7b-q4'
    work_alias=alias
Path('/etc/systemd/system/enmotion-llm.service').write_text(unit)
folder=Path('/etc/systemd/system/enmotion-runtime.service.d');folder.mkdir(exist_ok=True)
(folder/'enmotion-local-model.conf').write_text('[Service]\nEnvironment=ENMOTION_DIALOGUE_MODEL='+alias+'\nEnvironment=ENMOTION_WORK_MODEL='+work_alias+'\n')
PY
 systemctl daemon-reload
 systemctl restart enmotion-llm
}
ready() {
 for attempt in {1..60}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8081/health >/dev/null; then return 0; fi
  sleep 2
 done
 return 1
}
cp -n /etc/systemd/system/enmotion-llm.service /etc/systemd/system/enmotion-llm.service.before-model-test
configure "$CHOICE"
if ! ready; then
 echo 'Candidate did not become ready; restoring 4B' >&2
 configure 4b
 ready
 systemctl restart enmotion-runtime
 exit 1
fi
if [[ "$CHOICE" == router ]]; then
 if ! python3 - <<'PY'
import json,time,urllib.request
req=urllib.request.Request('http://127.0.0.1:8081/models/load',data=b'{"model":"qwen3-1.7b-q4"}',headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=150) as response: json.load(response)
for _ in range(150):
    with urllib.request.urlopen('http://127.0.0.1:8081/models',timeout=5) as response: result=json.load(response)
    if any(m['id']=='qwen3-1.7b-q4' and m['status']['value']=='loaded' for m in result['data']):
        break
    time.sleep(1)
else:
    raise SystemExit('Chat model did not finish loading')
PY
 then
  configure 4b
  ready
  systemctl restart enmotion-runtime
  exit 1
 fi
fi
systemctl restart enmotion-runtime
echo "Selected $CHOICE. The 4B model file remains available for rollback."
