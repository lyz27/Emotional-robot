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

Due tasks also enter a durable robot voice queue. With the robot powered on and
its listener running, delivery waits until the voice session is idle. The work
page can be closed and face detection is not required. There is no phone push.
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

The native window now includes a right sidebar for pending tasks and the ten
most recent notes. It refreshes on opening, after submissions and every 30 seconds.
The menu bar shows the pending count (or offline state); its tooltip lists up to
three tasks. Task rows show Beijing reminder times and overdue status. Checking
a task completes the same record used by the browser; note titles open their
contents in the response area. Lists retained during network failures are marked
as potentially stale. Authenticated routes: `GET /api/sync/work` and
`PATCH /api/sync/tasks/{id}` (strict boolean `done` only).

Four input modes share the existing Jetson model: capture, save notes, break down
a goal, and review work. Breakdown and review tools enforce read-only access;
suggestions never automatically create or change records in those modes. Change
back to capture to request actual updates. Connection failures retain the draft.
Note capture requires a successful save tool receipt before reporting success.
Definitive failures without writes retain the draft and allow a fresh attempt;
unknown network outcomes and partial writes retain the original replay ID.
Native diagnostic commands on the installed executable: `--check-layout`
(view bounds and sidebar rows) and `--check-work` (authenticated list retrieval).
Live-model acceptance: `python tests/capture_smoke.py` on the Jetson; it uses a
disposable database and never modifies the user's tasks or notes.

## Robot screen

### Vision and multimodal orchestration (Week 5)

Install the locked `vision` extra (`uv sync --extra vision --frozen`) alongside
the existing speech/agent extras as required. OpenCV 4.10 headless runs one CPU
thread: Haar frontal faces and HOG full-body detection, at up to four processing
cycles per second, 640px width. This is lightweight presence/position detection,
not image-language understanding, identity, demographic or emotion inference.
Profile faces, occlusion, poor lighting and seated bodies may be missed; HOG may
also report false positives. No camera frames are saved or uploaded.

The main runtime starts perception automatically; `ENMOTION_VISION_ENABLED=0`
disables camera access and `ENMOTION_CAMERA` selects a V4L2 index or stable path.
Camera failure reports offline, releases the device and retries; it does not
invent an exit event. Status and detection positions: `/vision`, `/api/vision`.
Touch tests: `POST /api/vision/touch` or touch the face. No hardware purchase or
motor arming is needed. `voice > touch > vision > idle` arbitrates gaze/virtual
neck/emotion; actual ESP32 motor commands are never emitted by this module.

Entry requires 0.5s of observations; absence tolerates 1.5s, long empty is 30s.
Approach means 1.5x target image area of the same detector class, not metric
distance. Events have a 5s per-type cooldown. Nearest previous target is retained
to reduce switching; this is not identity tracking. Gaze uses smoothing and a
deadband; virtual neck stays within yaw ±25°, pitch ±12°. Visual emotion returns
to calm after its transient event. Speech/TTS/listener activity takes precedence.
The dialogue receives only a fresh (<3s) local detection summary, not images.

Offline file validation uses the same detector and orchestrator:

```bash
python -m enmotion_robot.vision --input /path/to/test.mp4 --output runtime/vision.jsonl
python tests/vision_smoke.py runtime/vision-face.jpg runtime/vision-people.png runtime/vision-smoke
```

The smoke script uses public OpenCV sample images downloaded separately from
`opencv/opencv`'s `samples/data/lena.jpg` and `basketball1.png`; sample media is not
committed. It generates a 44-second, 176-frame MJPEG fixture, validates both real
detectors, all four events, bidirectional neck tracking and motion bounds.

### Local wake conversation

#### Low-latency replies and model selection

