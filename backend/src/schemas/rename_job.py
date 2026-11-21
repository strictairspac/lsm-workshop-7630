from typing import Literal
from pydantic import BaseModel,Field,model_validator
class RenameRule(BaseModel):
    old_key:str=Field(min_length=1,max_length=64)
    new_key:str=Field(min_length=1,max_length=64)
class BatchEntry(BaseModel):
    op:Literal['put','delete']
    key:str=Field(min_length=1,max_length=64)
    value:str|None=Field(default=None,max_length=200)
    @model_validator(mode='after')
    def check_value(self):
        if self.op=='put' and self.value is None:raise ValueError('put 操作必须包含字符串 value')
        return self
class RenameJobInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    source_note:str=Field(default='',max_length=200)
    rules:list[RenameRule]=Field(min_length=1,max_length=500)
    entries:list[BatchEntry]=Field(default=[],max_length=2000)
class LiveWriteInput(BaseModel):
    op:str=Field(pattern='^(put|delete)$')
    key:str=Field(min_length=1,max_length=64)
    value:str|None=Field(default=None,max_length=200)
class DecisionInput(BaseModel):
    group_ref:str=Field(min_length=1,max_length=64)
    winner_key:str=Field(min_length=1,max_length=64)
    expected_version:int=Field(ge=1)
class ApplyInput(BaseModel):
    attempt_id:str=Field(min_length=8,max_length=80)
