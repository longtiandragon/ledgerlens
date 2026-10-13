# 本地 SQLite 启动前迁移

更新：2026-10-13。`scripts/start-local.ps1` 在启动API/Vite前执行 `scripts/prepare_local_database.py`，失败时不启动服务。迁移通过仓库冻结的Alembic版本，当前head为 `0006`；没有修改既有迁移文件、模型或账务规则。

## 原问题与处理

旧本地启动路径直接运行API，API的 `create_all` 能建立缺失表，却不会记录迁移版本或修改已存在列。这样的旧库直接运行 `alembic upgrade head` 会与已有表冲突。新前置步骤区分三种情况：

|数据库状态|行为|
|---|---|
|空库|从冻结初始迁移升级到head，不通过现有ORM倒推历史结构|
|有版本记录|完整结构必须与该冻结版本一致，再备份并向前迁移；已在head且结构一致则不写入|
|无版本旧库|在内存数据库逐版执行冻结迁移，比较全部业务表、列、类型、可空性、主键、唯一约束、外键、检查约束和索引；只在唯一匹配时备份、登记已有版本并继续向前迁移|

未知表、缺列、额外列、索引漂移、视图、触发器、未来/多分支版本及异常版本表被明确拒绝。不会自动删表、改字段、强行stamp或清空历史。文件路径和备份目录不得经过链接；PostgreSQL仍走容器的显式Alembic迁移流程，本脚本只接收普通文件SQLite。

进入检查前取得 `BEGIN IMMEDIATE` 写锁，排除同时写入。对已有结构的每次实际修改，先通过SQLite在线备份接口生成数据库同目录下 `schema-backups/<文件名>-<UTC时间>-<随机值>.sqlite`，并检查备份完整性，再执行stamp/upgrade。显式事务覆盖DDL；失败回滚，已经生成的备份保留。备份文件名唯一且不覆盖，成功输出其绝对路径和SHA-256。

这份备份仅包含业务SQLite数据库，**不是全项目备份**；来源原件、独立检查点数据库和配置仍需运行手册中的完整备份流程。准备迁移前应停止API写入。持有写锁时拒绝本次迁移，不能据此推导为多进程在线升级方案。

## 使用

通常沿用原启动命令即可，`-SkipInstall`同样执行迁移准备：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -SkipInstall
```

也可以对停止使用的本地库或独立副本单独执行：

```powershell
.venv/Scripts/python.exe scripts/prepare_local_database.py
.venv/Scripts/python.exe scripts/prepare_local_database.py --database .local/rehearsal/ledgerlens.sqlite
```

无参数读取与API一致的 `DATABASE_URL` 配置，但不输出配置或密钥。`--database`用于指定副本；不要手动复制正在使用的SQLite主文件而忽略WAL，应通过SQLite备份接口或现有备份脚本取得一致副本。恢复时使用新目录核对完整数据，不用备份覆盖正在运行的库。

直接运行uvicorn会绕过这个启动器，API的旧 `create_all` 行为仍保留用于现有单进程测试；它不是升级已有数据库的替代方法。

## 验收

新增24项准备流程检查，连同既有SQLite迁移检查共30通过（12.00秒），Ruff通过。覆盖六个历史版本的有版本/无版本升级、ORM建表旧库、空库、备份内容相等、历史JSON原样保留、重复执行不改数据、结构漂移拒绝、写锁拒绝、备份失败以及DDL中途失败整事务回滚。没有修改测试预期迁就实现。

对当前工作库先只读生成一致副本，再在副本演练：识别无版本的 `0006` 结构，21张业务表共3257行逐行相等；升级前备份内容一致，第二次执行返回current。原工作库未执行迁移。证据：`.local/migration-rehearsal-v2/report.json`。

PowerShell启动器在隔离数据目录实跑六项通过：未知库拒绝且未启动服务；无版本 `0004` 先备份再升级 `0006`；历史记录保留；API合成导入及规则检查通过；Vite就绪；退出时自有进程记录移除。首轮验收遭中断，仅有拒绝日志，不计成功；第二轮完整报告为 `.local/local-launcher-v2/report.json`。验收临时使用先前干净源码中的依赖及测试脚本覆盖，结束后恢复归档源码；并未重写此前干净源码的验收结果。

各报告和源码哈希见[公开摘要](local-database-upgrade-validation.json)。未更改生产API、前端页面或数据规则，本轮不重复此前全量后端/浏览器验收。没有付费请求或新依赖。未来未知结构、任意旧版本、系统崩溃/断电及PostgreSQL在线升级不在本次验证范围内。
