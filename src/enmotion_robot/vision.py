"""CPU-only visual perception and deterministic multimodal arbitration.

No identity recognition, camera frame storage, or physical motor commands.
"""
from __future__ import annotations
import argparse
from collections import deque
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import threading
import time


class Detector:
    def __init__(self):
        import cv2
        cv2.setNumThreads(1)
        self.cv = cv2
        self.face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.face.empty():
            raise RuntimeError("Face cascade unavailable")
        self.people = cv2.HOGDescriptor()
        self.people.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame):
        cv = self.cv
        h, w = frame.shape[:2]
        if w > 640:
            frame = cv.resize(frame, (640, round(h*640/w)))
        h, w = frame.shape[:2]
        gray = cv.equalizeHist(cv.cvtColor(frame, cv.COLOR_BGR2GRAY))
        faces = self.face.detectMultiScale(gray, 1.15, 5, minSize=(35, 35))
        # Detect both classes; prefer faces for close-range desk tracking.
        bodies, _ = self.people.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.08) if h >= 128 and w >= 64 else ([], [])
        result = []
        for kind, boxes in (("face", faces), ("person", bodies)):
            for x, y, bw, bh in boxes:
                result.append({"kind": kind, "box": [float(x/w), float(y/h), float(bw/w), float(bh/h)],
                               "x": float((x+bw/2)/w*2-1), "y": float((y+bh/2)/h*2-1),
                               "area": float(bw*bh/(w*h))})
        return result


class Orchestrator:
    def __init__(self, robot, clock=time.monotonic):
        self.robot, self.clock = robot, clock
        self.lock = threading.RLock()
        self.started = clock()
        self.last_seen = self.started
        self.first_seen = None
        self.present = False
        self.target = None
        self.anchor_area = 0
        self.anchor_kind = None
        self.missing_since = self.started
        self.empty_sent = False
        self.touch_until = 0
        self.touch_was_down = False
        self.emotion_until = 0
        self.visual_emotion = "calm"
        self.owned_emotion = None
        self.last_event = {}
        self.events = deque(maxlen=30)
        self.last_tick = self.started
        self.state = {"online": False, "source": "none", "detections": [], "priority": "idle", "events": []}

    def event(self, name, now):
        if now-self.last_event.get(name, -1000) < 5:
            return
        self.last_event[name] = now
        self.events.appendleft({"event": name, "time": round(now-self.started, 2)})
        self.visual_emotion = {"arrived": "curious", "approaching": "happy", "left": "calm", "empty": "sleepy"}[name]
        self.emotion_until = now+4

    def touch(self):
        with self.lock:
            self.touch_until = self.clock()+2

    def observe(self, detections, *, online=True, source="camera", now=None):
        now = self.clock() if now is None else now
        with self.lock:
            self.state.update(online=online, source=source, detections=detections, updated_at=time.time())
            if not online:
                # Camera failure is not evidence that a person left.
                self.target = None
                self.present = False
                self.first_seen = None
                self.empty_sent = False
                self.missing_since = now
                return
            faces = [d for d in detections if d["kind"] == "face"]
            choices = faces or detections
            if not faces and self.target and self.target["kind"] == "face" and now-self.last_seen < .8:
                choices = []  # retain the face briefly instead of jumping to a HOG false positive
            if choices:
                # Prefer continuity over switching instantly to a newly larger face.
                reference = self.target
                target = min(choices, key=lambda d: (d["x"]-reference["x"])**2 + (d["y"]-reference["y"])**2) if reference else max(choices, key=lambda d: d["area"])
                self.target = dict(target)
                self.first_seen = now if self.first_seen is None else self.first_seen
                self.last_seen = now
                self.empty_sent = False
                if not self.present and now-self.first_seen >= .5:
                    self.present = True
                    self.anchor_area = target["area"]
                    self.anchor_kind = target["kind"]
                    self.event("arrived", now)
                if self.present and self.anchor_kind != target["kind"]:
                    self.anchor_area, self.anchor_kind = target["area"], target["kind"]
                if self.present and self.anchor_kind == target["kind"] and self.anchor_area > 0 and target["area"] > self.anchor_area*1.5:
                    self.event("approaching", now)
                    self.anchor_area = target["area"]
            else:
                self.first_seen = None
                if self.present and now-self.last_seen >= 1.5:
                    self.present = False
                    self.target = None
                    self.missing_since = now
                    self.event("left", now)
                elif not self.present and not self.empty_sent and now-self.missing_since >= 30:
                    self.event("empty", now)
                    self.empty_sent = True

    def tick(self, listener=None, *, speech_busy=False, now=None):
        now = self.clock() if now is None else now
        listener = listener or {}
        with self.lock:
            dt = max(0, min(.25, now-self.last_tick))
            self.last_tick = now
            sim = self.robot.snapshot()
            down = sim["sensors"]["touch"]
            if down and not self.touch_was_down:
                self.touch_until = now+2
            self.touch_was_down = down
            voice = listener.get("phase") in {"listening", "recognizing", "thinking", "speaking"} or speech_busy
            touching = now < self.touch_until
            priority = "voice" if voice else "touch" if touching else "vision" if self.present and self.target else "idle"
            target = self.target if priority == "vision" else None
            x = max(-1., min(1., target["x"])) if target else 0.
            y = max(-1., min(1., target["y"])) if target else 0.
            alpha = 1-math.exp(-dt*5)
            if priority != "idle" or self.state.get("owner") in {"vision", "touch", "voice"}:
                for command, old, desired, eps in (
                    ("set_gaze", sim["gaze"], {"x": x, "y": y}, .015),
                    ("set_neck", sim["neck"], {"yaw": x*25, "pitch": -y*12}, .6),
                ):
                    values = {k: old[k]+(v-old[k])*alpha for k, v in desired.items()}
                    if any(abs(values[k]-old[k]) > eps for k in values):
                        self.robot.command(command, **values)
            if touching and not voice:
                emotion = "happy"
            elif voice:
                emotion = listener.get("emotion", "thinking") if listener.get("phase") == "speaking" else "curious" if listener.get("phase") == "listening" else "thinking"
            elif now < self.emotion_until:
                emotion = self.visual_emotion
            else:
                emotion = None
            if not emotion and self.owned_emotion == sim["emotion"]:
                emotion = "calm"
                self.owned_emotion = None
            elif emotion:
                self.owned_emotion = emotion
            if emotion and sim["emotion"] != emotion:
                self.robot.command("set_emotion", emotion=emotion)
            current = self.robot.snapshot()
            returning = priority == "idle" and self.state.get("owner") in {"voice", "touch", "vision"} and (
                any(abs(v) > .6 for v in current["neck"].values()) or any(abs(v) > .015 for v in current["gaze"].values()))
            self.state.update(priority=priority, owner="vision" if returning else priority, present=self.present, target=self.target if self.present else None,
                              events=list(self.events), proximity="relative_image_area_only")
            self.state.update(gaze=current["gaze"], neck=current["neck"])
            return deepcopy(self.state)

    def snapshot(self):
        with self.lock:
            return deepcopy({**self.state, "events": list(self.events), "present": self.present})


