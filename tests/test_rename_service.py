"""改名演练后端确定性测试：SQLite 内存库 + 假 Redis，不依赖 Docker/MySQL。
运行：python3 -I tests/test_rename_service.py
"""
import sys,os
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'.pylibs'))
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'backend'))
os.environ.setdefault('DATABASE_URL','sqlite:///:memory:')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('REDIS_URL','redis://unused:6379/0')
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

class FakeRedis:
    def __init__(self):self.items=[]
    def rpush(self,k,v):self.items.append((k,str(v)))
    def blpop(self,k,timeout=1):return self.items.pop(0) if self.items else None
    def ping(self):return True

from src.config import database as dbmod
engine=create_engine('sqlite:///:memory:')
Session=sessionmaker(engine,expire_on_commit=False)
dbmod.engine=engine;dbmod.Session=Session
from src.models.rename_job import RenameJob,RenameKey,RenameDecision,RenameAlias
from src.schemas.rename_job import RenameJobInput,RenameRule,BatchEntry,LiveWriteInput,DecisionInput
from src.services import rename_job as svc
svc.queue=FakeRedis()
from src.config.database import Base
import src.models.user,src.models.dataset,src.models.config,src.models.trial,src.models.audit  # noqa
Base.metadata.create_all(engine)

PASS=0;FAIL=0
def check(name,cond,extra=''):
    global PASS,FAIL
    if cond:PASS+=1;print(f'  ok  {name}')
    else:FAIL+=1;print(f'FAIL  {name} {extra}')
def E(k,v):return BatchEntry(op='put',key=k,value=v)
def group(job,ref):return next(g for g in job['groups'] if g['group_ref']==ref)