The deployed router uses Qwen3 1.7B Q4_K_M for companion dialogue and Qwen3 4B
Q4_K_M for work tools. The small model failed the real work-tool acceptance test,
so it is not used to create or modify work records. `ENMOTION_WORK_MODEL` selects
the work model independently. `deploy/local-models.ini` plus
`deploy/enmotion-llm-router.service` keep at most one model resident. Main API model
requests share a lock so model switches cannot overlap; TTS remains concurrent.
The 4B preset reduces batch size and offloads 32 layers for GPU memory headroom.
Switching from chat to work requires loading 4B; cold requests can take tens of
seconds. After work, 1.7B also needs a one-time reload. These are not warm timings.

`POST /api/companion/stream` emits NDJSON sentence/done/error events, extracting
only the reply field from the model's streaming JSON. It preserves the companion
safety path, fresh visual context and final emotion. Voice replies use one reader,
one ordered TTS worker and playback. The first sentence can play while later
sentences generate/synthesize. Captions and mouth RMS follow the sentence currently
playing; the full answer is not displayed early. Failed partial replies do not
restart the model and repeat the first sentence. Playback remains half-duplex.

On-device three-prompt warm comparison (same prompts, generated answers differ):

| Model with sentence pipeline | First sentence audio ready, seconds | Mean |
| --- | --- | --- |
| 4B Q4_K_M | 4.073 / 6.309 / 3.858 | 4.747 |
| 1.7B Q4_K_M | 3.764 / 3.076 / 2.971 | 3.270 |
| Final 1.7B router + compact voice prompt | 2.575 / 2.998 / 2.818 | 2.797 |

This measures text-request arrival to audio-file readiness, excluding microphone
endpointing, ASR and physical speaker latency. Earlier buffered short replies took
8.04–8.38 seconds for LLM plus TTS. Actual 4B pipeline playback test began at 4.566s.
Only three prompts were measured, not a general latency/accuracy guarantee.
The final compact voice prompt reduces prompt length and explicitly disallows
physical actions it cannot perform; the three final samples followed that limit.
Reproduce with `python tests/latency_bench.py --label NAME --output runtime/result.json`.
Recorded samples are in `docs/latency-2026-11-14.json`.

The 1.7B quantization comes from `unsloth/Qwen3-1.7B-GGUF`, SHA256
`b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897`.
`scripts/download_small_model.py` downloads/verifies it; put the checked GGUF into
`models/llm/`. Pause listening with `systemctl --user stop enmotion-listener`, then
run `sudo bash scripts/select_local_model.sh router` and resume the user service.
The same script accepts `4b` for rollback to the original single-model setup and
`1.7b` for standalone benchmarking (not recommended for work tools).

`enmotion-listener.service` is a desktop user service that captures the UGREEN
microphone with PulseAudio. Enable it with `systemctl --user enable --now
enmotion-listener` after copying `deploy/enmotion-listener.service` into
`~/.config/systemd/user/`. Stop listening with `systemctl --user stop
enmotion-listener`. It follows the desktop login and does not require root.

