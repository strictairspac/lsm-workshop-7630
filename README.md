# 边缘遥测 LSM 合并工坊

北岭边缘计算小组的有序键值写入与合并演练：记录写入批次、配置层级策略、后台执行刷盘与合并，用最后写入字典 Oracle 核查读结果，防止墓碑误删导致旧值复活。

**这是 Python 确定性教学 LSM 模型。MySQL 8.0 仅保存业务元数据，不是对 MySQL/InnoDB 存储内核的实现或性能测试。** 页面与导出结果均保留此边界。

## 场景与模块

北岭边缘计算小组有 7 人，负责低内存遥测站点的存储方案验证。过去在电子表格记录写入序列、手工画 SSTable、用本地脚本核对合并结果，三个痛点是配置与操作序列混在一起、删除键的历史难复查、读写放大缺少一致口径。上游为站点模拟写入序列；本站替代输入归档、配置组合与可重现演练；下游是边缘存储评审人与值班交接。

- 写入批次：有序 put/delete，支持重复键、墓碑、完整操作预览、输入校验。
- 层级配置：memtable 不同键容量、SSTable 合并触发数、层级上限、最终完整合并开关。
- 合并队列：创建试验时持久化输入及配置快照，Redis 分发，独立 worker 执行并记录状态。
- 读放大证据：显示合并前后层级、读写放大、真实存活键、Oracle 摘要、刷盘/合并轨迹、JSON 导出。
- 键名统一演练：在工坊选批次配置旧名→新名改名规则；多个活键归到同一新名时由值班员裁决保留哪个值，裁决期间的并发改值/删键按乐观版本号让先前选择失效并要求重判；切换在单事务内一次性完成，旧名立碑并建立到新名的别名，新旧两种叫法读到同一个值；中断后 worker 按 attempt 幂等恢复，同次重试只启用一版。
- 管理员审计：记录写操作账号、路由与 HTTP 结果，包含权限拒绝。

| 角色 | 页面与权限 | 典型动作 |
|---|---|---|
| admin 管理员 | 全业务页面与审计 | 登记批次/配置、执行、导出、查看审计 |
| operator 操作员 | 四业务页面，不可看管理员审计 | 新建输入与配置、提交 worker、检查与下载结果 |
| viewer 观察员 | 四业务页面只读与导出 | 阅读层级和 Oracle 证据，写请求在 API 层403 |

