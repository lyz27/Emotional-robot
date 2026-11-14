"""Manual real-detector/file acceptance using OpenCV sample images (not user images).

Run: python tests/vision_smoke.py FACE.jpg PEOPLE.png OUTPUT_DIR
Images can be obtained from opencv/opencv samples/data/lena.jpg and basketball1.png.
"""
import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np

from enmotion_robot.vision import Detector

if __name__ == "__main__":
    face_path, people_path, output_path = map(Path, sys.argv[1:])
    output_path.mkdir(parents=True, exist_ok=True)
    detector = Detector()
    assert any(d["kind"] == "face" for d in detector.detect(cv2.imread(str(face_path))))
    assert any(d["kind"] == "person" for d in detector.detect(cv2.imread(str(people_path))))
    assert not detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
    face = cv2.imread(str(face_path))
    video = output_path / "fixture.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 4, (640, 360))
    assert writer.isOpened()
    for i in range(176):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        if 8 <= i < 40:
            size = 180 if i < 28 else 300
            x = min(330, 20+max(0, i-12)*12)
            y = 20
            frame[y:y+size, x:x+size] = cv2.resize(face, (size, size))
        writer.write(frame)
    writer.release()
    output = output_path / "result.jsonl"
    subprocess.run([sys.executable, "-m", "enmotion_robot.vision", "--input", str(video), "--output", str(output)], check=True)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    events = {e["event"] for row in rows for e in row["events"]}
    assert {"arrived", "approaching", "left", "empty"} <= events, events
    yaws = [row["simulation"]["neck"]["yaw"] for row in rows]
    assert min(yaws) < 0 < max(yaws)
    assert all(abs(y) <= 25 for y in yaws)
    assert max(abs(b-a) for a,b in zip(yaws, yaws[1:])) < 20
    print(json.dumps({"frames": len(rows), "events": sorted(events), "yaw_range": [min(yaws), max(yaws)],
                      "face_detection": True, "person_detection": True}))