Install the checked streaming keyword model with `bash scripts/install_wake.sh`.
Say “嘿，小蓝” (or “小蓝”); a short earcon acknowledges it while capture stays open,
so there is no spoken-acknowledgement deaf interval. The dedicated local Zipformer
keyword spotter replaces full SenseVoice decoding for idle wake detection.
A 45-second idle window opens after wake/answer. Once speech starts it stays latched
until endpointing, independent of that idle window and recognition latency.
The default listener now uses the bilingual small streaming Zipformer ASR model.
Install it with `bash scripts/install_online_asr.sh` before restarting the listener.
Partial text appears while speaking. Text-aware endpointing waits 700ms for clear
questions, 1.3s for ordinary phrases, 2.2s for short/unfinished phrases and 4s after
explicit hesitation. Text must be stable for 250ms. Empty hypotheses retain a 5s
energy endpoint; utterances have a 60s safety cap. These are heuristics, so a long
pause after a complete question may still end the turn. For rollback, set
`ENMOTION_STREAMING_ASR=0` in a service override: SenseVoice then uses the original
300ms pre-roll and `ENMOTION_END_SILENCE` (default 1.8s).
“停止对话” returns to waiting for the wake phrase.
Plain “再见”, “拜拜”, “不聊了”, “先这样”, and addressed variants are now local
end-session intents as well. Matching is anchored, so asking about a song called
“再见” or saying “不要说再见” does not end the session. In listening mode, a
streaming ASR hypothesis permits an early goodbye endpoint after 600ms silence.
The final hypothesis must still match an exact goodbye before ending. In the
offline rollback path, a dedicated short-command KWS requests an early SenseVoice
check after 560ms silence. Rejected candidates retain the utterance buffer. ASR also
recognizes these intents if KWS misses them. Goodbye, presence and identity replies
are synthesized once into a persistent local clip cache at startup, removing LLM
and per-request TTS from those hot paths. This is not millisecond end-to-end speech
understanding: endpointing/ASR remain, and open chat still takes seconds.
Physical acceptance: user confirmed plain “再见” exits and returns to waiting.
Qwen/TTS still add response latency. Open conversation remains seconds, not a
guaranteed millisecond response.
Captions hide reply text during synthesis and appear at estimated audio onset;
100ms status polling and a low-latency PulseAudio stream reduce the previous gap.
This is not forced word alignment or a measured sample-exact speaker timestamp.
Speech playback pauses and
reopens capture to avoid self-triggering; speaking cannot currently be interrupted
by voice. Raw microphone audio remains in memory. Voice transcripts now retain
bounded local conversation history and allowed profile/preferences, with existing
privacy filters. Explicit tasks and notes live in `work.db`; see below.
The default response pipeline uses `/api/speech/pcm`:
VITS callback samples are transported as bounded NDJSON/base64 20ms PCM packets,
then paced through one continuous `pacat` stream across reply sentences. Playback
starts before synthesis completes. The native VITS callback still produces batches;
20ms packets do not imply the neural model generates one frame every 20ms. Captions
use the estimated 60ms output-buffer delay, not word-level forced alignment.
Set `ENMOTION_PCM_STREAM=0` to restore sentence WAV playback. Browser/file speech
and cached control clips still use WAV and the existing retention behavior.
Listener status is in runtime/listener.json and /api/listener;
the face page polls it for captions, emotions and playback RMS mouth motion.
Revised physical acceptance: two wake detections in the user's recorded test
(2.38s and 10.54s within that clip); user subsequently confirmed live wake,
complete utterances and improved audio/caption synchronization. These are not
speaker-independent accuracy or sample-exact AV latency benchmarks.

### Local workstation companionship

Open `/care` (also linked from the face page) to configure reminders. Defaults are
water every **45 minutes**, workstation break every **60 minutes**, and a welcome
after stable face-backed presence. Settings persist in `runtime/wellness-config.json`;
elapsed counters, pending jobs and recent history reset when the runtime restarts.
The vision observer updates timers once per second. Time accumulates only with
fresh camera presence backed by a recent face detection. This measures presence,
not sitting posture, identity, drinking, or medical state. After two minutes away,
timers reset; welcomes have a ten-minute cooldown and do not repeat on short gaps.

The idle listener claims a reminder only when there is no active voice session,
no server speech/dialogue operation, and at least 800ms of quiet microphone input.
It closes capture while playing a cached local clip, acknowledges successful
playback, then resumes wake detection. No Qwen request is used for reminders.
Jobs are deduplicated by kind and leased for retry; an in-process delivered-ID set
prevents replay if acknowledgement temporarily fails. A process crash between
playback and acknowledgement can still cause a retry. Camera failure pauses
counting. No camera frames or microphone recordings are saved by these rules.

Measured transport checks and limits for the streaming update are recorded in
`docs/streaming-care-2026-11-14.json`. Hardware output drained successfully using
silent PCM; physical conversation and welcome acceptance remain separate checks.

### Durable voice assistant

Voice, face chat and work chat now share the same committed work records.
`assistant.py` handles common scheduling, recall, rescheduling, completion and
explicit notes locally. Success is confirmed only after SQLite commits. Missing
times and ambiguous targets ask a follow-up; clarification state persists for
15 minutes across restarts. Dates use Asia/Shanghai. Task names and the last-task
reference replace database identifiers. Complex explicit requests use Qwen-Agent;
breakdown and review remain suggestions with read-only tools.

