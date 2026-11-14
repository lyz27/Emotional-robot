from enmotion_robot.wellness import WellnessRules
from enmotion_robot.web import create_app
from fastapi.testclient import TestClient


class Clock:
    now=1000.
    def __call__(self):return self.now


def setup(tmp_path):
    clock=Clock()
    rules=WellnessRules(tmp_path/'care.json',clock,clock)
    def advance(seconds,present=True,online=True,face=True):
        for _ in range(seconds):
            clock.now+=1
            rules.update({'online':online,'updated_at':clock.now,'present':present,
                          'detections':[{'kind':'face'}] if face else []})
    return rules,clock,advance


def test_arrival_stable_lease_and_voice_priority(tmp_path):
    r,c,advance=setup(tmp_path)
    advance(2);assert r.claim(True) is None
    advance(1);assert r.claim(False) is None
    job=r.claim(True);assert job['kind']=='welcome'
    assert r.claim(True) is None
    c.now+=21;assert r.claim(True)['id']==job['id']
    assert r.acknowledge(job['id'])
    assert not r.acknowledge(job['id'])
    advance(10,False);advance(5)
    assert r.claim(True) is None


def test_water45_break60_and_no_timer_while_absent(tmp_path):
    r,c,advance=setup(tmp_path)
    r.configure({'welcome':False})
    advance(2700);assert r.claim(True) is None
    advance(1);job=r.claim(True);assert job['kind']=='water'
    r.acknowledge(job['id']);assert r.water_seconds==0
    advance(900);job=r.claim(True);assert job['kind']=='break'
    r.acknowledge(job['id']);assert r.work_seconds==0
    advance(121,False);assert r.water_seconds==0
    advance(10,True,False);assert r.work_seconds==0


def test_person_without_face_does_not_start_timer_and_settings_persist(tmp_path):
    r,c,advance=setup(tmp_path)
    advance(200,face=False);assert not r.visible and not r.pending
    r.configure({'enabled':False,'water_minutes':50})
    r2=WellnessRules(r.path,c,c)
    assert r2.config['water_minutes']==50 and not r2.config['enabled']
    import pytest
    for payload in ({'water_minutes':True},{'break_minutes':0},{'foo':1}):
        with pytest.raises(ValueError):r.configure(payload)


def test_return_after_two_minutes_and_cooldown(tmp_path):
    r,c,advance=setup(tmp_path)
    advance(3);r.acknowledge(r.claim(True)['id'])
    advance(121,False);advance(3);assert r.claim(True) is None
    advance(600);assert r.claim(True)['kind']=='welcome'


def test_api_settings_and_busy_defer(tmp_path,monkeypatch):
    import enmotion_robot.web as web
    monkeypatch.setattr(web,'STATIC_DIR',tmp_path/'src/enmotion_robot/static')
    app=create_app(manage_supervisor=False)
    with TestClient(app) as client:
        assert client.get('/api/wellness').json()['config']['water_minutes']==45
        assert client.patch('/api/wellness',json={'break_minutes':60}).status_code==200
        assert client.patch('/api/wellness',json={'water_minutes':-1}).status_code==422
        assert client.post('/api/wellness/claim').json()['job'] is None
        import json,time
        r=app.state.wellness
        r.visible=True
        r.enqueue('water',r.clock())
        app.state.orchestration.present=True
        app.state.orchestration.state.update(online=True,updated_at=time.time(),detections=[{'kind':'face'}])
        listener=tmp_path/'runtime/listener.json'
        listener.write_text(json.dumps({'phase':'listening','at':time.time()}))
        assert client.post('/api/wellness/claim').json()['job'] is None
        listener.write_text(json.dumps({'phase':'waiting','at':time.time()}))
        job=client.post('/api/wellness/claim').json()['job']
        assert job['kind']=='water'
        assert client.post('/api/wellness/ack',json={'id':job['id']}).json()['ok']
