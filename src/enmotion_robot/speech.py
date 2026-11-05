"""Offline speech pipeline for file-first development on Jetson."""

from __future__ import annotations

import os
import threading
import time
import uuid
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


class SpeechError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SpeechTimeout(SpeechError):
    def __init__(self) -> None:
        super().__init__("timeout", "语音处理超时，请缩短录音后重试")


class SpeechInterrupted(SpeechError):
    def __init__(self) -> None:
        super().__init__("interrupted", "语音处理已被用户打断")


@dataclass(frozen=True)
class AudioData:
    samples: list[float]
    sample_rate: int
    duration: float


class AudioSource(Protocol):
    name: str

    def read(self) -> AudioData: ...


class WavBytesSource:
    """Read uploaded 16-bit PCM WAV data through the common source interface."""

    def __init__(self, data: bytes, name: str = "upload.wav", target_rate: int = 16_000):
        self.data = data
        self.name = name
        self.target_rate = target_rate

    def read(self) -> AudioData:
        import io

        try:
            with wave.open(io.BytesIO(self.data), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                source_rate = wav.getframerate()
                frames = wav.getnframes()
                if sample_width != 2 or channels not in (1, 2) or source_rate <= 0:
                    raise SpeechError("unsupported_audio", "仅支持单声道或双声道、16位 PCM WAV 文件")
                raw = wav.readframes(frames)
        except (wave.Error, EOFError) as exc:
            raise SpeechError("invalid_audio", "无法读取音频，请上传标准 PCM WAV 文件") from exc

        pcm = array("h")
        pcm.frombytes(raw)
        if channels == 2:
            mono = [(pcm[index] + pcm[index + 1]) / 2 for index in range(0, len(pcm), 2)]
        else:
            mono = list(pcm)
        samples = [value / 32768.0 for value in mono]
        if source_rate != self.target_rate:
            samples = _linear_resample(samples, source_rate, self.target_rate)
        duration = len(samples) / self.target_rate
        if duration < 0.25:
            raise SpeechError("audio_too_short", "录音短于 0.25 秒")
        if duration > 60:
            raise SpeechError("audio_too_long", "当前调试模式只接受 60 秒以内的录音")
        return AudioData(samples=samples, sample_rate=self.target_rate, duration=round(duration, 3))


def _linear_resample(samples: list[float], source_rate: int, target_rate: int) -> list[float]:
    if not samples or source_rate == target_rate:
        return samples
    output_length = max(1, round(len(samples) * target_rate / source_rate))
    ratio = source_rate / target_rate
    output: list[float] = []
    for index in range(output_length):
        position = index * ratio
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(samples[left] * (1 - fraction) + samples[right] * fraction)
    return output


@dataclass(frozen=True)
class SpeechModelPaths:
    root: Path

    @property
    def asr_dir(self) -> Path:
        return self.root / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"

    @property
    def asr_model(self) -> Path:
        return self.asr_dir / "model.int8.onnx"

    @property
    def asr_tokens(self) -> Path:
        return self.asr_dir / "tokens.txt"

    @property
    def vad_model(self) -> Path:
        return self.root / "silero_vad.onnx"

    @property
    def tts_dir(self) -> Path:
        return self.root / "sherpa-onnx-vits-zh-ll"

    @property
    def tts_model(self) -> Path:
        return self.tts_dir / "model.onnx"

    @property
    def tts_tokens(self) -> Path:
        return self.tts_dir / "tokens.txt"

    @property
    def tts_lexicon(self) -> Path:
        return self.tts_dir / "lexicon.txt"

    @property
    def tts_rule_fsts(self) -> str:
        files = ("phone.fst", "date.fst", "number.fst")
        return ",".join(str(self.tts_dir / name) for name in files)

    def readiness(self) -> dict[str, bool]:
        return {
            "asr": self.asr_model.is_file() and self.asr_tokens.is_file(),
            "vad": self.vad_model.is_file(),
            "tts": all(
                path.is_file()
                for path in (self.tts_model, self.tts_tokens, self.tts_lexicon)
            ),
        }


class OfflineSpeechEngine:
    """Lazy-loaded SenseVoice + Silero VAD + VITS engine."""

    def __init__(self, models: SpeechModelPaths, num_threads: int = 4):
        self.models = models
        self.num_threads = num_threads
        self._recognizer: Any = None
        self._tts: Any = None

    @staticmethod
    def _runtime() -> tuple[Any, Any]:
        try:
            import numpy as np
            import sherpa_onnx
        except ImportError as exc:
            raise SpeechError(
                "runtime_unavailable",
                "离线语音运行库未安装，请执行语音依赖安装脚本",
            ) from exc
        return sherpa_onnx, np

    def _get_recognizer(self) -> Any:
        if not self.models.readiness()["asr"]:
            raise SpeechError("asr_model_missing", "中文识别模型尚未安装")
        if self._recognizer is None:
            sherpa_onnx, _ = self._runtime()
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(self.models.asr_model),
                tokens=str(self.models.asr_tokens),
                num_threads=self.num_threads,
                use_itn=True,
                debug=False,
                language="auto",
            )
        return self._recognizer

    def detect_speech(self, audio: AudioData) -> tuple[list[float], str]:
        if not self.models.vad_model.is_file():
            speech = _energy_vad(audio.samples, audio.sample_rate)
            return speech, "energy-fallback"

        sherpa_onnx, np = self._runtime()
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(self.models.vad_model)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.25
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = 20
        config.sample_rate = audio.sample_rate
        vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=70)
        samples = np.asarray(audio.samples, dtype=np.float32)
        window = config.silero_vad.window_size
        offset = 0
        while offset + window <= len(samples):
            vad.accept_waveform(samples[offset : offset + window])
            offset += window
        vad.accept_waveform(np.zeros(audio.sample_rate, dtype=np.float32))
        segments: list[float] = []
        gap = [0.0] * int(audio.sample_rate * 0.1)
        while not vad.empty():
            if segments:
                segments.extend(gap)
            segments.extend(vad.front.samples)
            vad.pop()
        return segments, "silero"

    def recognize(self, samples: list[float], sample_rate: int) -> str:
        recognizer = self._get_recognizer()
        _, np = self._runtime()
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, np.asarray(samples, dtype=np.float32))
        recognizer.decode_stream(stream)
        return stream.result.text.strip()

    def _get_tts(self) -> Any:
        if not self.models.readiness()["tts"]:
            raise SpeechError("tts_model_missing", "中文语音合成模型尚未安装")
        if self._tts is None:
            sherpa_onnx, _ = self._runtime()
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(self.models.tts_model),
                        lexicon=str(self.models.tts_lexicon),
                        tokens=str(self.models.tts_tokens),
                    ),
                    provider="cpu",
                    debug=False,
                    num_threads=self.num_threads,
                ),
                rule_fsts=self.models.tts_rule_fsts,
                max_num_sentences=1,
            )
            if not config.validate():
                raise SpeechError("tts_config_invalid", "中文语音合成模型配置无效")
            self._tts = sherpa_onnx.OfflineTts(config)
        return self._tts

    def synthesize(self, text: str, output: Path) -> float:
        sherpa_onnx, _ = self._runtime()
        tts = self._get_tts()
        generation = sherpa_onnx.GenerationConfig()
        generation.sid = 2
        generation.speed = 1.05
        generation.silence_scale = 0.2
        audio = tts.generate(text, generation)
        if len(audio.samples) == 0:
            raise SpeechError("tts_failed", "没有生成有效语音")
        output.parent.mkdir(parents=True, exist_ok=True)
        sherpa_onnx.write_wave(str(output), audio.samples, audio.sample_rate)
        return round(len(audio.samples) / audio.sample_rate, 3)


