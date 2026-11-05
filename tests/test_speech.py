import io
import math
import wave
from pathlib import Path

import pytest

from enmotion_robot.speech import (
    SpeechCoordinator,
    SpeechError,
    WavBytesSource,
    local_reply,
)


def wav_bytes(*, seconds: float = 0.5, rate: int = 16_000, amplitude: float = 0.2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for index in range(round(seconds * rate)):
            sample = round(math.sin(index * 2 * math.pi * 440 / rate) * amplitude * 32767)
            frames.extend(sample.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(frames))
    return buffer.getvalue()


class FakeEngine:
    def detect_speech(self, audio):
        return audio.samples, "test-vad"

    def recognize(self, samples, sample_rate):
        return "你好，机器人。"

    def synthesize(self, text: str, output: Path) -> float:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(wav_bytes(seconds=0.3))
        return 0.3


def test_wav_source_decodes_and_resamples() -> None:
    audio = WavBytesSource(wav_bytes(rate=8_000)).read()
    assert audio.sample_rate == 16_000
    assert audio.duration == pytest.approx(0.5, abs=0.01)
    assert len(audio.samples) == 8_000


def test_invalid_audio_is_rejected() -> None:
    with pytest.raises(SpeechError) as error:
        WavBytesSource(b"not a wave").read()
    assert error.value.code == "invalid_audio"


def test_file_pipeline_returns_transcript_reply_audio_and_timings(tmp_path: Path) -> None:
    coordinator = SpeechCoordinator(
        model_dir=tmp_path / "models",
        output_dir=tmp_path / "audio",
        engine=FakeEngine(),
    )
    result = coordinator.process(WavBytesSource(wav_bytes(), "hello.wav"))
    assert result["transcript"] == "你好，机器人。"
    assert "很高兴听到你的声音" in result["reply"]
    assert result["vad"] == "test-vad"
    assert result["offline"] is True
    assert result["timings"]["total_ms"] >= 0
    assert (tmp_path / "audio" / Path(result["audio_url"]).name).is_file()


def test_text_to_speech_and_interrupt(tmp_path: Path) -> None:
    coordinator = SpeechCoordinator(
        model_dir=tmp_path / "models",
        output_dir=tmp_path / "audio",
        engine=FakeEngine(),
    )
    result = coordinator.synthesize_text("语音合成测试")
    assert result["output_seconds"] == 0.3
    assert coordinator.interrupt()["generation"] == 1


def test_local_reply_has_emotional_fallbacks() -> None:
    assert "陪着你" in local_reply("我今天有点难过")
    assert "Enmotion" in local_reply("你叫什么名字")
