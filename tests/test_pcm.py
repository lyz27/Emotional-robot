import base64
import threading
import pytest
from enmotion_robot.pcm_pipeline import run


def test_audio_starts_before_synthesis_done_and_order_preserved():
    played=threading.Event();writes=[]
    def events(path,payload):
        if 'companion' in path:
            yield {'type':'sentence','text':'第一句。'}
            yield {'type':'sentence','text':'第二句。'}
            yield {'type':'done','emotion':'happy'}
        else:
            yield {'type':'audio','sample_rate':16000,'channels':1,'format':'s16le'}
            yield {'type':'pcm','data':base64.b64encode(b'\x01\x00'*320).decode()}
            assert played.wait(2), 'playback waited for entire synthesis'
            yield {'type':'done'}
    class Output:
        def __init__(self,publish,first):self.first=first
        def start(self,rate):assert rate==16000
        def write(self,pcm,text,emotion):
            writes.append(text);played.set();self.first()
        def idle(self):pass
        def finish(self):pass
        def close(self):pass
    m=run('你好',lambda *a:None,events,Output)
    assert writes==['第一句。','第二句。'] and m['pcm_packets']==2


def test_pcm_error_closes_player():
    closed=[]
    def events(path,payload):
        yield {'type':'sentence','text':'测试'} if 'companion' in path else {'type':'error','message':'失败'}
    class Output:
        def __init__(self,*a):pass
        def close(self):closed.append(True)
    with pytest.raises(RuntimeError,match='失败'):run('test',lambda *a:None,events,Output)
    assert closed


def test_pcm_api_format_samples_and_errors():
    from fastapi.testclient import TestClient
    from enmotion_robot.web import create_app
    import numpy as np
    class Engine:
        def _get_tts(self):return type('TTS',(),{'sample_rate':16000})()
        def synthesize_chunks(self,text,callback):
            if text=='故障':raise RuntimeError('private details')
            callback(np.array([-.5,.5]*640,dtype=np.float32),.5)
    class Voice:
        _process_lock=threading.Lock()
        engine=Engine()
    client=TestClient(create_app(speech=Voice(),manage_supervisor=False))
    import json
    response=client.post('/api/speech/pcm',json={'text':'测试'})
    events=[json.loads(x) for x in response.text.splitlines()]
    assert events[0]=={'type':'audio','sample_rate':16000,'format':'s16le','channels':1}
    packets=[base64.b64decode(x['data']) for x in events if x['type']=='pcm']
    assert len(packets)==4 and all(len(p)==640 for p in packets)
    assert events[-1]['type']=='done'
    assert 'private details' not in client.post('/api/speech/pcm',json={'text':'故障'}).text
    assert client.post('/api/speech/pcm',json={'text':''}).status_code==422
