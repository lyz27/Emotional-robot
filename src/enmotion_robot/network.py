"""Bounded public information retrieval, isolated from private work records.

Only provider endpoints are fetched; search result links are never executed or
followed. Search descriptions are snippets, not verified full-page evidence.
"""
from copy import deepcopy
from datetime import datetime
from html import unescape
import ipaddress
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Asia/Shanghai')


def clean(value,limit=600):
    return re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]*>','',str(value or '')))).strip()[:limit]


def public_url(value):
    try:
        url=urlsplit(value)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:return False
        host=url.hostname.lower()
        if '.' not in host or host.endswith(('.local','.localhost','.internal','.lan')):return False
        try:return ipaddress.ip_address(host).is_global
        except ValueError:return True
    except (ValueError,TypeError):return False


def fetch(url):
    request=Request(url,headers={'User-Agent':'EnmotionRobot/1.0','Accept-Encoding':'identity'})
    for attempt,timeout in enumerate((5,3)):
        try:
            with urlopen(request,timeout=timeout) as response:
                body=response.read(160001)
                if len(body)>160000:raise ValueError('Provider response too large')
                return body
        except HTTPError as exc:
            if attempt or exc.code<500:raise
        except (URLError,TimeoutError,ConnectionError):
            if attempt:raise


def query_text(text):
    text=re.sub(r'[，,。！？!?]+$','',text.strip())
    return re.sub(r'^(?:(?:请|麻烦|帮我|给我|小蓝|联网|上网|搜索|查询|查一下|查一查|查查|搜一下|搜一搜|百度一下|看看|看一下|告诉我|一下)[，,：:\s]*)+','',text).strip()


