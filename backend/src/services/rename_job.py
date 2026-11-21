"""遥测键统一改名演练：分组裁决、乐观并发、原子别名切换。

确定性教学模型：所有状态保存在 MySQL，Redis 仅做后台切换分发。
新名与旧名别名在同一个数据库事务内一次性切换，切换后两种叫法读到同一个值。
"""
from datetime import datetime
from sqlalchemy import select
from fastapi import HTTPException
from redis import Redis
from ..config.settings import REDIS_URL
from ..models.rename_job import RenameJob,RenameKey,RenameDecision,RenameAlias
queue=Redis.from_url(REDIS_URL)
SWITCH_QUEUE='rename:switch-jobs'

# ---------- 纯函数：从规则与活键计算撞名组 ----------
def build_groups(rules,keys):
    """keys: {key_name: {'value','is_tombstone','version'}}；返回按新名归并的裁决组视图。"""
    by_new={}
    for r in rules:
        by_new.setdefault(r['new_key'],[]).append(r['old_key'])
    live_names={n for n,k in keys.items() if not k['is_tombstone']}
    groups=[]
    for new_key in sorted(by_new):
        old_keys=by_new[new_key]
        members=[]
        seen=set()
        for ok in old_keys:
            if ok in seen:continue
            seen.add(ok)
            k=keys.get(ok)
            members.append({'key':ok,'value':None if not k else k['value'],'state':'missing' if not k else ('tombstone' if k['is_tombstone'] else 'live'),'version':0 if not k else k['version']})
        # 目标新名本身已作为未参与改名的活键存在，也算撞名一方
        if new_key not in seen and new_key in live_names:
            k=keys[new_key]
            members.append({'key':new_key,'value':k['value'],'state':'live','version':k['version']})
        live_members=[m for m in members if m['state']=='live']
        groups.append({'group_ref':new_key,'new_key':new_key,'old_keys':old_keys,'members':members,'live_count':len(live_members),'needs_choice':len(live_members)>1})
    return groups

def _key_map(db,job_id):
    return {k.key_name:{'value':k.value,'is_tombstone':k.is_tombstone,'version':k.version}
            for k in db.scalars(select(RenameKey).where(RenameKey.job_id==job_id))}

def _decisions(db,job_id):
    return {d.group_ref:d for d in db.scalars(select(RenameDecision).where(RenameDecision.job_id==job_id))}

def revalidate(db,job):
    """按当前活键重判每条裁决：组内改值或删键后，先前选择失效。返回失效的组列表。"""
    keys=_key_map(db,job.id);groups={g['group_ref']:g for g in build_groups(job.rules,keys)}
    invalidated=[]
    for d in _decisions(db,job.id).values():
        if d.state!='active':continue
        g=groups.get(d.group_ref)
        winner=next((m for m in g['members'] if m['key']==d.winner_key),None) if g else None
        ok=(g is not None and winner is not None and winner['state']=='live'
            and winner['value']==d.winner_value and d.based_on_version<=job.state_version)
        if not ok:
            d.state='invalidated';invalidated.append(d.group_ref)
    return invalidated

def group_views(db,job):
    keys=_key_map(db,job.id);decisions=_decisions(db,job.id)
    views=[]
    for g in build_groups(job.rules,keys):
        d=decisions.get(g['group_ref'])
        if g['needs_choice']:
            if not d or d.state=='invalidated':status='stale' if d else 'collision'
            else:status='resolved'
        else:status='single'
        views.append({**g,'status':status,'decision':None if not d else {'winner_key':d.winner_key,'winner_value':d.winner_value,'based_on_version':d.based_on_version,'state':d.state}})
    return views

# ---------- 序列化 ----------
def serialize(db,job):
    groups=group_views(db,job)
    pending=[g['group_ref'] for g in groups if g['status']=='collision' or g['status']=='stale']
    keys=_key_map(db,job.id)
    return {'id':job.id,'name':job.name,'source_note':job.source_note,'status':job.status,
            'rules':job.rules,'state_version':job.state_version,'last_event':job.last_event,
            'switched_at':None if not job.switched_at else str(job.switched_at),'created_at':str(job.created_at),
            'groups':groups,'pending_groups':pending,'ready':job.status in ('draft','failed') and not pending,
            'key_count':len(keys),'live_key_count':sum(1 for k in keys.values() if not k['is_tombstone'])}

def listing(db):
    return [serialize(db,x) for x in db.scalars(select(RenameJob).order_by(RenameJob.id.desc()))]

