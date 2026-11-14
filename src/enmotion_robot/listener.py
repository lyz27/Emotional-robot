"""Streaming keyword wake, latched utterances and audio-onset captions.

Capture continues through the wake earcon; answer playback remains half-duplex.
"""
import array
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import wave
from collections import deque

from .speech import OfflineSpeechEngine, SpeechModelPaths

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "runtime/listener.json"
SOURCE = "alsa_input.usb-0000_UGREEN_Camera_Audio_CM7970001-00.analog-stereo"


def wake_content(text):
    text = re.sub(r"[\s，,。.!！?？]", "", text)
    match = re.match(r"^(?:嘿|嗨|黑|诶|哎)小[蓝兰岚篮](.*)$", text)
    if match:
        return match.group(1)
    if text in {"小蓝", "小兰", "小岚", "小篮"}:
        return ""
    return None


def rms(samples):
    return math.sqrt(sum(x*x for x in samples) / max(1, len(samples)))


def is_goodbye(text):
    clean = re.sub(r'[\s，,。.!！?？、]', '', text).lower()
    clean = re.sub(r'^(?:(?:好的|好了|好|那就|那么|那|嗯))+', '', clean)
    clean = re.sub(r'(?:(?:谢谢|吧|啦|哦|了))+$', '', clean)
    return clean in {'再见','再见小蓝','再见小兰','小蓝再见','小兰再见','拜拜','拜','bye',
                     'byebye','不聊','不聊了','不想聊','不想聊了','先这样','结束对话','停止对话','退出','退出对话'}


def api(path, payload, timeout=120):
    req = urllib.request.Request("http://127.0.0.1:8080" + path,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def publish(phase, text="", emotion="calm", amplitude=0):
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(phase=phase, text=text, emotion=emotion,
        amplitude=amplitude, at=time.time()), ensure_ascii=False))
    os.replace(tmp, STATE)


