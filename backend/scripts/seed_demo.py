from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.user import User
from src.models.dataset import Dataset
from src.models.config import Config
from src.models.trial import Trial
from src.models.audit import Audit
from src.models.rename_job import RenameJob,RenameKey
from src.utils.password import hash_password
DEMO=[{'op':'put','key':'sensor-01','value':'18.2'},{'op':'put','key':'sensor-02','value':'19.1'},{'op':'put','key':'sensor-03','value':'22.4'},{'op':'put','key':'sensor-04','value':'17.0'},{'op':'put','key':'sensor-01','value':'18.8'},{'op':'delete','key':'sensor-02'},{'op':'put','key':'sensor-05','value':'21.6'},{'op':'put','key':'sensor-06','value':'20.3'},{'op':'delete','key':'sensor-03'},{'op':'put','key':'sensor-07','value':'23.0'},{'op':'put','key':'sensor-01','value':'19.0'},{'op':'put','key':'sensor-08','value':'18.9'}]
def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@beiling.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('EdgeStore!17')))
        sets=[('北岭站点-早班写入','sensor-01重复写，sensor-02与sensor-03墓碑删除；12条操作，最终6个存活键。',DEMO),('旧值复活回归集','先将旧值刷入深层，再写墓碑；最终合并必须防止旧值复活。',[{'op':'put','key':'sensor-a','value':'old'},{'op':'put','key':'sensor-b','value':'b'},{'op':'put','key':'sensor-c','value':'c'},{'op':'put','key':'sensor-d','value':'d'},{'op':'delete','key':'sensor-a'},{'op':'put','key':'sensor-e','value':'e'}]),('全部删除边界集','两个已写入键随后全部删除，最终应是空字典。',[{'op':'put','key':'x','value':'1'},{'op':'put','key':'y','value':'2'},{'op':'delete','key':'x'},{'op':'delete','key':'y'}])]
        for name,desc,entries in sets:
            if not db.scalar(select(Dataset).where(Dataset.name==name)):db.add(Dataset(name=name,description=desc,entries=entries))
        configs=[('小内存-完整合并',2,2,3,True),('保留层级观测',2,3,3,False),('宽缓冲-完整合并',4,2,4,True)]
        for name,limit,trigger,depth,final in configs:
            if not db.scalar(select(Config).where(Config.name==name)):db.add(Config(name=name,memtable_limit=limit,l0_trigger=trigger,max_levels=depth,final_compaction=final))
        rename_set=('站点遥测-统一键名演练','多个活键归并到同一新名，含撞名组与单键改名，用于值班员裁决演练。',[{'op':'put','key':'temp-north','value':'21.5'},{'op':'put','key':'temp_beiling','value':'22.1'},{'op':'put','key':'temp.old','value':'19.8'},{'op':'put','key':'hum-01','value':'54'},{'op':'put','key':'humidity_01','value':'57'},{'op':'put','key':'wind-ms','value':'3.4'},{'op':'put','key':'pressure_01','value':'1013'}])
        if not db.scalar(select(Dataset).where(Dataset.name==rename_set[0])):db.add(Dataset(name=rename_set[0],description=rename_set[1],entries=rename_set[2]))
        seed_rename_job(db)
        db.commit()
        return {name:db.query(model).count() for name,model in [('users',User),('datasets',Dataset),('configs',Config),('trials',Trial),('rename_jobs',RenameJob)]}
def seed_rename_job(db):
    name='北岭站点-遥测键统一演练'
    if db.scalar(select(RenameJob).where(RenameJob.name==name)):return
    rules=[{'old_key':'temp-north','new_key':'temperature'},{'old_key':'temp_beiling','new_key':'temperature'},{'old_key':'temp.old','new_key':'temperature'},
           {'old_key':'hum-01','new_key':'humidity'},{'old_key':'humidity_01','new_key':'humidity'},
           {'old_key':'wind-ms','new_key':'wind-speed'},{'old_key':'pressure_01','new_key':'pressure'}]
    entries=[{'op':'put','key':k,'value':v} for k,v in {'temp-north':'21.5','temp_beiling':'22.1','temp.old':'19.8','hum-01':'54','humidity_01':'57','wind-ms':'3.4','pressure_01':'1013'}.items()]
    job=RenameJob(name=name,source_note='站点遥测-统一键名演练',rules=rules,state_version=1)
    db.add(job);db.flush()
    for key in sorted({r['old_key'] for r in rules}|{r['new_key'] for r in rules}):
        db.add(RenameKey(job_id=job.id,key_name=key,value=None,is_tombstone=True,version=1))
    db.flush()
    for e in entries:
        row=db.scalar(select(RenameKey).where(RenameKey.job_id==job.id,RenameKey.key_name==e['key']))
        row.value=e['value'];row.is_tombstone=False;row.version+=1;job.state_version+=1
if __name__=='__main__':print(seed())