def _energy_vad(samples: list[float], sample_rate: int) -> list[float]:
    """Deterministic fallback used until the Silero model is installed."""
    frame_size = max(1, int(sample_rate * 0.03))
    energies = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        energies.append(sum(value * value for value in frame) / max(1, len(frame)))
    peak = max(energies, default=0.0)
    threshold = max(1e-5, peak * 0.08)
    active = [index for index, energy in enumerate(energies) if energy >= threshold]
    if not active or peak < 1e-5:
        return []
    padding = 5
    first = max(0, active[0] - padding) * frame_size
    last = min(len(samples), (active[-1] + padding + 1) * frame_size)
    return samples[first:last]


def local_reply(transcript: str) -> str:
    """Offline response used before the Week 4 dialog model is connected."""
    normalized = transcript.strip().replace(" ", "")
    if not normalized:
        return "我没有听清，请再说一遍。"
    if any(word in normalized for word in ("你好", "嗨", "在吗")):
        return "你好呀，我在这里，很高兴听到你的声音。"
    if "名字" in normalized:
        return "我叫 Enmotion，是正在成长中的情绪陪伴机器人。"
    if any(word in normalized for word in ("谢谢", "感谢")):
        return "不用客气，我很愿意陪着你。"
    if any(word in normalized for word in ("难过", "不开心", "伤心")):
        return "听起来你现在不太好受，我会安静地陪着你。"
    return f"我听到了，你说的是，{transcript}"


