import time,json,traceback
from pathlib import Path
from sqlalchemy import select
from .config.database import Session
from .models.trial import Trial
from .models.rename_job import RenameJob
from .services.trial import queue
from .services import rename_job as rsvc
from .services.lsm_engine import simulate
heartbeat=Path('/artifacts/worker.heartbeat')
def run_one(ident):
    with Session() as db:
        x=db.get(Trial,ident)
        if not x or x.result is not None:return
        x.status='running';db.commit()
        try:
            x.result=simulate(x.input_snapshot['entries'],x.input_snapshot['config']);x.status='passed'
        except Exception as exc:x.result={'error':str(exc)[:500]};x.status='failed'
        db.commit();Path(f'/artifacts/lsm-trial-{ident}.json').write_text(json.dumps(x.result,ensure_ascii=False,indent=2))
def run_switch(ident,attempt_id):
    # 同次重试只启用一版：apply_switch 对 switched 幂等；崩溃遗留在 switching 的演练在这里恢复
    with Session() as db:
        job=db.get(RenameJob,ident)
        if not job or job.status=='switched':return
        try:rsvc.apply_switch(db,ident,attempt_id)
        except Exception:
            traceback.print_exc()
            fresh=db.get(RenameJob,ident)
            if fresh and fresh.status=='switching':
                fresh.status='failed';fresh.last_event={**(fresh.last_event or {}),'event':'switch_failed','attempt_id':attempt_id,'error':'切换失败，修正后可重新发起'};db.commit()
def recover():
    with Session() as db:
        for ident in list(db.scalars(select(Trial.id).where(Trial.status.in_(['queued','running'])))):run_one(ident)
        # 崩溃恢复：事务未提交的 switching 演练按原 attempt 重放
        for j in list(db.scalars(select(RenameJob).where(RenameJob.status=='switching'))):
            attempt=(j.last_event or {}).get('attempt_id','recovery')
            run_switch(j.id,attempt)
def main():
    while True:
        try:
            heartbeat.write_text(str(time.time()))
            item=queue.blpop('lsm:jobs',timeout=1)
            if item:run_one(int(item[1]))
            sitem=rsvc.queue.blpop(rsvc.SWITCH_QUEUE,timeout=1)
            if sitem:
                ident,_,attempt_id=sitem[1].decode().partition(':')
                run_switch(int(ident),attempt_id)
            if not item and not sitem:recover()
        except Exception:traceback.print_exc();time.sleep(2)
if __name__=='__main__':main()
