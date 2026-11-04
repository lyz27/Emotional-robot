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

## Firmware

```bash
cd firmware/esp32_s3
pio run
pio run --target upload --upload-port /dev/ttyUSB0
```
