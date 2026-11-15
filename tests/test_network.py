from datetime import datetime
import json
from urllib.parse import parse_qs,urlsplit
import pytest
from fastapi.testclient import TestClient
from enmotion_robot.network import Network,public_url,TZ
from enmotion_robot.web import create_app
from enmotion_robot.work import WorkSpace
from enmotion_robot.companion import CompanionCore

RSS=b'''<?xml version="1.0"?><rss><channel><item><title>Official docs</title><link>https://developer.nvidia.com/example</link><description>Public information from the official documentation.</description></item><item><title>invalid</title><link>javascript:alert(1)</link><description>invalid</description></item></channel></rss>'''


def test_search_encodes_only_query_and_reuses_fresh_cache():
    calls=[]
    def fetch(url):calls.append(url);return RSS
    n=Network(fetch)
    result=n.run('帮我联网查一下 Jetson Orin Nano')
    assert result['ok'] and len(result['sources'])==1
    assert parse_qs(urlsplit(calls[0]).query)['q']==['Jetson Orin Nano']
    assert '官方' not in result['reply']  # no invented interpretation
    assert n.run('帮我联网查一下 Jetson Orin Nano')['cached']
    assert len(calls)==1 and n.status()['last']['fetched_at']==result['fetched_at']


@pytest.mark.parametrize('url',['file:///etc/passwd','javascript:alert(1)','http://127.0.0.1/','http://10.0.0.1/','http://[::1]/','http://robot.local/','https://user:pass@example.com','http://localhost/'])
def test_private_or_executable_source_links_rejected(url):assert not public_url(url)


@pytest.mark.parametrize('body',[b'<html>captcha</html>',b'<!DOCTYPE rss><rss/>',b'not xml',b'<rss><channel/></rss>'])
def test_empty_challenge_or_broken_results_never_invent_an_answer(body):
    r=Network(lambda _:body).run('搜索普通信息')
    assert not r['ok'] and not r['sources']


def test_fetch_error_has_explicit_failure_and_no_stale_result():
    def fetch(_):raise TimeoutError()
    n=Network(fetch);r=n.run('搜索测试')
    assert not r['ok'] and '失败' in r['reply']
    assert n.status()['last']==r


@pytest.mark.parametrize('text',['我明天要干什么','查一下明天安排','查询我的笔记','明天11点提醒我查天气','记住：联网搜索是一个功能','不要联网搜索这句话'])
def test_local_commands_do_not_become_external_queries(text):assert not Network().wants(text)


@pytest.mark.parametrize('text',['上海明天天气怎么样','帮我查一下乐山大佛开放时间','帮我搜索Jetson Orin Nano官方文档','帮我联网查一下明天上海天气'])
def test_public_query_routing(text):assert Network().wants(text)


def weather_transport(calls):
    def fetch(url):
        calls.append(url)
        if 'geocoding' in url:return json.dumps({'results':[{'name':'上海','latitude':31.2,'longitude':121.4,'country':'中国','timezone':'Asia/Shanghai'}]}).encode()
        return json.dumps({'daily':{'time':['2026-11-15','2026-11-16','2026-11-17','2026-11-18'],
            'temperature_2m_min':[10,11,12,13],'temperature_2m_max':[20,21,22,23],
            'precipitation_probability_max':[10,20,30,40]}}).encode()
    return fetch


def test_weather_asks_city_then_follows_up_tomorrow_without_guessing_location():
    calls=[];n=Network(weather_transport(calls))
    assert '哪个城市' in n.run('今天天气怎么样')['reply']
    assert not calls
    assert n.wants('上海')
    r=n.run('上海');assert r['weather']['low_c']==10
    assert n.wants('那明天呢')
    r=n.run('那明天呢')
    assert r['weather']['date']=='2026-11-16' and r['weather']['high_c']==21
    assert '预报' in r['reply'] and 'Open-Meteo' in r['reply']


def test_weather_public_question_wins_over_tomorrow_task_query():
    n=Network(weather_transport([]))
    assert n.run('帮我查一下明天上海天气')['weather']['date']=='2026-11-16'


def test_web_and_voice_keep_work_private_and_return_sources(tmp_path):
    work=WorkSpace(tmp_path/'work.db');work.save('notes',{'title':'私人项目','body':'do-not-send-this-private-note'})
    mind=CompanionCore(database_path=tmp_path/'mind.db')
    app=create_app(workspace=work,companion=mind,manage_supervisor=False)
    calls=[];app.state.network.transport=lambda url:(calls.append(url) or RSS)
    client=TestClient(app)
    response=client.post('/api/companion/stream',json={'text':'帮我搜索Jetson Orin Nano官方文档'})
    events=[json.loads(x) for x in response.text.splitlines()]
    assert events[0]['type']=='status' and events[-1]['search']['sources']
    assert 'do-not-send' not in str(calls)
    assert not work.snapshot()['tasks']
    calls.clear()
    r=client.post('/api/search',json={'text':'我的密码是123456，帮我搜索一下'})
    assert not r.json()['ok'] and not calls
    assert client.post('/api/search',json={'text':''}).status_code==422
    assert client.post('/api/search',json={'text':['bad']} ).status_code==422
    assert client.get('/api/search/status').json()['last']['sources']
    assert client.get('/search').status_code==200
    r=client.post('/api/work/chat',json={'text':'帮我搜索公开资料','mode':'chat'})
    assert r.json()['search']['sources']
    calls.clear()
    r=client.post('/api/work/chat',json={'text':'查一下明天安排','mode':'chat'})
    assert r.status_code==200 and not calls


def test_outbound_request_is_bounded(monkeypatch):
    import enmotion_robot.network as network
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):assert limit==160001;return b'x'*limit
    monkeypatch.setattr(network,'urlopen',lambda req,timeout:Response())
    with pytest.raises(ValueError):network.fetch('https://cn.bing.com/search')


def test_transient_network_timeout_retries_once(monkeypatch):
    import enmotion_robot.network as network
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return RSS
    def open_url(request,timeout):
        calls.append(timeout)
        if len(calls)==1:raise TimeoutError()
        return Response()
    monkeypatch.setattr(network,'urlopen',open_url)
    assert network.fetch('https://cn.bing.com/search')==RSS
    assert calls==[5,3]


def test_weather_cache_preserves_real_fetch_time():
    calls=[];n=Network(weather_transport(calls))
    first=n.run('上海明天天气怎么样')
    second=n.run('上海明天天气怎么样')
    assert second['cached'] and second['fetched_at']==first['fetched_at']
    assert len(calls)==2