Try “明天十一点提醒我去剪头发”, “我明天要干什么”, “把刚才那项改到后天下午两点”,
“剪头发已经做完了”, or “记住：演示用蓝色方案”. Cancelling a reminder clears its time
and retains the task; explicitly deleting the task removes it. Writes use request
IDs and exact title/time duplicate checks. A crash between a write and receipt
storage can lose the receipt; query the database to establish actual state.

Voice history now persists in the companion database across wake sessions. Tasks,
notes, last-task references and reminder acknowledgements persist in `work.db`.
Small model prompts get bounded history and a partial, relevance-ranked record
snapshot; explicit schedule queries read SQLite directly. Save durable facts as
notes: this does not promise unlimited verbatim chat recall. Privacy and secret
filters run before tool dispatch and transcript storage.

Task delivery uses leases and acknowledges after playback. Catch-up announcements
are spaced at least 30 seconds apart. Done/deleted tasks are excluded, rescheduling
invalidates old delivery state, and a crash after playback but before acknowledgement
can cause replay. Delivery waits for the current conversation window to end.
Old completed tasks lack completion dates, so they cannot establish when work was
finished. Validation is in `tests/test_assistant.py` and `docs/assistant-2026-11-14.md`.
Runtime databases and backups remain outside Git.

The assistant's default persona is 小蓝: a calm, concise, courteous Chinese personal
assistant with restrained wit inspired by JARVIS. It retains its own identity,
avoids invented execution results and live weather/device facts, and keeps empathy
in serious conversations. The face includes restrained cyan instrument rings,
not fabricated sensor readings. TTS uses the existing local voice at speed 1.0;
this is not a recreation of the movie actor's voice. Output remains at 80%.

UGREEN CM797 hardware acceptance: USB camera takes a 1280×720 MJPEG frame;
`UGREEN Camera Audio` exposes both capture and playback, independently of HDMI.
Run `bash deploy/configure-ugreen.sh --install` as the desktop user to retain the
selected audio routes at login. Initial audible verification used PulseAudio 63%
and ALSA `Audio` / PCM 25%, unmuted. The user subsequently requested louder replies;
the persistent default is now PulseAudio 80%, with PCM unmuted and no fixed lower
hardware volume overriding it. Lower software volumes were accepted by the
driver but were inaudible in the physical test. The setup uses stable device names
rather than card numbers. Temporary microphone tests remain private to the robot's
`runtime/hardware-test` directory; these are not included in Git. Hardware setup
does not itself enable an always-listening wake word service.

`/face` is the dedicated full-screen companion display (the work page and `/debug`
remain available). It uses a resolution-capped Canvas renderer, time-based springs,
procedural eyes, pupils, brows, breathing, blink scheduling and mood accents.
Touch previews are local and never drive motors. Live simulator emotion and gaze
are polled independently of animation. Replies use the existing companion and TTS
APIs; mouth motion follows actual audio RMS rather than a fake speaking timer.
`GET /api/display` exposes the last local display's ten-second frame-rate sample;
this measures animation callbacks, not guaranteed GPU presentation timing.

On the Jetson, `sudo bash deploy/install-display.sh lyz` configures GDM automatic
login, disables idle locking/blanking for this desktop, and launches a dedicated
kiosk browser after the service becomes available. This intentionally makes the
robot desktop accessible without a login password. Original GDM config is backed
up as `/etc/gdm3/custom.conf.before-enmotion`. Remove
`~/.config/autostart/enmotion-display.desktop` and restore that backup to undo.
The tested screen is 1024×600 at 60Hz. This board's Chromium Snap cannot launch
because of snap-confine capability errors; use the official Mozilla Linux ARM64
archive extracted into `~/.local/opt/firefox/`. The launcher prefers that native
Firefox and retains its browser sandbox. Restart GDM or reboot after installation.

## Firmware build

```bash
cd firmware/esp32_s3
pio run
pio run --target upload --upload-port /dev/ttyUSB0
```
