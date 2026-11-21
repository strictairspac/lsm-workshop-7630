"""改名演练 HTTP 层测试：FastAPI TestClient + SQLite + 假 Redis，验证路由/鉴权/角色权限。
运行：python3 -I tests/test_rename_api.py
"""
import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'.pylibs'))
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'backend'))
os.environ.setdefault('DATABASE_URL','sqlite:///:memory:')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('REDIS_URL','redis://unused:6379/0')
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

class FakeRedis:
    def __init__(self):self.items=[]
    def rpush(self,k,v):self.items.append((k,str(v)))
    def blpop(self,k,timeout=1):return self.items.pop(0) if self.items else None
    def ping(self):return True

from src.config import database as dbmod
engine=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
Session=sessionmaker(engine,expire_on_commit=False)
dbmod.engine=engine;dbmod.Session=Session
from src.config.database import Base
import src.models.user,src.models.dataset,src.models.config,src.models.trial,src.models.audit,src.models.rename_job  # noqa
Base.metadata.create_all(engine)
from src.services import rename_job as rsvc
from src.services import trial as tsvc
rsvc.queue=FakeRedis();tsvc.queue=rsvc.queue
from src.models.user import User
from src.utils.password import hash_password
with Session() as db:
    for role in ('admin','operator','viewer'):
        db.add(User(email=role+'@beiling.test',role=role,password_hash=hash_password('pw-123456')))
    db.commit()

from src.main import app
from src.config.database import get_db
app.dependency_overrides[get_db]=lambda: (s for s in [Session()]).__next__()
client=TestClient(app)
PASS=0;FAIL=0
def check(name,cond,extra=''):
    global PASS,FAIL
    if cond:PASS+=1;print(f'  ok  {name}')
    else:FAIL+=1;print(f'FAIL  {name} {extra}')
def login(role):
    r=client.post('/auth/login',json={'email':role+'@beiling.test','password':'pw-123456'})
    return {'Authorization':'Bearer '+r.json()['token']}
H_ADMIN=login('admin');H_OP=login('operator');H_VIEW=login('viewer')

print('A) 健康检查与鉴权')
check('健康检查 200',client.get('/health').status_code==200)
check('无令牌 403/401',client.get('/renames').status_code in (401,403))
check('viewer 可列演练',client.get('/renames',headers=H_VIEW).status_code==200)

print('B) 操作员配置改名规则、选批次创建')
body={'name':'http-rename-1','rules':[{'old_key':'k1','new_key':'canonical'},{'old_key':'k2','new_key':'canonical'}],
      'entries':[{'op':'put','key':'k1','value':'v1'},{'op':'put','key':'k2','value':'v2'}]}
r=client.post('/renames',json=body,headers=H_OP)
check('创建 201',r.status_code==201,r.text)
jid=r.json()['id']
check('撞名组待裁决',r.json()['pending_groups']==['canonical'])
check('viewer 创建被 403',client.post('/renames',json=body,headers=H_VIEW).status_code==403)

print('C) 并发写入触发失效，乐观版本号裁决')
ver=r.json()['state_version']
stale=client.post(f'/renames/{jid}/decisions',json={'group_ref':'canonical','winner_key':'k1','expected_version':1},headers=H_OP)
check('陈旧版本 409',stale.status_code==409,stale.text)
d=client.post(f'/renames/{jid}/decisions',json={'group_ref':'canonical','winner_key':'k1','expected_version':ver},headers=H_OP)
check('裁决 200 且就绪',d.status_code==200 and d.json()['ready'] is True,d.text)
w=client.post(f'/renames/{jid}/live-write',json={'op':'put','key':'k1','value':'v1-new'},headers=H_OP)
check('并发写入后裁决失效',w.json()['invalidated_groups']==['canonical'],w.text)
ver=w.json()['state_version']
check('viewer 写入被 403',client.post(f'/renames/{jid}/live-write',json={'op':'delete','key':'k1'},headers=H_VIEW).status_code==403)
d2=client.post(f'/renames/{jid}/decisions',json={'group_ref':'canonical','winner_key':'k1','expected_version':ver},headers=H_OP)
check('重新裁决就绪',d2.status_code==200 and d2.json()['ready'] is True)

print('D) 发起切换并由 worker 执行；新旧键读到同一个值')
r=client.post(f'/renames/{jid}/switch',json={'attempt_id':'attempt-http-001'},headers=H_OP)
check('切换入队 202',r.status_code==202,r.text)
_,payload=rsvc.queue.items[-1];ident,_,attempt=payload.partition(':')
from src.worker import run_switch
run_switch(int(ident),attempt)
r_new=client.get(f'/renames/{jid}/keys/canonical',headers=H_VIEW)
r_old=client.get(f'/renames/{jid}/keys/k1',headers=H_VIEW)
check('新名读到最新值',r_new.json()['value']=='v1-new',r_new.text)
check('旧名别名读到同一个值',r_old.json()['value']=='v1-new' and r_old.json()['is_alias'] is True,r_old.text)
again=client.post(f'/renames/{jid}/switch',json={'attempt_id':'attempt-http-002'},headers=H_OP)
check('已切换再次发起幂等返回已切换态',again.status_code==202 and again.json()['status']=='switched')
sealed=client.post(f'/renames/{jid}/live-write',json={'op':'put','key':'k1','value':'x'},headers=H_OP)
check('切换后封板 409',sealed.status_code==409)

print('E) 校验错误码：404/422/409')
check('不存在演练 404',client.get('/renames/9999',headers=H_OP).status_code==404)
bad=client.post('/renames',json={'name':'x','rules':[{'old_key':'a','new_key':'a'}]},headers=H_OP)
check('同名规则 422',bad.status_code==422,bad.text)
dup=client.post('/renames',json={'name':'http-rename-1','rules':[{'old_key':'a','new_key':'b'}]},headers=H_OP)
check('重名 409',dup.status_code==409)

print('F) 审计中间件记录了写操作')
audit=client.get('/audit',headers=H_ADMIN).json()
check('审计含改名切换动作',any('/switch' in x['action'] for x in audit),audit[:2])
check('viewer 不能看审计 403',client.get('/audit',headers=H_VIEW).status_code==403)

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