def play_audio(text, audio, emotion="calm", on_start=None):
    # Only use the known local audio directory, never a model-provided path.
    path = ROOT / "runtime/audio" / Path(audio["audio_url"]).name
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    player = subprocess.Popen(["paplay", "--latency-msec=60", str(path)])
    # PulseAudio startup is not sample-exact; keep captions hidden during buffering.
    start = time.monotonic()+.08
    announced = False
    try:
        while player.poll() is None:
            if time.monotonic() < start:
                time.sleep(.01)
                continue
            if not announced:
                announced = True
                if on_start:
                    on_start()
            pos = int((time.monotonic()-start)*rate)*channels
            part = samples[pos:pos+max(1, rate//10)*channels]
            publish("speaking", text, emotion, min(1, rms(part)/32768*5))
            time.sleep(.1)
        if player.returncode:
            raise RuntimeError("扬声器播放失败")
    finally:
        if player.poll() is None:
            player.terminate()


def speak(text, emotion="calm"):
    publish("thinking", "正在准备语音…", emotion)
    play_audio(text, api("/api/speech/tts", {"text": text}), emotion)


CONTROL_REPLIES = {'goodbye': '再见。', 'present': '我在，请说。', 'identity': '我叫小蓝，是您的本地助手。'}
from .wellness import MESSAGES
CONTROL_REPLIES.update({'care_'+key:text for key,text in MESSAGES.items()})


def prepare_controls():
    manifest = ROOT/'runtime/control-audio-v1.json'
    try:
        cache = json.loads(manifest.read_text())
    except (OSError, ValueError):
        cache = {}
    for key, text in CONTROL_REPLIES.items():
        path = ROOT/'runtime/audio'/Path(cache.get(key, {}).get('audio_url', '')).name
        if not cache.get(key) or not path.is_file():
            cache[key] = api('/api/speech/tts', {'text': text})
    manifest.write_text(json.dumps(cache))
    return cache


def control_reply(key, cache):
    # These clips are local and prepared at startup: no model or synthesis on the hot path.
    play_audio(CONTROL_REPLIES[key], cache[key])


def respond(text):
    if os.getenv('ENMOTION_PCM_STREAM','1')=='1':
        from .pcm_pipeline import run
        return run(text,publish)
    return respond_buffered(text)


def respond_buffered(text):
    """LLM reader -> one ordered TTS worker -> playback, overlapped and bounded."""
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor
    pending = queue.Queue(maxsize=3)
    stopped = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentence-tts")
    emotion = {"value": "calm"}
    started = time.monotonic()
    metrics = {"stage": "pipeline", "first_audio_seconds": None, "sentences": 0}

    def put(item):
        while not stopped.is_set():
            try:
                pending.put(item, timeout=.2)
                return
            except queue.Full:
                pass

    def reader():
        request = urllib.request.Request('http://127.0.0.1:8080/api/companion/stream',
            data=json.dumps({'text': text}).encode(), headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw in response:
                    if stopped.is_set():
                        return
                    event = json.loads(raw)
                    if event['type'] == 'sentence':
                        part = event['text']
                        future = executor.submit(api, '/api/speech/tts', {'text': part})
                        put(('sentence', part, future))
                    elif event['type'] == 'done':
                        emotion['value'] = event.get('emotion', 'calm')
                    elif event['type'] == 'error':
                        put(('error', event['message'], None))
        except Exception:
            put(('error', '回复传输中断，请再试一次。', None))
        finally:
            put(None)

    def first_audio():
        if metrics['first_audio_seconds'] is None:
            metrics['first_audio_seconds'] = round(time.monotonic()-started, 3)

    thread = threading.Thread(target=reader, daemon=True, name='reply-stream')
    thread.start()
    try:
        while True:
            item = pending.get(timeout=150)
            if item is None:
                break
            kind, part, future = item
            if kind == 'error':
                speak(part, 'worried')
                break
            publish('thinking', '正在准备下一句…', emotion['value'])
            audio = future.result(timeout=120)
            play_audio(part, audio, emotion['value'], first_audio)
            metrics['sentences'] += 1
    finally:
        stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)
        thread.join(timeout=2)
        metrics['total_seconds'] = round(time.monotonic()-started, 3)
        print(json.dumps(metrics), flush=True)


class Segmenter:
    """20ms PCM endpointing with pre-roll; session expiry never truncates speech."""
    def __init__(self, silence=1.8, maximum=60):
        self.silence, self.maximum = silence, maximum
        self.reset()

    def reset(self):
        self.pre = deque(maxlen=15)
        self.frames = []
        self.quiet = 0
        self.voiced_frames = 0
        self.noise = .0005

    def accept(self, raw):
        level = rms(array.array("h", raw))/32768
        voiced = level > max(.0025, self.noise*3)
        if not self.frames:
            self.pre.append(raw)
            if not voiced:
                self.noise = self.noise*.98+min(level,.005)*.02
                return None
            self.frames = list(self.pre)
        else:
            self.frames.append(raw)
        self.voiced_frames += int(voiced)
        self.quiet = 0 if voiced else self.quiet+1
        if self.quiet*.02 < self.silence and len(self.frames)*.02 < self.maximum:
            return None
        result = b"".join(self.frames) if self.voiced_frames >= 10 else b""
        self.reset()
        return result


class WakeSpotter:
    def __init__(self):
        import sherpa_onnx
        root = ROOT / "models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
        def model(prefix):
            files = sorted(root.glob(prefix+'*.onnx'), key=lambda p: ('int8' not in p.name, p.name))
            if not files:
                raise RuntimeError("缺少流式唤醒模型："+prefix)
            return str(files[0])
        self.model = sherpa_onnx.KeywordSpotter(tokens=str(root/'tokens.txt'),
            encoder=model('encoder'), decoder=model('decoder'), joiner=model('joiner'),
            keywords_file=str(root/'xiaolan-keywords.txt'), num_threads=1,
            keywords_score=2.5, keywords_threshold=.15, num_trailing_blanks=1)
        self.reset()

    def reset(self):
        self.stream = self.model.create_stream()
        self.end_stream = self.model.create_stream('z ài j iàn @再见/b ài b ài @拜拜/t íng zh ǐ d uì h uà @停止对话')

    def accept_end(self, raw):
        import numpy as np
        self.end_stream.accept_waveform(16000,np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768)
        while self.model.is_ready(self.end_stream):
            self.model.decode_stream(self.end_stream)
            if self.model.get_result(self.end_stream):
                self.model.reset_stream(self.end_stream)
                return True
        return False

    def accept(self, raw):
        import numpy as np
        self.stream.accept_waveform(16000,np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768)
        while self.model.is_ready(self.stream):
            self.model.decode_stream(self.stream)
            if self.model.get_result(self.stream):
                self.reset()
                return True
        return False


def cue():
    """Short non-speech earcon; capture continues, including immediate user speech."""
    path = ROOT / "runtime/wake-cue.wav"
    if not path.exists():
        values = array.array('h', (int(2800*math.sin(i*2*math.pi*880/16000)*math.sin(math.pi*i/1280)) for i in range(1280)))
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(values.tobytes())
    return subprocess.Popen(['paplay','--latency-msec=40',str(path)])


def main():
    publish("thinking", "正在初始化语音系统…")
    engine = OfflineSpeechEngine(SpeechModelPaths(ROOT / "models"), num_threads=2)
    online = None
    if os.getenv('ENMOTION_STREAMING_ASR','1')=='1':
        from .online_audio import OnlineSpeech, EndpointPolicy
        online = OnlineSpeech(ROOT/'models/sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16')
        endpoint = EndpointPolicy()
    else:
        engine._get_recognizer()
    wake = WakeSpotter()
    controls = prepare_controls()
    active_until = 0
    silence = float(os.getenv('ENMOTION_END_SILENCE', '1.8'))
    last_care_poll = 0
    idle_quiet = 0
    delivered = set()
    while True:
        capture = None
        try:
            capture = subprocess.Popen(["parec", "--device="+SOURCE, "--format=s16le",
                "--rate=16000", "--channels=1", "--latency-msec=60"], stdout=subprocess.PIPE)
            segment = Segmenter(silence=5.0 if online else silence)
            if online: online.reset()
            hypothesis = ''
            changed_at = time.monotonic()
            heartbeat = 0
            maybe_end = False
            last_idle_reset = time.monotonic()
            while True:
                raw = capture.stdout.read(640)
                if len(raw) != 640:
                    raise RuntimeError("麦克风断开")
                now = time.monotonic()
                active = now < active_until or bool(segment.frames)
                if not active:
                    idle_quiet = idle_quiet+1 if rms(array.array('h',raw))/32768 < .0025 else 0
                    if now-last_idle_reset > 60:
                        wake.reset(); last_idle_reset=now
                    if wake.accept(raw):
                        print(json.dumps({'stage':'wake','at':time.time()}),flush=True)
                        active_until = now+45
                        segment.reset()
                        if online: online.reset()
                        hypothesis = ''
                        cue_player = cue()
                        # Reap the short cue without blocking the microphone.
                        import threading
                        threading.Thread(target=cue_player.wait,daemon=True).start()
                        publish('listening','我在听，请直接说。','curious')
                        heartbeat=now
                    elif now-heartbeat > 1:
                        publish('waiting','说“嘿，小蓝”唤醒我'); heartbeat=now
                    elif idle_quiet>=40 and now-last_care_poll>=2:
                        last_care_poll=now
                        try:
                            job=api('/api/wellness/claim',{},timeout=.4).get('job')
                        except Exception:
                            job=None
                        if job and 'care_'+job['kind'] in controls:
                            capture.terminate();capture.wait(timeout=3)
                            if job['id'] not in delivered:
                                control_reply('care_'+job['kind'],controls)
                                delivered.add(job['id'])
                            try:api('/api/wellness/ack',{'id':job['id']},timeout=.4)
                            except Exception:pass
                            if len(delivered)>100:delivered={job['id']}
                            idle_quiet=0;wake.reset()
                            publish('waiting','说“嘿，小蓝”唤醒我')
                            break
                    continue
                if now-heartbeat > .3:
                    publish('listening',('您：'+hypothesis[-60:]) if hypothesis else '我在听，您说完后稍停一下。','curious'); heartbeat=now
                if online:
                    partial = online.accept(raw)
                    if partial != hypothesis:
                        hypothesis, changed_at = partial, now
                else:
                    maybe_end = wake.accept_end(raw) or maybe_end
                completed = segment.accept(raw)
                if online and segment.frames:
                    quiet = segment.quiet*.02
                    end_text = wake_content(hypothesis)
                    ending = is_goodbye(end_text if end_text is not None else hypothesis)
                    if (ending and quiet>=.6 and now-changed_at>=.25) or endpoint.ready(hypothesis, quiet, now-changed_at):
                        completed = b''.join(segment.frames)
                if not online and maybe_end and segment.frames and segment.quiet >= 28 and len(segment.frames) < 200:
                    # Confirm the short intent with ASR: discussing "再见这首歌" must not exit.
                    maybe_end = False
                    early = array.array('h', b''.join(segment.frames))
                    check = engine.recognize([x/32768 for x in early],16000)
                    content = wake_content(check)
                    if is_goodbye(content if content is not None else check):
                        capture.terminate(); capture.wait(timeout=3)
                        active_until = 0
                        control_reply('goodbye', controls)
                        wake.reset()
                        publish('waiting','本轮已结束，说“嘿，小蓝”再次唤醒。')
                        print(json.dumps({'stage':'local_control','intent':'goodbye','fast_endpoint':True}),flush=True)
                        break
                if completed is None or not completed:
                    continue
                # Capture remains open until a complete utterance has been collected.
                capture.terminate(); capture.wait(timeout=3)
                publish('recognizing','正在识别完整语句…','thinking')
                samples = array.array('h', completed)
                started=time.monotonic()
                text = online.finish() if online else engine.recognize([x/32768 for x in samples],16000)
                print(json.dumps({'stage':'asr','mode':'online' if online else 'offline','seconds':round(time.monotonic()-started,2),'audio_seconds':len(samples)/16000}),flush=True)
                content = wake_content(text)
                if content is not None:
                    text = content
                if not text.strip():
                    active_until=time.monotonic()+45
                    break
                if is_goodbye(text):
                    active_until=0
                    control_reply('goodbye', controls)
                    publish('waiting','本轮已结束，说“嘿，小蓝”再次唤醒。')
                    print(json.dumps({'stage':'local_control','intent':'goodbye','fast_endpoint':False}),flush=True)
                elif re.sub(r'[\s，,。.!！?？]', '', text) in {'在吗','你在吗','小蓝在吗','你还在吗'}:
                    control_reply('present', controls)
                    active_until=time.monotonic()+45
                elif re.sub(r'[\s，,。.!！?？]', '', text) in {'你叫什么名字','你是谁','你叫什么'}:
                    control_reply('identity', controls)
                    active_until=time.monotonic()+45
                else:
                    publish('thinking','正在整理回答…','thinking')
                    respond(text)
                    active_until=time.monotonic()+45
                wake.reset()
                break
        except Exception as exc:
            publish('error','语音暂不可用，正在重连。')
            print(type(exc).__name__,str(exc)[:200],flush=True)
            time.sleep(3)
        finally:
            if capture and capture.poll() is None:
                capture.terminate(); capture.wait(timeout=3)


if __name__ == "__main__":
    main()
