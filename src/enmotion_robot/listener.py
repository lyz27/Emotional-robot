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


def api(path, payload):
    req = urllib.request.Request("http://127.0.0.1:8080" + path,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def publish(phase, text="", emotion="calm", amplitude=0):
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(phase=phase, text=text, emotion=emotion,
        amplitude=amplitude, at=time.time()), ensure_ascii=False))
    os.replace(tmp, STATE)


def speak(text, emotion="calm"):
    publish("thinking", "正在准备语音…", emotion)
    audio = api("/api/speech/tts", {"text": text})
    # Only use the known local audio directory, never a model-provided path.
    path = ROOT / "runtime/audio" / Path(audio["audio_url"]).name
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    player = subprocess.Popen(["paplay", "--latency-msec=60", str(path)])
    # PulseAudio startup is not sample-exact; keep captions hidden during buffering.
    start = time.monotonic()+.08
    try:
        while player.poll() is None:
            if time.monotonic() < start:
                time.sleep(.01)
                continue
            pos = int((time.monotonic()-start)*rate)*channels
            part = samples[pos:pos+max(1, rate//10)*channels]
            publish("speaking", text, emotion, min(1, rms(part)/32768*5))
            time.sleep(.1)
        if player.returncode:
            raise RuntimeError("扬声器播放失败")
    finally:
        if player.poll() is None:
            player.terminate()


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
    engine._get_recognizer()
    wake = WakeSpotter()
    active_until = 0
    silence = float(os.getenv('ENMOTION_END_SILENCE', '1.8'))
    while True:
        capture = None
        try:
            capture = subprocess.Popen(["parec", "--device="+SOURCE, "--format=s16le",
                "--rate=16000", "--channels=1", "--latency-msec=60"], stdout=subprocess.PIPE)
            segment = Segmenter(silence=silence)
            heartbeat = 0
            last_idle_reset = time.monotonic()
            while True:
                raw = capture.stdout.read(640)
                if len(raw) != 640:
                    raise RuntimeError("麦克风断开")
                now = time.monotonic()
                active = now < active_until or bool(segment.frames)
                if not active:
                    if now-last_idle_reset > 60:
                        wake.reset(); last_idle_reset=now
                    if wake.accept(raw):
                        print(json.dumps({'stage':'wake','at':time.time()}),flush=True)
                        active_until = now+45
                        segment.reset()
                        cue_player = cue()
                        # Reap the short cue without blocking the microphone.
                        import threading
                        threading.Thread(target=cue_player.wait,daemon=True).start()
                        publish('listening','我在听，请直接说。','curious')
                        heartbeat=now
                    elif now-heartbeat > 1:
                        publish('waiting','说“嘿，小蓝”唤醒我'); heartbeat=now
                    continue
                if now-heartbeat > .3:
                    publish('listening','我在听，您说完后稍停一下。','curious'); heartbeat=now
                completed = segment.accept(raw)
                if completed is None or not completed:
                    continue
                # Capture remains open until a complete utterance has been collected.
                capture.terminate(); capture.wait(timeout=3)
                publish('recognizing','正在识别完整语句…','thinking')
                samples = array.array('h', completed)
                started=time.monotonic()
                text = engine.recognize([x/32768 for x in samples],16000)
                print(json.dumps({'stage':'asr','seconds':round(time.monotonic()-started,2),'audio_seconds':len(samples)/16000}),flush=True)
                content = wake_content(text)
                if content is not None:
                    text = content
                if not text.strip():
                    active_until=time.monotonic()+45
                    break
                if any(word in text for word in ('停止对话','再见小蓝','再见小兰')):
                    speak('收到，本轮对话结束。'); active_until=0
                else:
                    publish('thinking','正在整理回答…','thinking')
                    result=api('/api/companion/chat',{'text':text,'session_id':'voice','remember':False,'defer_expression':True})
                    speak(result['reply'],result.get('action',{}).get('emotion','calm'))
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