class SpeechCoordinator:
    def __init__(
        self,
        *,
        model_dir: Path | None = None,
        output_dir: Path | None = None,
        engine: OfflineSpeechEngine | None = None,
        timeout: float = 60.0,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        models = SpeechModelPaths(model_dir or Path(os.getenv("ENMOTION_MODEL_DIR", project_root / "models")))
        self.output_dir = output_dir or Path(os.getenv("ENMOTION_AUDIO_DIR", project_root / "runtime" / "audio"))
        self.engine = engine or OfflineSpeechEngine(models, num_threads=max(1, min(4, os.cpu_count() or 2)))
        self.models = models
        self.timeout = timeout
        self._process_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._generation = 0
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "offline": True,
                "models": self.models.readiness(),
                "ready": all(self.models.readiness().values()),
                "processing": self._process_lock.locked(),
                "generation": self._generation,
                "last_result": self._last_result,
                "last_error": self._last_error,
            }

    def interrupt(self) -> dict[str, Any]:
        with self._state_lock:
            self._generation += 1
            generation = self._generation
        return {"ok": True, "generation": generation, "message": "已请求停止播放和后续处理"}

    def _check(self, generation: int, started: float) -> None:
        with self._state_lock:
            current_generation = self._generation
        if generation != current_generation:
            raise SpeechInterrupted()
        if time.monotonic() - started > self.timeout:
            raise SpeechTimeout()

    def process(
        self,
        source: AudioSource,
        *,
        reply_override: str | None = None,
        reply_builder: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        with self._process_lock:
            with self._state_lock:
                generation = self._generation
            started = time.monotonic()
            timings: dict[str, int] = {}
            try:
                mark = time.monotonic()
                audio = source.read()
                timings["decode_ms"] = round((time.monotonic() - mark) * 1000)
                self._check(generation, started)

                mark = time.monotonic()
                speech, vad_mode = self.engine.detect_speech(audio)
                timings["vad_ms"] = round((time.monotonic() - mark) * 1000)
                if len(speech) < audio.sample_rate * 0.2:
                    raise SpeechError("no_speech", "录音中没有检测到清晰语音")
                self._check(generation, started)

                mark = time.monotonic()
                transcript = self.engine.recognize(speech, audio.sample_rate)
                timings["asr_ms"] = round((time.monotonic() - mark) * 1000)
                if not transcript:
                    raise SpeechError("recognition_empty", "语音识别没有得到文字")
                self._check(generation, started)

                mark = time.monotonic()
                reply = (reply_override or "").strip()
                if not reply:
                    reply = reply_builder(transcript) if reply_builder else local_reply(transcript)
                reply = reply.strip()
                if not reply:
                    raise SpeechError("reply_empty", "对话系统没有生成回复")
                timings["reply_ms"] = round((time.monotonic() - mark) * 1000)

                mark = time.monotonic()
                filename = f"reply-{uuid.uuid4().hex}.wav"
                output = self.output_dir / filename
                output_seconds = self.engine.synthesize(reply, output)
                timings["tts_ms"] = round((time.monotonic() - mark) * 1000)
                self._check(generation, started)

                timings["total_ms"] = round((time.monotonic() - started) * 1000)
                result = {
                    "ok": True,
                    "source": source.name,
                    "transcript": transcript,
                    "reply": reply,
                    "audio_url": f"/audio/{filename}",
                    "input_seconds": audio.duration,
                    "speech_seconds": round(len(speech) / audio.sample_rate, 3),
                    "output_seconds": output_seconds,
                    "vad": vad_mode,
                    "offline": True,
                    "timings": timings,
                }
                with self._state_lock:
                    self._last_result = result
                    self._last_error = None
                return result
            except SpeechError as exc:
                with self._state_lock:
                    self._last_error = str(exc)
                raise

    def synthesize_text(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise SpeechError("empty_text", "请输入需要合成的文字")
        if len(cleaned) > 300:
            raise SpeechError("text_too_long", "调试模式最多合成 300 个字符")
        started = time.monotonic()
        filename = f"tts-{uuid.uuid4().hex}.wav"
        duration = self.engine.synthesize(cleaned, self.output_dir / filename)
        return {
            "ok": True,
            "text": cleaned,
            "audio_url": f"/audio/{filename}",
            "output_seconds": duration,
            "tts_ms": round((time.monotonic() - started) * 1000),
            "offline": True,
        }
