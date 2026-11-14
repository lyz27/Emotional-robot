from enmotion_robot.listener import wake_content, rms, Segmenter
import array


def test_wake_aliases_and_preserve_request():
    assert wake_content("嘿，小蓝！") == ""
    assert wake_content("嘿，小兰，今天怎么样？") == "今天怎么样"
    assert wake_content("小蓝") == ""
    assert wake_content("我今天看到小蓝") is None
    assert wake_content("今天天气不错") is None
    assert rms([0, 0]) == 0


def test_endpoint_keeps_long_speech_and_sentence_pauses():
    segment = Segmenter()
    speech = array.array('h', [1000]*320).tobytes()
    silence = bytes(640)
    for _ in range(700):  # 14 seconds: old 12-second hard cut must not return.
        assert segment.accept(speech) is None
    for _ in range(60):  # 1.2s natural pause must not finish the sentence.
        assert segment.accept(silence) is None
    assert segment.frames  # captures stay latched even when a session timer expires
    for _ in range(100):
        assert segment.accept(speech) is None
    result = None
    for _ in range(91):
        result = segment.accept(silence) or result
    assert result and len(result) > 32000*14


def test_endpoint_preroll_and_noise_rejection():
    segment = Segmenter()
    for _ in range(100):
        assert segment.accept(bytes(640)) is None
    assert not segment.frames
    speech = array.array('h', [500]*320).tobytes()
    for _ in range(20):
        segment.accept(speech)
    assert segment.frames[:10] == [bytes(640)]*10
