from datetime import datetime
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,JSON,DateTime,ForeignKey,func,UniqueConstraint
from ..config.database import Base
from ..constants.enums import RENAME_JOB_STATUSES
class RenameJob(Base):
    __tablename__='rename_jobs'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    source_note:Mapped[str]=mapped_column(String(200),default='')  # 演练来源批次说明（自然键，不做硬引用）
    status:Mapped[str]=mapped_column(String(20),default=RENAME_JOB_STATUSES[0])
    rules:Mapped[list]=mapped_column(JSON)       # [{old_key,new_key}]
    state_version:Mapped[int]=mapped_column(default=1)
    last_event:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    switched_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
class RenameKey(Base):
    __tablename__='rename_keys'
    __table_args__=(UniqueConstraint('job_id','key_name',name='uq_rename_key'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('rename_jobs.id'))
    key_name:Mapped[str]=mapped_column(String(64))
    value:Mapped[str|None]=mapped_column(String(200),nullable=True)
    is_tombstone:Mapped[bool]=mapped_column(default=False)
    version:Mapped[int]=mapped_column(default=1)
class RenameDecision(Base):
    __tablename__='rename_decisions'
    __table_args__=(UniqueConstraint('job_id','group_ref',name='uq_rename_decision'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('rename_jobs.id'))
    group_ref:Mapped[str]=mapped_column(String(64))      # 目标新名（组以新名归并）
    winner_key:Mapped[str]=mapped_column(String(64))     # 值班员选择保留值的来源键
    winner_value:Mapped[str]=mapped_column(String(200))  # 裁决时刻快照值
    based_on_version:Mapped[int]=mapped_column()         # 乐观并发：裁决依据的 state_version
    state:Mapped[str]=mapped_column(String(20),default='active')
class RenameAlias(Base):
    __tablename__='rename_aliases'
    __table_args__=(UniqueConstraint('job_id','alias_name',name='uq_rename_alias'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey('rename_jobs.id'))
    alias_name:Mapped[str]=mapped_column(String(64))     # 旧名或中间名
    canonical_name:Mapped[str]=mapped_column(String(64)) # 切换后的规范新名
