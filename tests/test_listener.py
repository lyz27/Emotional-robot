from enmotion_robot.listener import wake_content, rms, Segmenter, is_goodbye
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


def test_goodbye_is_local_intent_not_a_substring():
    for text in ('再见','再见，小蓝！','拜拜','好了，先这样吧','不聊了','停止对话','嗯，好，再见了，谢谢','bye bye','退出'):
        assert is_goodbye(text), text
    for text in ('不要再见','我不想说再见','再见这首歌是谁唱的','解释停止对话是什么意思','你说再见时没有退出','我先这样安排明天的工作'):
        assert not is_goodbye(text), text
