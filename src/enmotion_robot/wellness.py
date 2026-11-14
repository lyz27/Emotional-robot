"""Local, presence-aware reminders. Presence is not a sitting/posture diagnosis."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
import uuid

DEFAULTS={'enabled':True,'welcome':True,'water_minutes':45,'break_minutes':60}
MESSAGES={'welcome':'欢迎来到工位，小蓝已就绪。','water':'已经工作一段时间了，记得喝点水。',
          'break':'您已连续在工位前较长时间，方便起身活动一下吗？'}


class WellnessRules:
    def __init__(self,path:Path,clock=time.monotonic,wall=time.time):
        self.path,self.clock,self.wall=path,clock,wall
        self.lock=threading.RLock()
        self.config=dict(DEFAULTS)
        try:self.config.update(json.loads(path.read_text()))
        except (OSError,ValueError):pass
        self.last_tick=clock();self.last_face=-1000.;self.visible=False
        self.present_since=None;self.absent_since=None
        self.water_seconds=0.;self.work_seconds=0.;self.last_welcome=-1000.
        self.pending=[];self.history=[];self.return_allowed=True

    def configure(self,payload):
        if not isinstance(payload,dict) or set(payload)-set(DEFAULTS):raise ValueError('未知提醒设置')
        for k,v in payload.items():
            if k in ('enabled','welcome'):
                if type(v)!=bool:raise ValueError('开关需要布尔值')
            elif type(v)!=int or not 5<=v<=240:raise ValueError('提醒间隔需为5–240分钟')
        with self.lock:
            self.config.update(payload)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.config));os.chmod(temp,0o600)
            os.replace(temp,self.path)
            if not self.config['enabled']:self.pending=[]
            if not self.config['welcome']:self.pending=[j for j in self.pending if j['kind']!='welcome']
            return self.snapshot()

    def enqueue(self,kind,now):
        if any(j['kind']==kind for j in self.pending):return
        self.pending.append({'id':uuid.uuid4().hex,'kind':kind,'text':MESSAGES[kind],
                             'created':now,'expires':now+(180 if kind=='welcome' else 900),'lease':0})

    def update(self,vision):
        with self.lock:
            now=self.clock();dt=max(0,min(30,now-self.last_tick));self.last_tick=now
            online=vision.get('online') and 0<=self.wall()-vision.get('updated_at',0)<3
            if online and vision.get('present') and any(d['kind']=='face' for d in vision.get('detections',[])):
                self.last_face=now
            visible=bool(online and vision.get('present') and now-self.last_face<15)
            self.pending=[j for j in self.pending if j['expires']>now]
            if not self.config['enabled']:
                self.visible=visible;return
            if visible:
                if self.present_since is None:self.present_since=now
                if self.visible:
                    self.water_seconds+=dt;self.work_seconds+=dt
                self.absent_since=None
                if self.config['welcome'] and self.return_allowed and now-self.present_since>=2 and now-self.last_welcome>=600:
                    self.enqueue('welcome',now);self.return_allowed=False
                if self.water_seconds>=self.config['water_minutes']*60:self.enqueue('water',now)
                if self.work_seconds>=self.config['break_minutes']*60:self.enqueue('break',now)
            else:
                self.present_since=None
                if self.absent_since is None:self.absent_since=now
                if now-self.absent_since>=120:
                    self.water_seconds=0;self.work_seconds=0;self.return_allowed=True
                    self.pending=[]
            if not visible:
                self.pending=[j for j in self.pending if j['kind']!='welcome']
            self.visible=visible

    def claim(self,voice_free):
        with self.lock:
            now=self.clock()
            if not voice_free or not self.visible or not self.config['enabled']:return None
            for job in self.pending:
                if job['lease']<=now:
                    job['lease']=now+20
                    return {k:job[k] for k in ('id','kind','text')}
            return None

    def acknowledge(self,job_id):
        with self.lock:
            now=self.clock()
            for job in self.pending:
                if job['id']==job_id:
                    if job['kind']=='water':self.water_seconds=0
                    if job['kind']=='break':self.work_seconds=0
                    if job['kind']=='welcome':self.last_welcome=now
                    self.history.insert(0,{'kind':job['kind'],'text':job['text'],'at':self.wall()})
                    self.history=self.history[:20];self.pending.remove(job)
                    return True
            return False

    def snapshot(self):
        with self.lock:
            return deepcopy({'config':self.config,'present':self.visible,'water_seconds':round(self.water_seconds),
                'work_seconds':round(self.work_seconds),'pending':[{k:j[k] for k in ('id','kind','text')} for j in self.pending],
                'history':self.history,'basis':'continuous_workstation_presence_not_sitting_posture'})