## 启动与账号

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 默认启动自动执行；手动重跑不覆盖已存在的输入、配置、账号
docker compose exec -T backend python -m scripts.seed_demo
```

[前端入口](http://localhost:18245) · [后端接口文档](http://localhost:19245/docs)，数据库映射端口17745。

三个本地演示账号是 `admin@beiling.test` / `operator@beiling.test` / `viewer@beiling.test`，统一密码 `EdgeStore!17`。`.env.example` 提供固定本地数据库与JWT演示凭据，上线前替换。

```bash
# 只清理本项目与其演示卷
docker compose down -v --remove-orphans
```

## 09:00—10:00 的工作剧本

1. 09:00 operator 登录，打开“北岭站点-早班写入”：12条操作，8个不同键。
2. 09:05 查看 sensor-01 的18.2→18.8→19.0覆盖记录，sensor-02和sensor-03有删除墓碑。
3. 09:10 新建 `shift-20261117` 输入，或复用“旧值复活回归集”。
4. 09:15 选择“小内存-完整合并”：memtable键容量2，表触发数2，3层，最后完整合并。
5. 09:20 创建试验并提交worker，在队列刷新至“Oracle一致”。
6. 09:25 打开读放大证据，确认早班输入最终6个存活键，sensor-02/03未复活。
7. 09:30 下载 `lsm-trial-<id>.json`，包含操作/配置快照、层级、轨迹与校验和。
8. 09:40 viewer复核，创建试验被403拒绝；10:00 admin检查操作审计。

Seed另含“旧值复活回归集”和“全部删除边界集”；配置另含“保留层级观测”和“宽缓冲-完整合并”。账户、批次、配置以自然键查缺插入，重复seed不改变已存在字段。

## 核心算法与指标口径

输入为有序操作数组和参数配置：

- 操作数1–2000；键长1–64；put的value必须是1个不超过200字符的字符串（允许空字符串）；delete的value忽略。
- memtable_limit范围2–64，按不同键数量计容量；l0_trigger范围2–8；max_levels范围2–6。
- 每个用户操作分配单调序号。memtable同键覆盖；达到容量后按键排序刷成L0 SSTable。末尾不足容量也刷盘。
- 某层表数达到触发值时，合并该层所有表到下一层；相同键按最高序号保留记录。最深层在原层合并。**层级合并始终保留墓碑**，不会提早清理。
- 若打开最终完整合并，收集全部层级所有表，按序号合并完整历史后才移除墓碑，存放在最深层。不开启时保留中间层级与墓碑。
- 点查遍历范围覆盖键的SSTable，在候选记录中选择最高序号；带墓碑则不返回。固定探测集合为全部输入键加一个不存在的探针键。没有Bloom过滤器模型。
- 读放大 = 固定点查集合的范围命中SSTable读取总数 / 探测键数；合并前后使用相同集合。写放大 = flush/各次compaction真实输出条目总数 / 用户操作数，**不是物理字节或磁盘I/O**。
- Python另从原始操作顺序构造最后写入字典Oracle；合并前、合并后均必须与Oracle一致，否则试验failed。结果与Oracle各自对有序JSON求SHA-256；同输入/配置结果确定，无随机时钟项。

输出包括真实计算的层级表数/键范围/墓碑数、每步刷盘合并轨迹、输入键数、存活键、写出条目、读写放大、Oracle及实际键值、摘要。空输入和超限在提交时422，引用不存在404，重名409，排队/运行总数超过20时429；worker失败保存error和failed状态。

## 遥测键统一改名演练（裁决台）

“键名统一”页面把同一站点的多个历史叫法归并到规范新名，是独立于 LSM 演算的并发改名教学模型，状态全部保存在 MySQL：

1. **配规则、选批次**：`POST /renames` 提交 `rules:[{old_key,new_key}]` 与可选 `entries` 初始批次。同一旧名只能出现一次；旧名=新名、以及 A→B 且 B→C 的链路改名在一次性切换模型中被 422 拒绝。规则按新名归并成裁决组，组内超过一个活键即 `collision` 撞名待裁决。
2. **值班员裁决**：`POST /renames/{id}/decisions` 提交 `group_ref/winner_key/expected_version`。`expected_version` 必须等于当前 `state_version`，否则 409——这是乐观并发控制，防止拿着过期视图覆盖别人的裁决。
3. **并发写入与失效**：裁决期间 `POST /renames/{id}/live-write` 模拟站点实时 put/delete，每次推高 `state_version`；若改值或删键命中某裁决组的赢家键，该裁决立即置 `invalidated`（页面显示 stale），先前选择失效，必须按最新值重判后才能切换。
4. **一次性原子切换**：`POST /renames/{id}/switch` 前置校验（无失效裁决、无未裁决撞名组）后入 Redis 队列，worker 在**单个数据库事务**内完成：新名取裁决赢家值（单活键组取唯一活值，全墓碑组新名也为墓碑）、全部旧名立碑、写入旧名→新名别名。切换后 `GET /renames/{id}/keys/{name}` 用旧名或新名都解析到同一规范行、读到同一个值。
5. **中断恢复与幂等**：每个切换带 `attempt_id`；已 `switched` 的演练再次执行直接幂等返回，同次重试只启用一版。worker 轮询到遗留在 `switching` 的演练会按原 attempt 重放；`failed` 状态允许修正冲突后用新 attempt 重新驱动。切换后演练封板，拒绝再写入或改判。

错误码与全站一致：重名 409、规则/墓碑赢家 422、引用不存在 404、待切换超过 20 个 429、观察员写请求 403。种子数据含“北岭站点-遥测键统一演练”，预置 temperature（三活键）与 humidity（两活键）两个撞名组。

## 技术栈与代码组织

| 服务 | 实现与作用 |
|---|---|
| frontend | 原生HTML/CSS/JS ES模块，Nginx1.27，土橙材料样本册视觉 |
| backend | Python3.11、FastAPI、SQLAlchemy、PyMySQL、JWT与PBKDF2密码 |
| worker | 独立Python进程，Redis BLPOP，LSM演算，数据库恢复队列，心跳健康检查 |
| db | MySQL8.0，持久化3个业务实体、用户与审计 |
| redis | Redis7，异步队列与持久卷 |

`backend/src/`分为config/models/schemas/services/controllers/routes/middlewares/utils/constants；`backend/scripts/seed_demo.py`种子与初始化；`backend/migrations/README.md`为schema v1/v2说明。dataset/config/trial与rename_job各自贯穿model/schema/service/route、前端api/store/page。试验结果保存在Trial JSON字段中，改名演练的活键/裁决/别名保存在rename_keys/rename_decisions/rename_aliases表，详情页为独立业务页面。确定性后端测试位于`tests/`（SQLite内存库+假Redis，无需Docker）：`python3 -I tests/test_rename_service.py`与`python3 -I tests/test_rename_api.py`。

`frontend/src/`包含api/stores/types/components/common/hooks/pages/router/utils/constants，表格/状态/页头跨页共享，HTML转义与反馈工具复用。角色/任务状态/操作类型枚举在`backend/src/constants/enums.py`和`frontend/src/constants/enums.js`。模型默认任务状态使用后端枚举，前端状态组件使用前端枚举。

Docker恰好5服务，全部healthcheck；backend依赖db/redis healthy，worker还等待backend完成seed；frontend等待backend healthy。命名卷保存MySQL、Redis及worker证据。Nginx `/api/`剥前缀转发后端。

环境变量：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、MYSQL_PASSWORD、MYSQL_ROOT_PASSWORD、JWT_SECRET、SEED_DEMO；容器连接还配置DATABASE_URL/REDIS_URL。端口可在.env整体调整避免冲突。

本地开发推荐修改代码后`docker compose up -d --build`。也可用虚拟环境安装backend/requirements.txt，启动Compose db/redis，设置可达DATABASE_URL/REDIS_URL/JWT_SECRET，进入backend执行`uvicorn src.main:app --reload`；worker须有可写`/artifacts`并执行`python -m src.worker`。

## 已知边界

- 存储过程为内存教学模型，忽略磁盘块、压缩、Bloom过滤器、WAL持久化、并行compaction与真实故障恢复。MySQL负责保存网页业务输入和输出。
- 前端创建独立批次/配置，不提供原记录编辑删除；新版本新建记录，试验总是固定输入快照。
- 最终全历史合并允许去掉墓碑；没有模拟跨副本历史/快照读，因此不能将该规则直接用于生产数据库。
- 单worker与按钮刷新；不提供多worker分布式锁、SSE。
- Web双字体来自Google Fonts，离线回退到serif/monospace。

MIT License，见LICENSE。实际测试与截图位于批次的`test-artifacts/gb-4106/`，源码内不保存测试证据。
