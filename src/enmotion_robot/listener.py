"""Local utterance-based wake listener. Playback pauses microphone capture."""
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
    publish("thinking", text, emotion)
    audio = api("/api/speech/tts", {"text": text})
    # Only use the known local audio directory, never a model-provided path.
    path = ROOT / "runtime/audio" / Path(audio["audio_url"]).name
    with wave.open(str(path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    player = subprocess.Popen(["paplay", str(path)])
    start = time.monotonic()
    try:
        while player.poll() is None:
            pos = int((time.monotonic()-start)*rate)*channels
            part = samples[pos:pos+max(1, rate//10)*channels]
            publish("speaking", text, emotion, min(1, rms(part)/32768*5))
            time.sleep(.1)
        if player.returncode:
            raise RuntimeError("扬声器播放失败")
    finally:
        if player.poll() is None:
            player.terminate()


def main():
    engine = OfflineSpeechEngine(SpeechModelPaths(ROOT / "models"), num_threads=2)
    engine._get_recognizer()  # Warm up before reporting ready.
    active_until = 0
    while True:
        capture = None
        try:
            capture = subprocess.Popen(["parec", "--device="+SOURCE, "--format=s16le",
                "--rate=16000", "--channels=1"], stdout=subprocess.PIPE)
            frames, quiet, noise = [], 0, .001
            heartbeat = 0
            while True:
                raw = capture.stdout.read(640)
                if len(raw) != 640:
                    raise RuntimeError("麦克风断开")
                pcm = array.array("h", raw)
                level = rms(pcm)/32768
                now = time.monotonic()
                if now-heartbeat > 1:
                    publish("listening" if now < active_until else "waiting",
                            "我在听，请说。" if now < active_until else "说“嘿，小蓝”唤醒我")
                    heartbeat = now
                voiced = level > max(.004, noise*3)
                if not frames and not voiced:
                    noise = noise*.98 + min(level, .01)*.02
                    continue
                frames.append(raw)
                quiet = 0 if voiced else quiet+1
                if quiet < 40 and len(frames) < 600:
                    continue
                capture.terminate(); capture.wait(timeout=3)
                if len(frames) < 15:
                    break
                samples = array.array("h", b"".join(frames))
                publish("recognizing", "正在听清你的话…")
                text = engine.recognize([x/32768 for x in samples], 16000)
                content = wake_content(text)
                active = time.monotonic() < active_until
                if content is None and not active:
                    break
                if content is not None:
                    text = content
                if not text.strip():
                    speak("我在，请说。", "curious")
                elif any(word in text for word in ("停止对话", "再见小蓝", "再见小兰")):
                    speak("好的，需要我时再叫我。")
                    active_until = 0
                    break
                else:
                    publish("thinking", "让我想一想…", "thinking")
                    result = api("/api/companion/chat", {"text": text,
                        "session_id": "voice", "remember": False, "defer_expression": True})
                    speak(result["reply"], result.get("action", {}).get("emotion", "calm"))
                active_until = time.monotonic()+20
                break
        except Exception as exc:
            publish("error", "语音暂不可用，正在重连。")
            print(type(exc).__name__, str(exc)[:200], flush=True)
            time.sleep(3)
        finally:
            if capture and capture.poll() is None:
                capture.terminate()
                capture.wait(timeout=3)


if __name__ == "__main__":
    main()
