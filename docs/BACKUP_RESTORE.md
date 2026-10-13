# 本地备份与恢复验证

更新：2026-10-14。前半部分适用于本地 SQLite，公开摘要见 [backup-restore-validation.json](backup-restore-validation.json)；Compose PostgreSQL 部署见文末。

## 使用

先停止 API 和 worker 写入，再从仓库根目录运行，两个目标均须为不存在的新目录：

```powershell
.venv\Scripts\python.exe scripts/backup.py --runtime runtime --output F:\LedgerLensBackups\backup-001
.venv\Scripts\python.exe scripts/restore_local.py --backup F:\LedgerLensBackups\backup-001 --destination F:\LedgerLensRestored\restore-001
```

备份使用 SQLite 只读连接和 backup 接口取得业务库及可选检查点库，复制 sources 原件，验证数据库完整性及每个 Document 的原件哈希。全部成功后才把临时目录发布到指定位置。失败不留下看似完整的目标目录；拒绝覆盖、源目录内的目标，以及路径中的符号链接或目录联接。

新格式 `sqlite-local-backup-v2` 的 `backup.json` 记录每个数据库和来源文件的 SHA-256、字节数，并明确检查点库是否包含。恢复在复制前后分别核对清单，再检查数据库及原件；缺少或改动数据库、缺少或新增来源文件、复制过程中损坏都会拒绝。只在恢复副本中重定位 `documents.storage_path`，原备份不改动。

恢复后先核验新目录，再配置对应 DATA_DIR/DATABASE_URL。恢复副本保留的 backup.json 是原备份记录，其中业务库哈希对应重定位之前的字节；恢复目录本身不是可直接再次恢复的备份。需再次备份时，对该目录重新运行 backup.py，生成新的清单。不要直接移动恢复目录，否则已重定位的绝对路径会失效。

## 范围与限制

- 每个 SQLite 备份各自一致，跨数据库和原件的一致性依赖停止写入；脚本不自动停止服务，也未验证断电和并发写入恢复。
- 旧版无文件清单的备份仍可读取，并检查 SQLite 完整性和 Document 原件；无法据此识别原本存在但后来丢失的检查点库。未知版本、重复 JSON 键及残缺的新版清单拒绝读取。
- 哈希用于发现损坏或不一致；没有外部签名，不能证明被恶意重写的清单可信。
- 不包含 `.env`、模型、公开资料索引、日志、评测报告或 schema-backups。历史参考片段虽已冻结，完整校验仍依赖其绑定的受信资料注册表及本机资料；本次在现有资料环境校验，不代表空白机器的完整灾难恢复。
- 远端存储、服务启动后恢复历史调查及真实企业业务效果不在本次验收范围；PostgreSQL容器部署见文末独立工具及其验证。

## 验证证据

21 项新增回归与原 11 项恢复检查合计 **32 通过、0 跳过（2.18 秒）**，Ruff 通过。包括可选检查点、缺失/改动/新增文件、版本和清单异常、备份复制失败、恢复复制期间损坏以及拒绝覆盖。未改变原金额与完整性断言。

停止本地 API/worker 后，只读备份当前合成测试工作库，恢复到 `.local/restore-rehearsal-v1/restored`：

|核验|结果|
|---|---|
|业务库|21 张表、3257 行，除 Document 存储路径外逐行一致|
|检查点库|573 条 checkpoints、3606 条 writes，逐行一致|
|原件|266 个 Document 重定位并校验 SHA-256；10 个去重原件文件|
|历史上下文|199 个快照、26 个完成或部分完成的 OCR 结果重新校验通过|
|冻结参考资料|按快照绑定注册表核验 117 个参考上下文通过|
|原备份|12 个有效载荷文件，恢复前后清单和哈希一致|

未启动恢复目录的 API，避免派发历史任务；验证连接只读，无模型请求或新增依赖。

首次专项测试 2 失败/30 通过：Windows 上测试夹具的 SQLite 连接未显式关闭，导致删除和故障清理被占用；改用 closing 保留事务提交并关闭连接后通过。首次 Ruff 另发现夹具导入别名与参数冲突，已修正。首次实际复核误用默认资料注册表，导致 4 个显式选择 BGE 资料的历史快照拒绝；改用生产的按快照绑定注册表校验入口后 117 个通过，没有修改资料或预期。初次测试 XML 与 verification.json 均保留，最终报告为 verification-v2.json。

后续补验：[恢复副本调查续跑](RESTORED_RUN.md) 在另建的合成库实际中断两个规则调查进程，备份恢复后续跑/复用结果、预算保持及重复派发通过；未对上方工作库中的历史调查执行重跑。

## Compose PostgreSQL 部署（2026-10-14）

容器部署使用独立工具 `scripts/postgres_backup.py`，不复用SQLite命令。先停止写入方，只保留postgres运行；备份与恢复目标都必须是不存在的新目录：

```powershell
docker compose stop api worker scheduler
.venv\Scripts\python.exe scripts/postgres_backup.py backup --output F:\LedgerLensBackups\pg-001
.venv\Scripts\python.exe scripts/postgres_backup.py restore --backup F:\LedgerLensBackups\pg-001 --storage-root F:\LedgerLensRestored\pg-001
$env:STORAGE_ROOT='F:\LedgerLensRestored\pg-001'; docker compose up -d
```

工具从 `docker compose config` 读取实际镜像、账号和绑定目录（含STORAGE_ROOT与POSTGRES_PASSWORD覆盖）。备份要求postgres健康且api/worker/scheduler未运行，`pg_dump -Fc` 前后逐表指纹（行数及排序后逐行md5的md5）必须一致；复制API数据目录（排除Celery beat调度文件与可重建的ocr-cache），逐个Document核对容器内路径与原件哈希；再把转储恢复到同一镜像的一次性容器、逐表比较，全部一致后才发布 `compose-postgres-backup-v1` 清单。恢复同样经一次性容器生成新的STORAGE_ROOT（app与postgres），复查表、修订号和Document，并保留 `restored-from-backup.json`。密码通过环境变量传给容器，不出现在命令行；恢复时使用之后运行该栈的同一POSTGRES_PASSWORD。

实测：写入方运行时备份拒绝、无目录残留；停写后备份48.5秒，26张表1586行、10个Document、PostgreSQL 17.10、迁移0006；恢复21.4秒。另以独立Compose项目在恢复目录启动全栈，迁移保持0006（head），只读API视图（账套、记录、Document下载哈希、案件、运行、快照）与原部署SHA-256完全一致，完整容器验收20/20通过。原部署的数据文件在备份后未变化。修复相对路径清单缺陷后用最终代码重复备份42.2秒、在部署已停止时恢复21.2秒，与首轮表、Document和修订完全一致，转储字节因时间戳不同。17项单元检查覆盖停写前提、表名注入、排除项、Document布局与篡改、清单重复键、目录重叠及恢复前拒绝。

限制：需要停写，没有在线或时间点恢复、WAL归档和加密；不备份Redis，排队运行依赖定时失效租约恢复重新派发；只在同一开发机验证，异机恢复与大库未测；清单哈希只发现损坏，不能证明被恶意重写的清单可信。摘要见 [postgres-backup-restore-validation.json](postgres-backup-restore-validation.json)。
