from datetime import datetime,timedelta
import json
import pytest
from fastapi.testclient import TestClient
from enmotion_robot.assistant import Assistant,TZ,parse_time
from enmotion_robot.work import WorkSpace
from enmotion_robot.companion import CompanionCore,HybridDialogueModel
from enmotion_robot.web import create_app


NOW=datetime(2026,11,14,21,0,tzinfo=TZ)


@pytest.fixture
def assistant(tmp_path):return Assistant(WorkSpace(tmp_path/'work.db'),lambda:NOW)


def test_schedule_recall_after_restart_and_idempotency(assistant):
    a=assistant
    result=a.handle('明天十一点提醒我去剪头发',request_id='one')
    assert result['record']['title']=='剪头发'
    assert result['record']['due']=='2026-11-15T03:00:00+00:00'
    restored=Assistant(WorkSpace(a.work.path),lambda:NOW)
    assert '剪头发' in restored.handle('我明天要干什么')['reply']
    assert restored.handle('明天十一点提醒我去剪头发',request_id='one')==result
    restored.handle('明天十一点提醒我去剪头发')
    assert len(restored.work.snapshot()['tasks'])==1
    with pytest.raises(ValueError):restored.handle('记下别的事情',request_id='one')


def test_resolve_modify_complete_and_cancel_without_ids(assistant):
    a=assistant
    a.handle('帮我记录明天11点提醒我去剪头发')
    changed=a.handle('把刚才那项改到后天下午两点半')
    assert changed['record']['due']=='2026-11-16T06:30:00+00:00'
    assert '剪头发' in a.handle('我后天有哪些安排')['reply']
    assert '标记完成' in a.handle('剪头发已经做完了')['reply']
    assert not a.handle('我后天要干什么').get('tasks')
    assert '已取消' in a.handle('取消剪头发的提醒')['reply']
    assert a.work.snapshot()['tasks'][0]['due'] is None
    assert '已取消' in a.handle('删除剪头发这个待办')['reply']
    assert not a.work.snapshot()['tasks']


def test_missing_time_followup_survives_restart(assistant):
    a=assistant
    result=a.handle('帮我记一下明天交方案')
    assert '几点' in result['reply'] and not a.work.snapshot()['tasks']
    a=Assistant(WorkSpace(a.work.path),lambda:NOW)
    result=a.handle('上午十点')
    assert result['record']['title']=='交方案'
    assert result['record']['due']=='2026-11-15T02:00:00+00:00'


def test_ambiguous_time_does_not_guess_and_allows_clarification(assistant):
    a=assistant
    assert '明确' in a.handle('明天三点提醒我开会')['reply']
    assert not a.work.snapshot()['tasks']
    assert a.handle('下午三点')['record']['due']=='2026-11-15T07:00:00+00:00'


def test_past_time_and_ambiguous_target_do_not_write(assistant):
    a=assistant
    assert '过去' in a.handle('今天十一点提醒我开会')['reply']
    assert not a.work.snapshot()['tasks']
    a.work.save('tasks',{'title':'开会'});a.work.save('tasks',{'title':'开会'})
    assert '哪一项' in a.handle('把开会改到明天上午十点')['reply']
    assert all(t['due'] is None for t in a.work.snapshot()['tasks'])


def test_notes_saved_queried_and_retrieved(assistant):
    a=assistant
    a.handle('记住：演示用蓝色方案')
    for i in range(8):a.work.save('notes',{'title':f'其他记录{i}','body':f'其他内容{i}'})
    assert any('蓝色方案' in n['body'] for n in a.context('演示用什么方案')['notes'])
    assert '蓝色方案' in a.handle('查询演示用蓝色方案的笔记')['reply']
    a.handle('记住：不要使用红色方案，把待办改到后天是什么效果只是讨论')
    assert len(a.work.snapshot()['notes'])==10
    assert not a.work.snapshot()['tasks']


@pytest.mark.parametrize('text',['不要帮我记录明天11点开会','假如我让你提醒我开会','如果明天下雨你会提醒我吗'])
def test_discussion_is_not_an_action(assistant,text):
    assert assistant.handle(text) is None
    assert not assistant.work.snapshot()['tasks']


def test_record_question_does_not_create_new_record(assistant):
    assistant.handle('明天11点提醒我剪头发')
    assert '剪头发' in assistant.handle('你记录了什么')['reply']
    assert len(assistant.work.snapshot()['tasks'])==1


@pytest.mark.parametrize('text,expected',[
    ('明天下午两点半', '2026-11-15T14:30'),
    ('后天上午十点一刻', '2026-11-16T10:15'),
    ('下周一下午三点', '2026-11-16T15:00'),
    ('十五分钟后', '2026-11-14T21:15'),
    ('晚上十一点', '2026-11-14T23:00'),
])
def test_time_parsing(text,expected):
    assert parse_time(text,NOW)[0].isoformat().startswith(expected)


def test_new_year_boundary():
    assert parse_time('明天十一点',datetime(2026,12,31,23,tzinfo=TZ))[0].year==2027