class VisionService:
    def __init__(self, orchestrator, listener, speech_busy=lambda: False):
        self.orchestrator, self.listener, self.speech_busy = orchestrator, listener, speech_busy
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if os.getenv("ENMOTION_VISION_ENABLED", "1") == "0":
            return
        self.thread = threading.Thread(target=self.run, daemon=True, name="vision")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def run(self):
        capture = None
        while not self.stop_event.is_set():
            try:
                detector = Detector()
                cv = detector.cv
                source = os.getenv("ENMOTION_CAMERA", "0")
                source = int(source) if source.isdigit() else source
                capture = cv.VideoCapture(source, cv.CAP_V4L2)
                capture.set(cv.CAP_PROP_FOURCC, cv.VideoWriter_fourcc(*"MJPG"))
                capture.set(cv.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv.CAP_PROP_FRAME_HEIGHT, 480)
                capture.set(cv.CAP_PROP_FPS, 5)
                capture.set(cv.CAP_PROP_BUFFERSIZE, 1)
                if not capture.isOpened():
                    raise RuntimeError("Camera unavailable")
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError("Camera read failed")
                    self.orchestrator.observe(detector.detect(frame))
                    self.orchestrator.tick(self.listener(), speech_busy=self.speech_busy())
                    self.stop_event.wait(max(0, .25-(time.monotonic()-started)))
            except Exception as exc:
                self.orchestrator.observe([], online=False, source=type(exc).__name__)
                self.orchestrator.tick(self.listener(), speech_busy=self.speech_busy())
                self.stop_event.wait(2)
            finally:
                if capture:
                    capture.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Image or video file; no upload")
    parser.add_argument("--output", required=True, help="Detection/event JSONL, no images")
    args = parser.parse_args()
    from .simulator import RobotSimulator
    detector = Detector()
    cv = detector.cv
    cap = cv.VideoCapture(args.input)
    if not cap.isOpened():
        raise SystemExit("Cannot open input")
    fps = cap.get(cv.CAP_PROP_FPS) or 4
    if not math.isfinite(fps) or fps > 240:
        fps = 4
    step = max(1, round(fps/4))
    robot = RobotSimulator()
    flow = Orchestrator(robot, clock=lambda: 0.)
    i = 0
    try:
        with Path(args.output).open("w") as output:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if i % step == 0:
                    now = i/fps
                    flow.observe(detector.detect(frame), now=now, source="file")
                    state = flow.tick(now=now)
                    output.write(json.dumps({"seconds": now, **state, "simulation": robot.snapshot()}, ensure_ascii=False)+"\n")
                i += 1
    finally:
        cap.release()


if __name__ == "__main__":
    main()
