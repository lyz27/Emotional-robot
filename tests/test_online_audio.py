from enmotion_robot.online_audio import EndpointPolicy


def test_endpoint_keeps_pauses_for_unfinished_thoughts():
    p=EndpointPolicy()
    assert not p.ready('我想',1.5,1)
    assert not p.ready('今天比较忙因为',1.8,1)
    assert not p.ready('让我想想',3,1)
    assert p.ready('让我想想',4,1)
    assert not p.ready('明天有空吗',.8,.1)
    assert p.ready('明天有空吗',.8,.3)
    assert not p.ready('帮我安排一下今天的任务',1,1)
    assert p.ready('帮我安排一下今天的任务',1.4,1)
    assert not p.ready('',6,6)
