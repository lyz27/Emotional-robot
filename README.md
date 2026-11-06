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

The production Jetson setup uses `deploy/enmotion-llm.service` to run the official
Qwen3-4B Q4_K_M GGUF with CUDA-enabled llama.cpp on localhost port 8081. The model
returns a natural reply plus a constrained emotion/intensity suggestion; the robot
runtime validates the suggestion before it can affect the existing safe expression
and motion mapping. `deploy/enmotion-runtime.service` connects to this local
OpenAI-compatible endpoint and keeps the offline rules as its failure fallback.

Jetson deployment uses a 2048-token context and `--load-mode none` to avoid the
initial allocation failure seen with mmap and a 4096-token context. All model
layers are offloaded to CUDA. The tested llama.cpp source revision is `e30ad80`.
The official GGUF SHA-256 is
`7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5`.

## Work space

The root page is the work space: a persistent assistant sidebar, work conversation,
tasks, notes, review and reminders. The original companion and hardware debug
dashboard remains at `/debug`. Work data lives in `runtime/work.db`, separately
from companion memory. Suggestions can be confirmed via the editor; with the
Agent installed, explicit requests to save, create, update or complete records
execute directly and return tool receipts in the conversation.

Reminders currently appear in the open browser and overdue reminders are shown
on reopening; there is no phone push or background browser notification service.
UI checks: `node tests/work_ui.cjs` and `node tests/dashboard_presentation.cjs`.

Install the work Agent with `uv sync --extra agent --extra speech --extra dev`
(or install the `agent` project extra in the existing Jetson environment).
Qwen-Agent 0.0.34 uses the existing local `ENMOTION_DIALOGUE_URL`; no additional
model or cloud API is needed. Its only tool is `work_records`: list/search/read,
create/update tasks and notes. It cannot delete records or run shell commands.
Each request is bounded to three model calls and six tool operations. Execution
receipts are persisted in `agent_runs`; a replayed `request_id` returns the saved
result without creating another record. Reminder delivery remains browser-only.
Live-model acceptance test: `python tests/agent_smoke.py` on the Jetson, using a
temporary database that is discarded after the test.

## Mac inbox sync

The selected Mac folder is `/Users/lixizi/Documents/enmotion_robot`. A per-user
LaunchAgent (`com.enmotion.folder-sync`) checks it every 30 seconds while the Mac
is awake and logged in. UTF-8 `.txt`, `.md`, and `.markdown` files are imported
into Jetson work notes; each file is limited to 100KB / 20,000 characters. Hidden
files and symbolic links are ignored. Source-file deletions do not delete notes.

Imports use body hashes, persistent revisions (`sync_versions`) and atomic SQLite
transactions. If a note was edited in the browser, the next source update creates
a new sync copy and preserves the browser edit. WorkSpace includes sync status.
The Agent can search imported notes through its existing note tools.

Mac configuration and cache are stored under `~/.config/enmotion-robot-sync/`;
logs are in its `logs/` folder. The authentication token is kept in the private
Mac config and Jetson `runtime/sync-token`, never in Git. Update the server address
in the Mac config if the Jetson IP changes.

Manual check:

```bash
.venv/bin/python -m enmotion_robot.folder_sync run --config /Users/lixizi/.config/enmotion-robot-sync/config.json
```

Reinstall the login task with the same command using `install` instead of `run`.
This is a one-way file import; the work page is the shared interface for editing
Jetson records. Imported files are not automatically turned into tasks.

## Mac quick capture

Run `bash scripts/install_quick_capture.sh` on the Mac to build and install the
native menu bar app at `~/Applications/Enmotion 随手记.app`, a desktop shortcut,
and login task `com.enmotion.quick-capture`. Requires Xcode command line tools.
It reuses `~/.config/enmotion-robot-sync/config.json`; no new connection setup is
needed on this Mac. The existing folder sync continues independently.

Press **Control + Option + Space**, type work content or click **粘贴文字**, then
press **Command + Return**. Escape hides the window. For example:
“明天下午三点提醒我跟进客户报价” or “记一下：客户希望下周看到第一版方案”.
The authenticated `/api/sync/capture` route delegates to the Jetson work agent.
Responses show the agent's saved results or clarification questions. Only an
explicit paste reads the clipboard. Draft text is saved locally in app preferences;
failed requests retain their request ID for safe retries through `agent_runs`.
The global shortcut uses Carbon hotkey registration, without accessibility or
screen recording permission. If occupied, the menu bar entry remains available.
Reminder delivery is still through the open work page, not native notifications.

## Firmware

```bash
cd firmware/esp32_s3
pio run
pio run --target upload --upload-port /dev/ttyUSB0
```
