Schema v1: users, audit, datasets (JSON write sequence), configs (bounded LSM parameters), trials (immutable input snapshot and JSON result). MySQL stores metadata only. Run python -m scripts.seed_demo for create-if-missing and natural-key seed; existing values are not overwritten.

Schema v2 (遥测键统一改名演练，均由 Base.metadata.create_all 幂等建表，无破坏性变更):
- rename_jobs: 改名演练主表。name 自然键唯一；rules JSON 保存 [{old_key,new_key}]；status 取 draft/switching/switched/failed；state_version 乐观版本号，每次站点写入与最终切换推高；last_event JSON 记录最近一次并发写入、失效组与切换 attempt；switched_at 记录一次性切换时间。
- rename_keys: 演练内的活键与墓碑行，(job_id,key_name) 唯一。value 为空且 is_tombstone=true 表示墓碑；version 为键级版本。
- rename_decisions: 值班员对撞名组（group_ref=目标新名）的裁决，(job_id,group_ref) 唯一。winner_key/winner_value 为裁决时刻快照，based_on_version 为乐观并发依据；state=active/invalidated，组内改值或删键后由 revalidate 置为 invalidated，先前选择失效需重判。
- rename_aliases: 一次性切换在同一事务内写入的旧名→规范新名别名，(job_id,alias_name) 唯一。切换后旧名与新名经 resolve_name 读到同一个规范值；切换对 switched 状态幂等，同次重试只启用一版。