with Session() as db:
    print('1) 选批次创建演练：多活键撞名 + 单键改名 + 新名自身活键并入')
    j=svc.create(db,RenameJobInput(name='rename-demo',rules=[
        RenameRule(old_key='a',new_key='temperature'),RenameRule(old_key='b',new_key='temperature'),
        RenameRule(old_key='c',new_key='temperature'),RenameRule(old_key='h1',new_key='humidity'),
        RenameRule(old_key='w',new_key='wind')],
        entries=[E('a','21.5'),E('b','22.1'),E('c','19.8'),E('h1','54'),E('w','3.4'),E('temperature','99.9')]))
    jid=j['id'];ver=j['state_version']
    check('初始即有撞名组',group(j,'temperature')['status']=='collision' and group(j,'temperature')['live_count']==4,group(j,'temperature'))
    check('单键组无需裁决',group(j,'wind')['status']=='single' and group(j,'humidity')['status']=='single')
    check('尚未就绪',j['ready'] is False and j['pending_groups']==['temperature'])

    print('2) 乐观并发：旧版本号裁决被拒（当前 v=%d）'%ver)
    try:
        svc.decide(db,jid,DecisionInput(group_ref='temperature',winner_key='a',expected_version=1));check('陈旧版本被拒',False)
    except HTTPException as e:check('陈旧版本返回409',e.status_code==409)

    print('3) 按当前版本裁决，选择 b 保留')
    svc.decide(db,jid,DecisionInput(group_ref='temperature',winner_key='b',expected_version=ver))
    check('裁决后就绪',svc.detail(db,jid)['ready'] is True)
    check('组状态 resolved',group(svc.detail(db,jid),'temperature')['status']=='resolved')

    print('4) 组内并发改值：先前裁决失效')
    out=svc.live_write(db,jid,LiveWriteInput(op='put',key='b',value='22.9'))
    check('写入返回失效组',out['invalidated_groups']==['temperature'],out['invalidated_groups'])
    j=svc.detail(db,jid)
    check('改值后组回到 stale',group(j,'temperature')['status']=='stale')
    try:
        svc.request_switch(db,jid,'attempt-0001');check('有失效裁决时禁止切换',False)
    except HTTPException as e:check('切换前置校验409且未入队',e.status_code==409 and svc.queue.items==[])

    print('5) 重新裁决后删键再次失效；墓碑赢家被拒；第三次裁决成功')
    ver=svc.detail(db,jid)['state_version']
    svc.decide(db,jid,DecisionInput(group_ref='temperature',winner_key='b',expected_version=ver))
    svc.live_write(db,jid,LiveWriteInput(op='delete',key='b'))
    check('删键后裁决失效',group(svc.detail(db,jid),'temperature')['status']=='stale')
    ver=svc.detail(db,jid)['state_version']
    try:
        svc.decide(db,jid,DecisionInput(group_ref='temperature',winner_key='b',expected_version=ver));check('选墓碑键被拒',False)
    except HTTPException as e:check('墓碑赢家422',e.status_code==422)
    svc.decide(db,jid,DecisionInput(group_ref='temperature',winner_key='a',expected_version=ver))
    check('重新裁决后就绪',svc.detail(db,jid)['ready'] is True)

    print('6) 原子切换：新名取值、旧名立碑、别名一次性生效')
    before=len(svc.queue.items)
    svc.request_switch(db,jid,'attempt-0002')
    check('切换任务入队',len(svc.queue.items)==before+1)
    _,payload=svc.queue.items[-1];ident,_,attempt=payload.partition(':')
    from src.worker import run_switch
    run_switch(int(ident),attempt)
    j=svc.detail(db,jid)
    check('状态 switched',j['status']=='switched',j['status'])
    r_new=svc.read_key(db,jid,'temperature');r_old=svc.read_key(db,jid,'a')
    check('新名读到裁决赢家值 21.5',r_new['found'] and r_new['value']=='21.5',r_new)
    check('旧名经别名读到同一个值',r_old['found'] and r_old['value']=='21.5' and r_old['is_alias'] and r_old['canonical_name']=='temperature',r_old)
    r_h=svc.read_key(db,jid,'h1')
    check('单键改名旧名也指向新名同值',r_h['value']=='54' and r_h['canonical_name']=='humidity')
    r_w=svc.read_key(db,jid,'wind')
    check('新名直读无别名跳数',r_w['value']=='3.4' and r_w['alias_hops']==0)
    aliases={a.alias_name for a in db.scalars(select(RenameAlias).where(RenameAlias.job_id==jid))}
    check('全部旧名都建立别名',aliases=={'a','b','c','h1','w'},aliases)
    rows={k.key_name:k.is_tombstone for k in db.scalars(select(RenameKey).where(RenameKey.job_id==jid))}
    check('旧名全部立碑',all(rows[k] for k in ('a','b','c','h1','w')),rows)

    print('7) 同次重试只启用一版：重复执行幂等')
    out1=svc.apply_switch(db,jid,'attempt-0002')
    out2=svc.apply_switch(db,jid,'attempt-0002-RETRY')
    check('重复切换幂等返回 switched',out1['idempotent'] and out2['idempotent'])
    check('值没有被二次改写',svc.read_key(db,jid,'temperature')['value']=='21.5')

    print('8) 切换后封板：拒绝并发写入与改判')
    for call,arg in [('w',LiveWriteInput(op='put',key='a',value='x')),('d',DecisionInput(group_ref='temperature',winner_key='a',expected_version=99))]:
        try:
            (svc.live_write if call=='w' else svc.decide)(db,jid,arg);check('封板操作被拒',False)
        except HTTPException as e:check('封板返回409',e.status_code==409)

