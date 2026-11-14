import json
import threading
from fastapi.testclient import TestClient
from enmotion_robot.streaming import reply_prefix, SentenceEmitter
from enmotion_robot.companion import CompanionCore, HybridDialogueModel, CompanionError
from enmotion_robot.web import create_app
from enmotion_robot.work import WorkSpace


def test_incremental_json_escaping_and_order():
    text = '这是第一句回答。第二句有“引号”、换行\n和反斜杠\\，以及😀表情。'
    encoded = json.dumps({'reply': text, 'emotion': 'calm'}, ensure_ascii=True)
    out = []
    emitter = SentenceEmitter(out.append)
    for i in range(len(encoded)+1):
        emitter.accept(reply_prefix(encoded[:i]))
    emitter.accept(reply_prefix(encoded), final=True)
    assert ''.join(out) == text
    assert len(out) >= 2
    assert all('emotion' not in p for p in out)


def test_time_colon_is_not_a_sentence_boundary():
    for text in ('明天15日11:00将进行剪发安排','这是保存的那一项任务现在已经安排到了明天11:00。'):
        parts=[];emitter=SentenceEmitter(parts.append)
        for i in range(len(text)+1):emitter.accept(text[:i])
        emitter.accept(text,final=True)
        assert ''.join(parts)==text
        assert not any(p.endswith((':','11','11:0')) for p in parts)


def test_stream_uses_safety_and_no_duplicate_fallback(tmp_path):
    class Remote:
        last_emotion_signal = ('calm', .3)
        def generate(self, text, **kwargs):
            kwargs['context']['_on_sentence']('这是已生成的第一句。')
            raise CompanionError('fail', 'mock transport failure')
    mind = CompanionCore(database_path=tmp_path/'mind.db', provider=HybridDialogueModel(remote=Remote()))
    client = TestClient(create_app(companion=mind, workspace=WorkSpace(tmp_path/'work.db'), manage_supervisor=False))
    events = [json.loads(line) for line in client.post('/api/companion/stream', json={'text':'你好'}).text.splitlines()]
    assert [e['text'] for e in events if e['type']=='sentence'] == ['这是已生成的第一句。']
    assert any(e['type']=='error' for e in events)
    assert client.post('/api/companion/stream', json={'text':''}).status_code == 422
    safe = [json.loads(line) for line in client.post('/api/companion/stream', json={'text':'我的密码是123456'}).text.splitlines()]
    assert any(e['type']=='sentence' for e in safe)
    assert not any('123456' in e.get('text','') for e in safe)


def test_tts_is_prefetched_while_first_sentence_plays(monkeypatch):
    monkeypatch.setenv('ENMOTION_PCM_STREAM','0')
    import enmotion_robot.listener as listener
    second_ready = threading.Event()
    played = []
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def __iter__(self):
            for text in ('第一句。','第二句。'):
                yield json.dumps({'type':'sentence','text':text}).encode()
            yield b'{"type":"done","emotion":"happy"}'
    def api(path, payload):
        if payload['text']=='第二句。': second_ready.set()
        return {'audio_url':'/audio/test.wav'}
    def play(text, audio, emotion, on_start):
        on_start()
        if not played: assert second_ready.wait(2)
        played.append(text)
    monkeypatch.setattr(listener.urllib.request,'urlopen',lambda *a,**kw:Response())
    monkeypatch.setattr(listener,'api',api)
    monkeypatch.setattr(listener,'play_audio',play)
    monkeypatch.setattr(listener,'publish',lambda *a,**kw:None)
    listener.respond('测试')
    assert played == ['第一句。','第二句。']