def get_job(db,ident):
    job=db.get(RenameJob,ident)
    if not job:raise HTTPException(404,'改名演练不存在')
    return job

def detail(db,ident):return serialize(db,get_job(db,ident))

# ---------- 创建演练 ----------
def create(db,data):
    if db.scalar(select(RenameJob).where(RenameJob.name==data.name)):raise HTTPException(409,'改名演练名称已存在')
    rules=[r.model_dump() for r in data.rules]
    olds=[r['old_key'] for r in rules];news={r['new_key'] for r in rules}
    if len(olds)!=len(set(olds)):raise HTTPException(422,'同一个旧名不能出现在多条规则中')
    if any(r['old_key']==r['new_key'] for r in rules):raise HTTPException(422,'旧名与新名不能相同')
    # 链路改名（A→B，B→C）在一次性切换模型中无法表达，禁止
    if set(olds)&news:raise HTTPException(422,'不支持链路改名：某条规则的新名不能同时是另一条规则的旧名')
    job=RenameJob(name=data.name,source_note=data.source_note,rules=rules,state_version=1)
    db.add(job);db.flush()
    for name in sorted(set(olds)|news):
        db.add(RenameKey(job_id=job.id,key_name=name,value=None,is_tombstone=True,version=1))
    db.flush()
    # 初始演练批次：与并发写入同一条版本化路径，每个操作推高一版
    for e in data.entries:
        row=db.scalar(select(RenameKey).where(RenameKey.job_id==job.id,RenameKey.key_name==e.key))
        if row is None:
            row=RenameKey(job_id=job.id,key_name=e.key,version=1);db.add(row);db.flush()
        if e.op=='put':row.value=e.value;row.is_tombstone=False
        else:row.value=None;row.is_tombstone=True
        row.version+=1;job.state_version+=1
    db.commit();db.refresh(job)
    return serialize(db,job)

# ---------- 裁决期间的并发站点写入 ----------
def live_write(db,ident,data):
    job=get_job(db,ident)
    if job.status in ('switching','switched'):raise HTTPException(409,'演练已切换封板，不能再注入并发写入')
    row=db.scalar(select(RenameKey).where(RenameKey.job_id==ident,RenameKey.key_name==data.key))
    if not row:
        if data.op=='delete':raise HTTPException(404,'演练批次中不存在该键，无法删除')
        row=RenameKey(job_id=ident,key_name=data.key,version=0);db.add(row);db.flush()
    if data.op=='put':
        if data.value is None:raise HTTPException(422,'put 操作必须包含字符串 value')
        row.value=data.value;row.is_tombstone=False
    else:
        row.value=None;row.is_tombstone=True
    row.version+=1;job.state_version+=1
    invalidated=revalidate(db,job)
    job.last_event={'op':data.op,'key':data.key,'value':data.value,'version':job.state_version,'invalidated_groups':invalidated,'at':datetime.now().isoformat(timespec='seconds')}
    db.commit()
    return {'state_version':job.state_version,'invalidated_groups':invalidated,'job':serialize(db,job)}

# ---------- 值班员裁决（乐观并发） ----------
def decide(db,ident,data):
    job=get_job(db,ident)
    if job.status in ('switching','switched'):raise HTTPException(409,'演练已切换封板，裁决已锁定')
    if data.expected_version!=job.state_version:
        raise HTTPException(409,f'状态已变化（当前版本 {job.state_version}），请刷新后按最新值重新裁决')
    g=next((x for x in group_views(db,job) if x['group_ref']==data.group_ref),None)
    if not g:raise HTTPException(404,'撞名组不存在')
    if not g['needs_choice']:raise HTTPException(422,'该组没有多个活键，不需要人工裁决')
    winner=next((m for m in g['members'] if m['key']==data.winner_key),None)
    if not winner or winner['state']!='live':raise HTTPException(422,'选择的来源键已不是活键（被删除或不存在），请改选其他活键')
    d=db.scalar(select(RenameDecision).where(RenameDecision.job_id==ident,RenameDecision.group_ref==data.group_ref))
    if d:
        d.winner_key=winner['key'];d.winner_value=winner['value'];d.based_on_version=job.state_version;d.state='active'
    else:
        db.add(RenameDecision(job_id=ident,group_ref=data.group_ref,winner_key=winner['key'],winner_value=winner['value'],based_on_version=job.state_version,state='active'))
    db.commit()
    return serialize(db,job)