with Session() as db:
    print('9) 中断恢复：switching 遗留在崩溃点，worker 恢复后完成')
    j2=svc.create(db,RenameJobInput(name='recover-demo',rules=[RenameRule(old_key='p',new_key='pressure'),RenameRule(old_key='q',new_key='pressure')],entries=[E('p','1013'),E('q','1011')]))
    ver=j2['state_version']
    svc.decide(db,j2['id'],DecisionInput(group_ref='pressure',winner_key='p',expected_version=ver))
    job=db.get(RenameJob,j2['id']);job.status='switching';job.last_event={'attempt_id':'attempt-crash'};db.commit()
    from src.worker import recover
    recover()
    db.expire_all()  # 真实 API 每请求独立会话；这里手动模拟新请求的新鲜装载
    j2=svc.detail(db,j2['id'])
    check('恢复后 switched',j2['status']=='switched',j2['status'])
    check('新旧名同值 1013',svc.read_key(db,j2['id'],'q')['value']=='1013' and svc.read_key(db,j2['id'],'pressure')['value']=='1013')

with Session() as db:
    print('10) 全墓碑组：切换后新名不可读，旧名别名解析到墓碑规范名')
    j3=svc.create(db,RenameJobInput(name='tomb-demo',rules=[RenameRule(old_key='z1',new_key='znew'),RenameRule(old_key='z2',new_key='znew')]))
    svc.request_switch(db,j3['id'],'attempt-tomb-001')
    _,payload=svc.queue.items[-1];ident,_,attempt=payload.partition(':')
    from src.worker import run_switch
    run_switch(int(ident),attempt)
    r=svc.read_key(db,j3['id'],'z1')
    check('全墓碑组新名读不到值',r['found'] is False and r['canonical_name']=='znew',r)

with Session() as db:
    print('11) 规则校验：重复旧名/同名/链路改名拒绝')
    for bad in ([('a','x'),('a','y')],[('a','a')],[('a','b'),('b','c')]):
        try:
            svc.create(db,RenameJobInput(name='bad-'+str(abs(hash(str(bad)))),rules=[RenameRule(old_key=o,new_key=n) for o,n in bad]));check('非法规则被拒',False)
        except HTTPException as e:check('非法规则422',e.status_code==422)

with Session() as db:
    print('12) 未裁决撞名组禁止入队；未裁决全墓碑/单键组可直接切换')
    j4=svc.create(db,RenameJobInput(name='blocked-demo',rules=[RenameRule(old_key='m1',new_key='metric'),RenameRule(old_key='m2',new_key='metric')],entries=[E('m1','1'),E('m2','2')]))
    qlen=len(svc.queue.items)
    try:
        svc.request_switch(db,j4['id'],'attempt-blocked');check('未裁决入队被拒',False)
    except HTTPException as e:check('未裁决409且未入队',e.status_code==409 and len(svc.queue.items)==qlen)

with Session() as db:
    print('13) failed 状态可修正后重新驱动，切换成功')
    j5=svc.create(db,RenameJobInput(name='retry-demo',rules=[RenameRule(old_key='s1',new_key='signal'),RenameRule(old_key='s2',new_key='signal')],entries=[E('s1','on'),E('s2','off')]))
    ver=j5['state_version']
    svc.decide(db,j5['id'],DecisionInput(group_ref='signal',winner_key='s1',expected_version=ver))
    job=db.get(RenameJob,j5['id']);job.status='failed';job.last_event={'event':'switch_failed','attempt_id':'attempt-bad'};db.commit()
    # failed 后仍可写入修正与重新裁决
    out=svc.live_write(db,j5['id'],LiveWriteInput(op='put',key='s2',value='off-2'))
    check('failed 后允许写入修正',out['state_version']>ver)
    svc.decide(db,j5['id'],DecisionInput(group_ref='signal',winner_key='s2',expected_version=out['state_version']))
    svc.request_switch(db,j5['id'],'attempt-retry-001')
    _,payload=svc.queue.items[-1];ident,_,attempt=payload.partition(':')
    from src.worker import run_switch
    run_switch(int(ident),attempt)
    db.expire_all()
    r=svc.read_key(db,j5['id'],'s1')
    check('重驱动后新名取新赢家值 off-2',r['value']=='off-2' and r['canonical_name']=='signal',r)
    check('重驱动后状态 switched',svc.detail(db,j5['id'])['status']=='switched')

print(f'\n{PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)
