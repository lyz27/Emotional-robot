"""Live local-model A/B benchmark; no microphone, speaker or user memory writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import urllib.request


def post(base, path, payload):
    request = urllib.request.Request(base+path, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
    return urllib.request.urlopen(request, timeout=120)


def benchmark(base, text):
    started = time.monotonic()
    parts, futures = [], []
    report = {'question': text, 'first_sentence_seconds': None, 'first_audio_ready_seconds': None}
    def tts(part):
        with post(base, '/api/speech/tts', {'text':part}) as response:
            json.load(response)
        return time.monotonic()-started
    with ThreadPoolExecutor(max_workers=1) as executor:
        with post(base, '/api/companion/stream', {'text':text}) as response:
            for raw in response:
                event = json.loads(raw)
                if event['type']=='error':
                    raise RuntimeError(event['message'])
                if event['type']=='sentence':
                    if not parts: report['first_sentence_seconds'] = round(time.monotonic()-started,3)
                    parts.append(event['text'])
                    futures.append(executor.submit(tts,event['text']))
                if event['type']=='done': report['full_model_seconds'] = round(time.monotonic()-started,3)
        assert futures
        report['first_audio_ready_seconds'] = round(futures[0].result(),3)
        for future in futures: future.result()
    report.update(reply=''.join(parts), sentences=len(parts), all_audio_ready_seconds=round(time.monotonic()-started,3))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--base',default='http://127.0.0.1:8080')
    args=parser.parse_args()
    with post(args.base,'/api/speech/tts',{'text':'测试。'}) as r: json.load(r)
    results=[]
    for text in ('我今天有点累，简单说两句。','准备一个明天演示的方案，简短说出两步。','你现在能查到我这里的实时天气吗？'):
        result=benchmark(args.base,text)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        results.append(result)
    Path(args.output).write_text(json.dumps({'label':args.label,'results':results},ensure_ascii=False,indent=2))