# ---------- 一次性原子切换（可由 API 同步执行，也可由 worker 重试） ----------
def apply_switch(db,job_id,attempt_id):
    job=db.get(RenameJob,job_id)
    if not job:raise HTTPException(404,'改名演练不存在')
    # 同次重试只启用一版：attempt_id 记录在 last_event，已完成的切换直接幂等返回
    if job.status=='switched':
        return {'id':job.id,'status':'switched','idempotent':True,'attempt_id':attempt_id}
    # failed 允许在修正冲突后用新 attempt 重新驱动；draft/failed/switching 都进入同一套校验
    invalidated=revalidate(db,job)
    if invalidated:raise HTTPException(409,'存在失效裁决，请重新选择：'+','.join(invalidated))
    pending=[g['group_ref'] for g in group_views(db,job) if g['status'] in ('collision','stale')]
    if pending:raise HTTPException(409,'还有撞名组未裁决：'+','.join(pending))
    job.status='switching'
    groups={g['group_ref']:g for g in group_views(db,job)}
    decisions=_decisions(db,job.id)
    keys={k.key_name:k for k in db.scalars(select(RenameKey).where(RenameKey.job_id==job.id))}
    rules=job.rules
    # 1) 计算每个新名的最终值：多活键取裁决赢家；单活键取唯一活值；全墓碑则新名也是墓碑
    final={}
    for new_key,g in groups.items():
        live=[m for m in g['members'] if m['state']=='live']
        if not live:final[new_key]={'value':None,'tombstone':True}
        elif len(live)==1:final[new_key]={'value':live[0]['value'],'tombstone':False}
        else:
            d=decisions[new_key];final[new_key]={'value':d.winner_value,'tombstone':False}
    # 2) 同一事务内写新名规范行、旧名立碑、别名指向新名 —— 一次性切换
    for r in rules:
        old=keys[r['old_key']]
        old.value=None;old.is_tombstone=True;old.version+=1
        db.add(RenameAlias(job_id=job.id,alias_name=r['old_key'],canonical_name=r['new_key']))
    for new_key,outcome in final.items():
        canonical=keys.get(new_key)
        if canonical is None:
            canonical=RenameKey(job_id=job.id,key_name=new_key);db.add(canonical);db.flush()
        canonical.value=outcome['value'];canonical.is_tombstone=outcome['tombstone'];canonical.version+=1
    job.status='switched';job.state_version+=1;job.switched_at=datetime.now()
    job.last_event={'event':'switch','attempt_id':attempt_id,'version':job.state_version,'at':job.switched_at.isoformat(timespec='seconds')}
    db.commit()
    return {'id':job.id,'status':'switched','idempotent':False,'attempt_id':attempt_id}

def request_switch(db,ident,attempt_id):
    job=get_job(db,ident)
    if job.status=='switched':return serialize(db,job)
    if job.status=='switching':raise HTTPException(409,'切换正在执行，请勿重复发起；worker 会按 attempt 恢复')
    # failed 状态在通过下面的重新校验后，可用新 attempt 重新入队驱动
    if len(list(db.scalars(select(RenameJob).where(RenameJob.status=='switching'))))>=20:
        raise HTTPException(429,'待切换改名演练上限20个')
    # 入队前前置校验：失效裁决与未裁决撞名组直接挡回，避免无效任务进队列
    invalidated=revalidate(db,job)
    if invalidated:raise HTTPException(409,'存在失效裁决，请重新选择：'+','.join(invalidated))
    pending=[g['group_ref'] for g in group_views(db,job) if g['status'] in ('collision','stale')]
    if pending:raise HTTPException(409,'还有撞名组未裁决：'+','.join(pending))
    db.commit()
    queue.rpush(SWITCH_QUEUE,f'{ident}:{attempt_id}')
    return serialize(db,job)

# ---------- 新旧键查值：两种叫法读到同一个值 ----------
def resolve_name(db,job_id,name):
    seen=set();cur=name;hops=0
    while cur not in seen and hops<8:
        seen.add(cur)
        a=db.scalar(select(RenameAlias).where(RenameAlias.job_id==job_id,RenameAlias.alias_name==cur))
        if not a:break
        cur=a.canonical_name;hops+=1
    return cur,hops

def read_key(db,ident,name):
    job=get_job(db,ident)
    canonical,hops=resolve_name(db,ident,name)
    row=db.scalar(select(RenameKey).where(RenameKey.job_id==ident,RenameKey.key_name==canonical))
    found=row is not None and not row.is_tombstone
    return {'query':name,'canonical_name':canonical,'alias_hops':hops,'found':found,
            'value':None if not found else row.value,'is_alias':canonical!=name,
            'job_status':job.status,'version':0 if row is None else row.version}
