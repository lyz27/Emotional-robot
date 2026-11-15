"""Bounded PCM transport and paced continuous PulseAudio playback."""
import array
import base64
import json
import math
import queue
import subprocess
import threading
import time
import urllib.request
import uuid


def events(path, payload):
    request=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:
        for line in response:
            yield json.loads(line)


class PCMOutput:
    def __init__(self, publish, first_audio):
        self.publish,self.first_audio=publish,first_audio
        self.player=None
        self.rate=None
        self.next_at=None
        self.caption=None
        self.current=''
        self.emotion='calm'
        self.last_publish=0
        self.announced=False

    def start(self, rate):
        if self.player:
            if self.rate!=rate: raise RuntimeError('PCM sample rate changed')
            return
        self.rate=rate
        self.player=subprocess.Popen(['pacat','--playback','--raw','--format=s16le',f'--rate={rate}',
            '--channels=1','--latency-msec=60'],stdin=subprocess.PIPE)

    def write(self, pcm, text, emotion):
        if not self.player or not pcm or len(pcm)%2: raise RuntimeError('Invalid PCM stream')
        now=time.monotonic()
        self.next_at=max(self.next_at or now,now)
        if self.current!=text and (not self.caption or self.caption[0]!=text):
            self.caption=(text,emotion,self.next_at+.06)
        self.player.stdin.write(pcm)
        self.player.stdin.flush()
        samples=array.array('h',pcm)
        amplitude=min(1,math.sqrt(sum(v*v for v in samples)/max(1,len(samples)))/32768*5)
        self.next_at+=len(pcm)/(2*self.rate)
        if self.caption and now>=self.caption[2]:
            self.current,self.emotion,_=self.caption
            self.caption=None
            if not self.announced:
                self.announced=True;self.first_audio()
        if self.current and now-self.last_publish>=.08:
            self.publish('speaking',self.current,self.emotion,amplitude)
            self.last_publish=now
        time.sleep(max(0,self.next_at-time.monotonic()))

    def idle(self):
        if self.current:
            self.publish('speaking',self.current,self.emotion,0)

    def finish(self):
        if self.player:
            time.sleep(max(0,(self.next_at or 0)+.08-time.monotonic()))
            self.player.stdin.close()
            if self.player.wait(timeout=5): raise RuntimeError('PCM playback failed')
            self.player=None

    def close(self):
        if self.player:
            self.player.terminate()
            self.player.wait(timeout=3)


def run(text,publish, event_source=events, output_factory=PCMOutput):
    stopped=threading.Event()
    phrases=queue.Queue(maxsize=3)
    prepared=queue.Queue(maxsize=3)
    emotion={'value':'calm'}
    started=time.monotonic()
    metrics={'stage':'pcm_pipeline','first_audio_seconds':None,'sentences':0,'pcm_packets':0}

    def put(q,item):
        while not stopped.is_set():
            try:q.put(item,timeout=.1);return
            except queue.Full:pass

    def read_model():
        try:
            for event in event_source('/api/companion/stream',{'text':text,'request_id':uuid.uuid4().hex}):
                if stopped.is_set():break
                if event['type']=='sentence':put(phrases,event['text'])
                elif event['type']=='status':publish('thinking',event.get('text','正在查询…'),'thinking')
                elif event['type']=='done':emotion['value']=event.get('emotion','calm')
                elif event['type']=='error':put(phrases,event['message']);break
        except Exception:put(phrases,'回复传输中断，请再试一次。')
        finally:put(phrases,None)

    def make_audio():
        try:
            while not stopped.is_set():
                try:part=phrases.get(timeout=.1)
                except queue.Empty:continue
                if part is None:break
                packets=queue.Queue(maxsize=100)
                put(prepared,(part,packets))
                try:
                    for event in event_source('/api/speech/pcm',{'text':part}):
                        if stopped.is_set():break
                        put(packets,event)
                except Exception:put(packets,{'type':'error','message':'音频连接中断'})
                finally:put(packets,None)
        finally:put(prepared,None)

    def first_audio():
        metrics['first_audio_seconds']=round(time.monotonic()-started,3)

    output=output_factory(publish,first_audio)
    threads=[threading.Thread(target=f,daemon=True) for f in (read_model,make_audio)]
    for thread in threads:thread.start()
    try:
        while True:
            item=prepared.get(timeout=150)
            if item is None:break
            part,packets=item
            header=False;complete=False;sample_packets=0
            while True:
                try:event=packets.get(timeout=.1)
                except queue.Empty:output.idle();continue
                if event is None:
                    if not complete or not sample_packets:raise RuntimeError('Incomplete PCM stream')
                    break
                if event['type']=='audio':
                    if header:raise RuntimeError('Duplicate PCM header')
                    rate=event.get('sample_rate')
                    if event.get('format')!='s16le' or event.get('channels')!=1 or type(rate)!=int or not 8000<=rate<=48000:
                        raise RuntimeError('Unsupported PCM format')
                    output.start(rate)
                    header=True
                elif event['type']=='pcm':
                    if not header or complete:raise RuntimeError('Unexpected PCM packet')
                    pcm=base64.b64decode(event['data'],validate=True)
                    if not pcm or len(pcm)%2 or len(pcm)>192000:raise RuntimeError('Invalid PCM packet')
                    output.write(pcm,part,emotion['value']);metrics['pcm_packets']+=1
                    sample_packets+=1
                elif event['type']=='done':complete=True
                elif event['type']=='error':raise RuntimeError(event['message'])
            metrics['sentences']+=1
        output.finish()
    finally:
        stopped.set();output.close()
        for thread in threads:thread.join(timeout=2)
        metrics['total_seconds']=round(time.monotonic()-started,3)
        print(json.dumps(metrics),flush=True)
    return metrics