class Network:
    def __init__(self,transport=fetch,clock=lambda:datetime.now(TZ)):
        self.transport,self.clock=transport,clock
        self.lock=threading.RLock();self.cache={};self.last=None;self.sessions={}

    def wants(self,text,session='voice'):
        if re.search(r'不要联网|不用联网|别联网|不要搜索|不用搜索',text):return False
        if re.search(r'记住|提醒我|记一下|记下来|保存|创建|添加|改到|改成|完成了|取消',text):return False
        if re.search(r'笔记|待办|安排|日程|备忘|事项|我的|任务|我明天|我今天|记录',text) and not re.search(r'联网|上网|搜索|搜一下|搜一搜',text):return False
        if re.search(r'联网|上网|搜索|搜一下|搜一搜|百度一下|查一下|查一查|查查|天气|气温|温度|几度|会不会下雨|下不下雨|会下雨|冷不冷|热不热|最新.*(?:消息|新闻|进展|价格|版本)|今天.*新闻',text):return True
        state=self.sessions.get(session,{})
        return bool(state.get('weather') and time.monotonic()-state.get('at',0)<300
                    and (state.get('waiting_city') or re.fullmatch(r'(?:那)?(?:今天|明天|后天)(?:呢|的呢)?[？?。 ]*',text)))

    def status(self):
        with self.lock:
            return {'enabled':True,'provider':'Bing public search RSS / Open-Meteo',
                    'last':deepcopy(self.last),'privacy':'Only current query is sent; no private database context'}

    def _get(self,url):return self.transport(url)

    def search(self,query):
        raw=self._get('https://cn.bing.com/search?'+urlencode({'format':'rss','q':query,'setlang':'zh-hans'}))
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Invalid search response')
        root=ET.fromstring(raw)
        if root.tag!='rss':raise ValueError('Search provider returned a challenge or non-RSS response')
        results=[];seen=set()
        for item in root.findall('./channel/item'):
            url=clean(item.findtext('link'),2000)
            if not public_url(url) or url in seen:continue
            title=clean(item.findtext('title'),160);snippet=clean(item.findtext('description'),450)
            if not title or not snippet:continue
            seen.add(url)
            results.append({'id':len(results)+1,'title':title,'url':url,'snippet':snippet,
                            'domain':urlsplit(url).hostname,'result_time':clean(item.findtext('pubDate'),100) or None})
            if len(results)==3:break
        return results

    def run(self,text,session='voice'):
        text=str(text).strip()
        if not 1<=len(text)<=300:raise ValueError('联网问题需要1～300个字符')
        with self.lock:
            state=deepcopy(self.sessions.get(session,{}))
        weather=bool(re.search(r'天气|气温|温度|几度|会不会下雨|下不下雨|会下雨|冷不冷|热不热',text) or
                     (state.get('weather') and time.monotonic()-state.get('at',0)<300 and
                      (state.get('waiting_city') or re.fullmatch(r'(?:那)?(?:今天|明天|后天)(?:呢|的呢)?[？?。 ]*',text))))
        if weather:
            return self.weather(text,session,state)
        query=query_text(text)
        if not query:return {'reply':'想查什么信息？请告诉我关键词。','sources':[],'kind':'clarification'}
        key=('search',query)
        with self.lock:
            cached=self.cache.get(key)
            if cached and time.monotonic()-cached[0]<60:
                result=deepcopy(cached[1]);result['cached']=True;self.last=result;return result
        now=self.clock().isoformat(timespec='seconds')
        try:
            results=self.search(query)
            if not results:
                # Broaden mixed technical queries without inventing new keywords.
                latin=' '.join(re.findall(r'[A-Za-z0-9][A-Za-z0-9.+_-]*',query))
                if len(latin)>=5 and latin!=query:results=self.search(latin)
            if not results:
                result={'kind':'search','query':query,'fetched_at':now,'sources':[],
                        'reply':'这次没有检索到有效结果，请换几个关键词。我不会用模型猜测冒充联网结果。','ok':False}
            else:
                first=results[0]
                # Deterministic extraction avoids treating web text as tool instructions.
                result={'kind':'search','query':query,'fetched_at':now,'sources':results,'ok':True,
                        'reply':f'检索到的摘要，来源{first["domain"]}：{first["snippet"][:180].rstrip("。.")}。这是搜索摘要，详情和其他来源可在联网查询页查看。',
                        'evidence':'search_snippets_not_full_page_verification'}
        except Exception:
            result={'kind':'search','query':query,'fetched_at':now,'sources':[],'ok':False,
                    'reply':'这次联网查询暂时失败，可能是网络或搜索服务不可用。没有取得可核验的结果，请稍后重试。'}
        with self.lock:
            self.last=deepcopy(result)
            if result['ok']:
                if len(self.cache)>=32:self.cache.pop(next(iter(self.cache)))
                self.cache[key]=(time.monotonic(),deepcopy(result))
        return result

    def weather(self,text,session,state):
        if re.search(r'下周|本周|周[一二三四五六日天末]|星期|\d+天后|昨天|前天',text):
            return {'kind':'clarification','sources':[],'reply':'目前可以查询今天、明天、后天和大后天的城市天气，请指定其中一天。'}
        query=query_text(text)
        query=re.sub(r'^(?:我在|我住在|我这里是|在)','',query)
        city=re.sub(r'大后天|后天|明天|今天|现在|此刻|目前|天气预报|天气|气温|温度|会不会下雨|下不下雨|会下雨|冷不冷|热不热|下雨|怎么样|怎样|如何|多少度|几度|多少|这里|这边|本地|我这|呢|吗|[的，,。？?\s]','',query).strip()
        if re.fullmatch(r'(?:那)?(?:今天|明天|后天)(?:呢|的呢)?[？?。 ]*',text):city=''
        if not city:city=state.get('city','')
        if not city:
            with self.lock:self.sessions[session]={'weather':True,'waiting_city':True,'at':time.monotonic()}
            return {'kind':'clarification','sources':[],'reply':'要查哪个城市的天气？例如上海。我不会自动定位您的位置。'}
        if not re.fullmatch(r'[\w\u4e00-\u9fff ,.-]{2,60}',city):
            return {'kind':'clarification','sources':[],'reply':'请告诉我城市名称，例如上海或北京。'}
        day_offset=3 if '大后天' in text else 2 if '后天' in text else 1 if '明天' in text else 0
        key=('weather',city,day_offset)
        with self.lock:
            cached=self.cache.get(key)
            if cached and time.monotonic()-cached[0]<60:
                result=deepcopy(cached[1]);result['cached']=True;self.last=result
                self.sessions[session]={'weather':True,'waiting_city':False,'city':city,'at':time.monotonic()}
                return result
        now=self.clock().isoformat(timespec='seconds')
        try:
            geo_url='https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':city,'count':1,'language':'zh','format':'json'})
            places=json.loads(self._get(geo_url)).get('results',[])
            if not places and city.endswith('市'):
                places=json.loads(self._get('https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':city[:-1],'count':1,'language':'zh','format':'json'}))).get('results',[])
            if not places:raise LookupError('City not found')
            place=places[0]
            lat,lon=float(place['latitude']),float(place['longitude'])
            if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid coordinates')
            forecast_url='https://api.open-meteo.com/v1/forecast?'+urlencode({'latitude':lat,'longitude':lon,'timezone':'auto','forecast_days':4,
                'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max'})
            daily=json.loads(self._get(forecast_url))['daily']
            date=daily['time'][day_offset];low=daily['temperature_2m_min'][day_offset];high=daily['temperature_2m_max'][day_offset];rain=daily['precipitation_probability_max'][day_offset]
            if not all(type(v) in (int,float) for v in (low,high,rain)):raise ValueError('Missing weather values')
            name=place['name'];label=('今天','明天','后天','大后天')[day_offset]
            with self.lock:self.sessions[session]={'weather':True,'waiting_city':False,'city':city,'at':time.monotonic()}
            result={'kind':'weather','query':text,'fetched_at':now,'ok':True,
                    'reply':f'{name}{label}的预报：最低{low:g}度，最高{high:g}度，降水概率{rain:g}%。数据来自Open-Meteo，预报可能变化。',
                    'weather':{'city':name,'country':place.get('country'),'date':date,'low_c':low,'high_c':high,'rain_probability':rain,'timezone':place.get('timezone')},
                    'sources':[{'id':1,'title':'Open-Meteo Weather Forecast','url':forecast_url,'domain':'api.open-meteo.com','snippet':f'{name} {date} 天气预报'}]}
        except Exception:
            result={'kind':'weather','query':text,'fetched_at':now,'ok':False,'sources':[],
                    'reply':'没有成功取得这个城市的天气预报，请确认城市名称，或稍后重试。我没有使用模型猜天气。'}
        with self.lock:
            self.last=deepcopy(result)
            if result['ok']:
                if len(self.cache)>=32:self.cache.pop(next(iter(self.cache)))
                self.cache[key]=(time.monotonic(),deepcopy(result))
        return result
