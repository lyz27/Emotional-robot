"""Durable assistant actions, Chinese scheduling and grounded recall.

Common record operations execute locally; ambiguous requests ask for clarification.
The language model is never the source of truth for a successful write.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re
import threading
import uuid

TZ = ZoneInfo('Asia/Shanghai')
NUM = r'[零〇一二三四五六七八九十两\d]+'
DAY = r'今天|明天|后天|大后天|(?:这|下)?(?:周|星期)[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]'
CLOCK = rf'(?:(早上|上午|中午|下午|傍晚|晚上|凌晨))?\s*({NUM})(?:[:：]([0-5]\d)|[点时](半|一刻|三刻|{NUM}分?)?)'


def number(value):
    if value.isdigit():return int(value)
    digits=dict(zip('零〇一二两三四五六七八九','001223456789'))
    if '十' in value:
        a,b=value.split('十',1)
        return (int(digits[a]) if a else 1)*10+(int(digits[b]) if b else 0)
    return int(digits[value])


def parse_time(text,now,base_day=None):
    """Return due/day/has_time/spans. Raises rather than guessing ambiguous clocks."""
    now=now.astimezone(TZ);day=base_day;spans=[]
    relative=re.search(rf'({NUM})(分钟|小时)后',text)
    if relative:
        count=number(relative[1]);delta=timedelta(minutes=count) if relative[2]=='分钟' else timedelta(hours=count)
        if count<1:raise ValueError('请告诉我一个大于零的提醒间隔。')
        return now+delta,None,True,[relative.span()]
    match=re.search(DAY,text)
    if match:
        token=match[0];spans.append(match.span())
        if token in ('今天','明天','后天','大后天'):
            day=(now+timedelta(days=('今天','明天','后天','大后天').index(token))).date()
        elif '月' in token:
            month,date=map(int,re.findall(r'\d+',token));day=now.date().replace(month=month,day=date)
            if day<now.date():day=day.replace(year=day.year+1)
        else:
            weekday='一二三四五六日'.index(token[-1].replace('天','日'))
            delta=weekday-now.weekday()
            if token.startswith('下'):delta+=7
            elif not token.startswith('这') and delta<0:delta+=7
            day=(now+timedelta(days=delta)).date()
    match=re.search(CLOCK,text)
    if not match:return None,day,False,spans
    spans.append(match.span());period,hour,colon,minutes=match.groups();hour=number(hour)
    minute=int(colon) if colon else {'半':30,'一刻':15,'三刻':45}.get(minutes,0)
    if minutes and minutes not in ('半','一刻','三刻'):minute=number(minutes.rstrip('分'))
    if not 0<=hour<=23 or not 0<=minute<=59:raise ValueError('这个时间不正确，请告诉我几点几分。')
    if period in ('下午','傍晚','晚上') and hour<12:hour+=12
    if period=='中午' and hour<11:hour+=12
    if period in ('上午','早上','凌晨') and hour==12:hour=0
    if not period and 1<=hour<=6:raise ValueError('您说的是上午还是下午？请带上具体几点。')
    due=datetime.combine(day or now.date(),datetime.min.time(),TZ).replace(hour=hour,minute=minute)
    if not day and due<=now:due+=timedelta(days=1)
    return due,day,True,spans


def stamp(value):
    return datetime.fromisoformat(value).astimezone(TZ).strftime('%m月%d日 %H:%M')


class Assistant:
    def __init__(self,work,clock=lambda:datetime.now(TZ)):
        self.work,self.clock=work,clock
        self.lock=threading.RLock()
        with work.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assistant_state(session TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_receipts(request_id TEXT PRIMARY KEY, text TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_notifications(
                    task_id INTEGER NOT NULL, due TEXT NOT NULL, token TEXT NOT NULL,
                    lease_until TEXT, delivered_at TEXT, PRIMARY KEY(task_id,due));
            ''')

    def state(self,session):
        with self.work.connect() as db:
            row=db.execute('SELECT data FROM assistant_state WHERE session=?',(session,)).fetchone()
        return json.loads(row[0]) if row else {}

    def set_state(self,session,state):
        with self.work.connect() as db:
            db.execute('INSERT OR REPLACE INTO assistant_state VALUES(?,?)',(session,json.dumps(state,ensure_ascii=False)))

    def context(self,text=''):
        state=self.work.snapshot()
        words={text[i:i+2] for i in range(max(0,len(text)-1))}
        notes=sorted(state['notes'],key=lambda n:sum(w in n['title']+n['body'] for w in words),reverse=True)
        tasks=sorted(state['tasks'],key=lambda t:(sum(w in t['title'] for w in words),not t['done'],t['id']),reverse=True)
        return {'now_beijing':self.clock().isoformat(),'tasks':[{'title':t['title'][:40],'due':t['due'],'done':t['done']} for t in tasks[:4]],
                'notes':[{'title':n['title'][:30],'body':n['body'][:120]} for n in notes[:2]],
                'task_count':len(state['tasks']),'note_count':len(notes),'partial':True}

    def handle(self,text,session='voice',request_id=None):
        with self.lock:
            if request_id:
                if not isinstance(request_id,str) or len(request_id)>100:raise ValueError('请求标识无效')
                with self.work.connect() as db:
                    row=db.execute('SELECT text,result FROM assistant_receipts WHERE request_id=?',(request_id,)).fetchone()
                if row:
                    if row['text']!=text:raise ValueError('请求标识已被其他消息使用')
                    return json.loads(row['result'])
            result=self._handle(text.strip(),session)
            if result:
                self.work.history.add_exchange('work',text,result['reply'])
                if request_id:
                    with self.work.connect() as db:
                        db.execute('INSERT INTO assistant_receipts VALUES(?,?,?)',(request_id,text,json.dumps(result,ensure_ascii=False)))
            return result

    def _handle(self,text,session):
        def reply(message,**extra):return {'reply':message,'source':'local-records',**extra}
        now=self.clock().astimezone(TZ);state=self.state(session)
        pending=state.get('pending')
        if pending and now.timestamp()-pending.get('at',0)>900:pending=None
        if pending and pending.get('original') and re.fullmatch(rf'\s*{CLOCK}[。！!\s]*',text):
            original=pending['original'];state.pop('pending',None);self.set_state(session,state)
            return self._handle(re.sub(CLOCK,lambda _:text.strip('。！! '),original,count=1),session)
        if re.fullmatch(r'(?:算了|取消|不用了|不记了)[。！! ]*',text) and pending:
            state.pop('pending',None);self.set_state(session,state);return reply('好的，已取消这次未完成的记录。')
        literal_note=re.match(r'^(?:请|帮我)?(?:记住|记录(?:一下)?|保存(?:笔记)?|笔记|记下来)[：:]',text)
        # Never interpret negated discussion as permission to mutate records.
        if not literal_note and re.search(r'不要|别帮|不用|不需要|先不|不想|假如|假设|举个例|比如|如果',text):return None
        query=bool(re.search(r'什么|哪些|有哪些|查一下|查询|查查|列出|看看|安排了|待办列表|记得|还记|记住了|记录了|几点|什么时候|有没有|保存了吗|几个任务',text) or re.fullmatch(r'(?:全部|所有|我的)?(?:待办|事项|任务)[。？? ]*',text))
        change=bool(re.search(r'改到|改成|改为|改一下|修改|调整|挪到|推迟|提前|延后|完成了|已完成|做完了|标记.*完成|^完成|取消.*提醒|删除.*(?:待办|事项|提醒)|取消了',text))
        completed_query=bool(query and re.search(r'完成.*(?:什么|哪些)|做完.*什么',text))
        if completed_query:change=False
        create=bool(re.search(r'提醒我|提醒一下|帮我记|记一下|记下来|记住|记下|记录|添加|新增|创建|保存',text))
        if re.search(r'(?:能不能|会不会|可以).*(?:提醒|记录|保存).*[吗？?]',text):return reply('可以，直接告诉我要做什么、哪天几点；我会保存并复述具体时间。')
        notes=bool(re.search(r'笔记|备忘|记住|记录(?:一下)?[：:]|记下来[：:]',text))
        if literal_note:query=False;change=False;notes=True;create=True
        records=self.work.snapshot()
        if query and not change and not re.search(r'(?:笔记|记住|记录一下)[：:]',text):
            if notes:
                matches=[n for n in records['notes'] if n['title'] in text]
                items=matches or records['notes'][:3]
                return reply('已保存的笔记：'+'；'.join(n['body'][:180] for n in items)+'。' if items else '还没有保存的工作笔记。')
            if re.search(r'明天|今天|后天|待办|安排|事项|任务|要做|要干|要办|提醒|记录了什么|记了什么|刚才|刚刚',text) or completed_query or any(t['title'] in text for t in records['tasks']):
                _,day,_,_=parse_time(text,now)
                items=[t for t in records['tasks'] if bool(t['done'])==completed_query]
                named=[t for t in items if t['title'] in text]
                if not named and re.search(r'刚才|刚刚|那件|那项|那条|它',text):named=[t for t in items if t['id']==state.get('last_task')]
                if named:items=named
                if day and not completed_query:items=[t for t in items if t['due'] and datetime.fromisoformat(t['due']).astimezone(TZ).date()==day]
                label=f'{day:%m月%d日}' if day else '当前'
                if completed_query:return reply('已标记完成的事项：'+'、'.join(t['title'] for t in items[:8])+'。旧记录没有完成日期，不能据此判断是哪天做完的。' if items else '目前没有已标记完成的事项。')
                if not items:return reply(f'{label}没有已保存的待办。'+('另外还有未设置时间的事项，可以问我全部待办。' if day and any(not t['done'] and not t['due'] for t in records['tasks']) else ''))
                lines=[(stamp(t['due'])+'，' if t['due'] else '未定时间，')+t['title'] for t in items[:8]]
                return reply(f'{label}的待办：'+'；'.join(lines)+'。'+(f'另有{len(items)-8}项。' if len(items)>8 else ''),tasks=items)
        if re.search(r'先做什么|先干什么|优先做|优先级',text) and not create:
            items=[t for t in records['tasks'] if not t['done']]
            items.sort(key=lambda t:(t['due'] is None,t['due'] or '',t['id']))
            return reply('按到期时间，建议先处理：'+'；'.join(t['title'] for t in items[:3])+'。未设置时间的事项暂排后面。' if items else '目前没有未完成待办。告诉我目标，我可以帮您拆成下一步。')
        if notes and create and not change:
            body=(text[literal_note.end():] if literal_note else re.sub(r'^.*?(?:记住|笔记[：:]?|记录(?:一下)?[：:]?|记下来[：:]?|保存(?:一下)?)[，,：:\s]*','',text)).strip('。 ')
            if not body:return reply('您想保存什么笔记内容？')
            old=next((n for n in records['notes'] if n['body']==body),None)
            record=old or self.work.save('notes',{'title':body[:40],'body':body})
            return reply('这条笔记已经保存。' if old else f'已保存笔记：{body[:100]}。',record=record)
        target=None
        if change:
            matches=[t for t in records['tasks'] if t['title'] in text]
            if not matches and re.search(r'刚才|刚刚|那[个条项]|这[个条项]|它|^(?:改到|改成|改为|挪到|推迟|提前|延后)',text) and now.timestamp()-state.get('last_at',0)<86400:
                matches=[t for t in records['tasks'] if t['id']==state.get('last_task')]
            if len(matches)!=1:
                names='、'.join(t['title'] for t in (matches or [t for t in records['tasks'] if not t['done']])[:4])
                return reply('您要修改哪一项？'+(f'我查到：{names}。' if names else '目前没有匹配的待办。'))
            target=matches[0]
            if re.search(r'完成了|已完成|做完了|标记.*完成|^完成',text):
                record=self.work.save('tasks',{'done':True},target['id']);return reply(f'已将「{record["title"]}」标记完成。',record=record)
            if re.search(r'取消|删除',text):
                if re.search(r'取消.*提醒',text):
                    record=self.work.save('tasks',{'due':None},target['id'])
                    return reply(f'已取消「{record["title"]}」的定时提醒，待办仍保留。',record=record)
                self.work.delete('tasks',target['id']);return reply(f'已取消待办「{target["title"]}」。')
        continuation=bool(pending and re.fullmatch(rf'[，,\s]*(?:{DAY})?\s*{CLOCK}[。！!\s]*',text))
        if not (create or change or continuation):return None
        if not continuation:pending=None
        base_day=datetime.fromisoformat(pending['day']).date() if pending and pending.get('day') else None
        if target and target['due']:base_day=datetime.fromisoformat(target['due']).astimezone(TZ).date()
        schedule=re.split(r'改到|改成|改为|挪到|推迟到|调整到',text)[-1] if target else text
        shifted=re.search(rf'(推迟|延后|提前)\s*({NUM}|半)(小时|分钟)',schedule) if target and target['due'] else None
        try:
            if shifted:
                count=.5 if shifted[2]=='半' else number(shifted[2])
                delta=timedelta(hours=count) if shifted[3]=='小时' else timedelta(minutes=count)
                due=datetime.fromisoformat(target['due']).astimezone(TZ)+(delta if shifted[1]!='提前' else -delta)
                day,has_time,spans=due.date(),True,[]
            else:due,day,has_time,spans=parse_time(schedule,now,base_day)
        except (ValueError,KeyError):
            state['pending']={'original':text,'at':now.timestamp()};self.set_state(session,state)
            return reply('时间还不够明确，请再说具体哪天、上午或下午几点。')
        if target and day and not has_time and target['due']:
            previous=datetime.fromisoformat(target['due']).astimezone(TZ)
            due=datetime.combine(day,previous.timetz(),TZ)
        if pending:
            title=pending['title'];target=next((t for t in records['tasks'] if t['id']==pending.get('target')),None)
        else:
            title=text
            for a,b in reversed(spans):title=title[:a]+title[b:]
            title=re.sub(r'^(?:小蓝[，, ]*)?(?:请|麻烦|帮我|给我|你|再|把|将|一下|提醒我|提醒一下|记一下|记下来|记住|记下|记录|添加|新增|创建|保存|一个|一条|待办|事项|在|到|，|,|：|:|\s)+','',title)
            title=re.sub(r'^(?:提醒我|提醒一下|帮我|给我|记一下|记录一下|去|要)[，,\s]*','',title).strip('，,。！! ：:')
            title=re.sub(r'[，,\s]*(?:提醒我|提醒一下|帮我记录一下|帮我记一下|记一下|记录一下|记录下来|记下来)$','',title).strip('，,。 ')
            if target:title=target['title']
        if not title:return reply('要记录什么事情？请把事项和时间一起告诉我。')
        if (re.search(r'提醒',text) or day or change or pending) and due is None:
            state['pending']={'title':title,'day':(day or base_day).isoformat() if (day or base_day) else None,'target':target['id'] if target else None,'at':now.timestamp()}
            self.set_state(session,state);return reply(f'「{title}」还没有保存提醒，请问具体几点？')
        if due and due<=now:return reply('这个时间已经过去了，请告诉我新的提醒时间；本次没有修改记录。')
        if due and due.date()>now.date()+timedelta(days=366):return reply('请确认具体年份和日期，再设置提醒。')
        if not target and re.search(r'和|以及|另外|再加|并且|同时|然后',title):return None
        payload={'title':title}
        if due:payload['due']=due.isoformat()
        old=next((t for t in records['tasks'] if not t['done'] and t['title']==title and t['due']==self.work.due(payload.get('due'))),None) if not target else None
        record=old or self.work.save('tasks',payload,target['id'] if target else None)
        state.pop('pending',None);state.update(last_task=record['id'],last_at=now.timestamp());self.set_state(session,state)
        message=('这项待办已经存在' if old else '已更新' if target else '已记下')+f'：{title}'
        message+=f'，{stamp(record["due"])}（北京时间）提醒您。' if record['due'] else '。尚未设置提醒时间。'
        return reply(message,record=record)

    def claim_reminder(self):
        now=self.clock();current=now.isoformat();lease=(now+timedelta(seconds=120)).isoformat()
        with self.work.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Space out catch-up announcements, including across process restarts.
            recent=db.execute('SELECT 1 FROM task_notifications WHERE julianday(delivered_at)>julianday(?) LIMIT 1',((now-timedelta(seconds=30)).isoformat(),)).fetchone()
            if recent:return None
            row=db.execute('''SELECT t.* FROM tasks t LEFT JOIN task_notifications n ON n.task_id=t.id AND n.due=t.due
                WHERE t.done=0 AND t.due IS NOT NULL AND julianday(t.due)<=julianday(?)
                AND n.delivered_at IS NULL AND (n.lease_until IS NULL OR julianday(n.lease_until)<=julianday(?))
                ORDER BY julianday(t.due),t.id LIMIT 1''',(current,current)).fetchone()
            if not row:return None
            token='task:'+uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO task_notifications VALUES(?,?,?,?,NULL)',(row['id'],row['due'],token,lease))
            return {'id':token,'kind':'task','text':f'提醒您：{row["title"]}。原定时间是{stamp(row["due"])}。'}

    def acknowledge_reminder(self,token):
        with self.work.connect() as db:
            return db.execute('UPDATE task_notifications SET delivered_at=? WHERE token=? AND delivered_at IS NULL',(self.clock().isoformat(),token)).rowcount>0
