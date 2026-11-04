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

Open `http://<jetson-ip>:8080`. The page exposes status, ping, disarm,
emergency-stop, and fault-clear controls. It intentionally does not expose an
arm command.

The production service definition is in `deploy/enmotion-runtime.service`.

## Firmware

```bash
cd firmware/esp32_s3
pio run
pio run --target upload --upload-port /dev/ttyUSB0
```
