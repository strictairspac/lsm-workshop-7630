from fastapi import APIRouter,Depends
from ..config.database import get_db
from ..schemas.rename_job import RenameJobInput,LiveWriteInput,DecisionInput,ApplyInput
from ..services.auth import current_user,writer
from ..services import rename_job as svc
router=APIRouter(prefix='/renames')
@router.get('')
def get(db=Depends(get_db),user=Depends(current_user)):return svc.listing(db)
@router.post('',status_code=201)
def add(data:RenameJobInput,db=Depends(get_db),user=Depends(writer)):return svc.create(db,data)
@router.get('/{ident}')
def one(ident:int,db=Depends(get_db),user=Depends(current_user)):return svc.detail(db,ident)
@router.post('/{ident}/live-write',status_code=200)
def write_live(ident:int,data:LiveWriteInput,db=Depends(get_db),user=Depends(writer)):return svc.live_write(db,ident,data)
@router.post('/{ident}/decisions',status_code=200)
def decide(ident:int,data:DecisionInput,db=Depends(get_db),user=Depends(writer)):return svc.decide(db,ident,data)
@router.post('/{ident}/switch',status_code=202)
def request_switch(ident:int,data:ApplyInput,db=Depends(get_db),user=Depends(writer)):return svc.request_switch(db,ident,data.attempt_id)
@router.post('/{ident}/switch/run')
def run_switch(ident:int,data:ApplyInput,db=Depends(get_db),user=Depends(writer)):return svc.apply_switch(db,ident,data.attempt_id)
@router.get('/{ident}/keys/{name}')
def read(ident:int,name:str,db=Depends(get_db),user=Depends(current_user)):return svc.read_key(db,ident,name)
