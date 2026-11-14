from enmotion_robot.listener import wake_content, rms


def test_wake_aliases_and_preserve_request():
    assert wake_content("嘿，小蓝！") == ""
    assert wake_content("嘿，小兰，今天怎么样？") == "今天怎么样"
    assert wake_content("小蓝") == ""
    assert wake_content("我今天看到小蓝") is None
    assert wake_content("今天天气不错") is None
    assert rms([0, 0]) == 0