def test_durable_delivery_leases_done_and_time_changes(assistant):
    a=assistant;work=a.work
    record=work.save('tasks',{'title':'准时提醒测试','due':(NOW-timedelta(minutes=1)).isoformat()})
    job=a.claim_reminder();assert job['kind']=='task'
    restarted=Assistant(WorkSpace(work.path),lambda:NOW)
    assert restarted.claim_reminder() is None
    restarted.clock=lambda:NOW+timedelta(seconds=121)
    retry=restarted.claim_reminder();assert retry and retry['id']!=job['id']
    assert not restarted.acknowledge_reminder(job['id'])
    assert restarted.acknowledge_reminder(retry['id'])
    assert not restarted.acknowledge_reminder(retry['id'])
    restarted.clock=lambda:NOW+timedelta(hours=1)
    assert restarted.claim_reminder() is None
    work.save('tasks',{'due':(NOW+timedelta(minutes=10)).isoformat()},record['id'])
    assert restarted.claim_reminder()
    work.save('tasks',{'done':True},record['id'])
    restarted.clock=lambda:NOW+timedelta(hours=2)
    assert restarted.claim_reminder() is None
    work.delete('tasks',record['id'])
    new=work.save('tasks',{'title':'新提醒','due':(NOW-timedelta(minutes=1)).isoformat()})
    assert restarted.claim_reminder()['text'].startswith('提醒您：新提醒')


def test_voice_stream_persists_work_and_conversation_across_app_restart(tmp_path):
    path=tmp_path/'work.db';mindpath=tmp_path/'mind.db'
    class Remote:
        last_emotion_signal=None
        def generate(self,text,**kwargs):
            assert kwargs['context']['messages']
            return '已经接上前面的对话。'
    def app():
        mind=CompanionCore(database_path=mindpath,provider=HybridDialogueModel(remote=Remote()))
        instance=create_app(companion=mind,workspace=WorkSpace(path),manage_supervisor=False)
        instance.state.assistant.clock=lambda:NOW
        return TestClient(instance)
    client=app()
    def say(client,text):
        return [json.loads(x) for x in client.post('/api/companion/stream',json={'text':text}).text.splitlines()]
    assert any('已记下' in e.get('text','') for e in say(client,'明天11点提醒我剪头发'))
    client=app()
    assert any('剪头发' in e.get('text','') for e in say(client,'我明天要干什么'))
    assert any('接上' in e.get('text','') for e in say(client,'继续聊聊'))
    assert len(WorkSpace(path).snapshot()['tasks'])==1
    # Safety is checked before any assistant tool or transcript persistence.
    say(client,'我的密码是123456，帮我记下来')
    assert '123456' not in str(WorkSpace(path).snapshot())
    assert not any('123456' in m['content'] for m in CompanionCore(database_path=mindpath).store.conversation_context('voice')['messages'])


def test_failed_write_never_acknowledges_success(assistant,monkeypatch):
    def fail(*a,**kw):raise OSError('disk unavailable')
    monkeypatch.setattr(assistant.work,'save',fail)
    with pytest.raises(OSError):assistant.handle('明天11点提醒我剪头发')
    assert not assistant.work.snapshot()['tasks']


def test_update_reads_new_time_not_original_date_and_relative_shift(assistant):
    a=assistant
    a.handle('明天十一点提醒我剪头发')
    r=a.handle('把明天的剪头发改到后天下午两点')
    assert r['record']['due']=='2026-11-16T06:00:00+00:00'
    assert a.handle('改到下午四点')['record']['due']=='2026-11-16T08:00:00+00:00'
    assert a.handle('把剪头发提前半小时')['record']['due']=='2026-11-16T07:30:00+00:00'
    assert '15:30' in a.handle('刚才让你记的是几点')['reply']


def test_completion_query_cannot_complete_an_item(assistant):
    assistant.handle('明天十一点提醒我剪头发')
    assert '没有' in assistant.handle('我今天完成了什么')['reply']
    assert not assistant.work.snapshot()['tasks'][0]['done']


def test_due_reminder_api_defers_voice_and_does_not_need_open_browser_or_face(tmp_path,monkeypatch):
    import enmotion_robot.web as web
    monkeypatch.setattr(web,'STATIC_DIR',tmp_path/'src/enmotion_robot/static')
    work=WorkSpace(tmp_path/'work.db')
    work.save('tasks',{'title':'工作提醒','due':(NOW-timedelta(minutes=1)).isoformat()})
    app=create_app(workspace=work,manage_supervisor=False)
    app.state.assistant.clock=lambda:NOW
    path=tmp_path/'runtime/listener.json';path.parent.mkdir(exist_ok=True)
    import time
    path.write_text(json.dumps({'phase':'listening','at':time.time()}))
    client=TestClient(app)
    assert client.post('/api/wellness/claim').json()['job'] is None
    path.write_text(json.dumps({'phase':'waiting','at':time.time()}))
    job=client.post('/api/wellness/claim').json()['job']
    assert job['kind']=='task' and '工作提醒' in job['text']
    assert client.post('/api/wellness/ack',json={'id':job['id']}).json()['ok']
    assert client.post('/api/wellness/claim').json()['job'] is None


def test_agent_write_guard_rejects_negated_and_hypothetical_writes(tmp_path):
    from enmotion_robot.work_agent import WorkTools
    work=WorkSpace(tmp_path/'work.db')
    for text in ('不要记录明天十一点开会','假如明天十一点提醒我开会'):
        r=WorkTools(work,user_text=text).execute({'operation':'create_task','title':'开会','due':'2026-11-15T11:00:00+08:00'})
        assert not r['ok']
    assert not work.snapshot()['tasks']
