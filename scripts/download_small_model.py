"""Resumable ranged download of the benchmark model, with pinned SHA256."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SIZE = 1107409472
SHA = 'b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897'
URL = 'https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf'

if __name__ == '__main__':
    folder = ROOT/'runtime/model-parts'
    folder.mkdir(parents=True, exist_ok=True)
    step = 8*1024*1024
    count = (SIZE+step-1)//step
    def fetch(i):
        start, end = i*step, min(SIZE, (i+1)*step)-1
        path = folder/str(i)
        if path.exists() and path.stat().st_size == end-start+1:
            return i
        for attempt in range(5):
            try:
                req = urllib.request.Request(URL+f'?download=true&chunk={i}', headers={'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(req, timeout=90) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{SIZE}':
                        raise RuntimeError('Server returned unexpected range')
                    with path.with_suffix('.part').open('wb') as output:
                        while data := response.read(1024*1024): output.write(data)
                if path.with_suffix('.part').stat().st_size != end-start+1:
                    raise RuntimeError('Incomplete part')
                path.with_suffix('.part').replace(path)
                return i
            except Exception:
                if attempt == 4: raise
                time.sleep(2)
    with ThreadPoolExecutor(max_workers=12) as pool:
        for done, f in enumerate(as_completed([pool.submit(fetch,i) for i in range(count)]), 1):
            f.result()
            print(f'{done}/{count} parts verified', flush=True)
    output = ROOT/'runtime/Qwen3-1.7B-Q4_K_M.gguf'
    digest = hashlib.sha256()
    with output.with_suffix('.assembling').open('wb') as stream:
        for i in range(count):
            with (folder/str(i)).open('rb') as part:
                while data := part.read(1024*1024):
                    digest.update(data); stream.write(data)
    if digest.hexdigest() != SHA:
        raise SystemExit('Model SHA256 mismatch')
    output.with_suffix('.assembling').replace(output)
    print('SHA256 verified: '+str(output),flush=True)
