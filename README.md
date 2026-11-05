# Enmotion Robot

Software-first emotional companion robot built around a Jetson Orin Nano and an
ESP32-S3. The current milestone establishes a safe, versioned serial link before
any physical actuator is connected.

## ESP32 protocol milestone

The firmware uses newline-delimited JSON at 115200 baud. It starts in a safe,
disarmed state and returns to that state if Jetson heartbeats stop for two
seconds. No GPIO or actuator outputs are enabled in this milestone.

Supported requests:

- `hello`
- `heartbeat`
- `get_status`
- `ping`
- `arm`
- `disarm`
- `estop`
- `clear_faults`

## Jetson diagnostic

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/enmotion-device --port auto --duration 5
```

## Locked development environment

Install [uv](https://docs.astral.sh/uv/) and reproduce the exact dependency set:

```bash
uv sync --frozen --extra dev
```

The committed `uv.lock` is the source of truth for Python dependency versions.

## Runtime and browser dashboard

Run the auto-discovery, heartbeat, reconnect service and debug dashboard:

```bash
.venv/bin/enmotion-runtime --host 0.0.0.0 --port 8080
```

Open `http://<jetson-ip>:8080`. The Week 2 studio includes animated eyes,
automatic blinking, six emotion states, gaze and virtual neck controls, touch,
distance, face-position and motor simulation, a combined event/status panel,
and the existing ESP32 safety controls. It intentionally does not expose an
arm command.

Both simulated behavior and physical ESP32 controls use the same endpoint:

```text
POST /api/control/{command}
{"target":"simulator", ...command fields}
{"target":"esp32"}
```

The complete combined state is available from `GET /api/robot`. Simulator
commands are `set_emotion`, `set_gaze`, `set_neck`, `set_sensors`, `blink`, and
`reset`. Physical commands remain restricted by the safety allowlist.

The production service definition is in `deploy/enmotion-runtime.service`.

## Offline speech pipeline (Week 3)

Install the ARM64-compatible offline speech runtime and download the Chinese
SenseVoice ASR, Silero VAD, and VITS TTS models:

```bash
./scripts/setup_speech.sh
```

Restart `enmotion-runtime.service`, then use the "第三周 · 离线语音链路"
panel to upload a 16-bit PCM WAV file. The file and future microphone source use
the same audio-source interface. Processing is entirely offline and returns the
transcript, a local fallback reply, synthesized reply audio, VAD mode, and
per-stage latency. Playback can be interrupted from the browser.

Speech endpoints:

- `GET /api/speech/status`
- `POST /api/speech/process` (multipart WAV upload)
- `POST /api/speech/tts`
- `POST /api/speech/interrupt`
- `GET /audio/{generated-file}.wav`

## Companion core (Week 4)

The Week 4 core adds a bounded conversation context, automatic summaries,
controllable SQLite memory, an emotion state machine with natural decay, expression
and pose mapping, privacy controls, and companion-safety boundaries. It runs with
the offline local dialogue fallback by default, so no new hardware or API key is
required.

The dialogue provider is hybrid-ready. To use an OpenAI-compatible chat endpoint,
set `ENMOTION_DIALOGUE_URL` to the full chat-completions URL and optionally set
`ENMOTION_DIALOGUE_MODEL`, `ENMOTION_DIALOGUE_API_KEY`, and
`ENMOTION_DIALOGUE_TIMEOUT`. Provider failure automatically falls back to the local
dialogue engine. Messages marked private or containing credentials are never sent
to the remote provider.

Companion endpoints:

- `GET /api/companion/status`
- `POST /api/companion/chat`
- `GET /api/companion/memories`
- `PUT /api/companion/memories/{id}`
- `DELETE /api/companion/memories/{id}`
- `DELETE /api/companion/memories`
- `DELETE /api/companion/sessions/{session_id}`

The generated database defaults to `runtime/companion.db` and is excluded from
version control. The dashboard can view, edit, delete, or clear all saved memories.

## Firmware

```bash
cd firmware/esp32_s3
pio run
pio run --target upload --upload-port /dev/ttyUSB0
```
