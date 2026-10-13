# 本地运行与故障恢复

## 两种部署

开发模式运行 `scripts/start-local.ps1`。默认本地SQLite，单API进程；不要使用多个uvicorn worker，本地恢复假设只存在一个调度进程。前端Vite代理 `/api` 到8000。

本地启动器现会在服务启动前核验数据库结构并执行向前迁移：已知无版本旧库只在完整结构匹配时接管，变更前生成SQLite备份，未知结构或迁移失败拒绝启动。`-SkipInstall`也执行此步骤，详见[本地数据库升级](LOCAL_DATABASE_UPGRADE.md)。业务库备份不包含来源文件和检查点，完整备份仍按下文执行。

确认首版基础流程不依赖已有账套和密钥，可运行 `scripts/validate_clean_checkout.py --output .local/clean-checkout-new`。它从Git提交导出源码、离线按锁文件安装依赖，再以独立空库执行数据生成、规则和网页验收；需要完整依赖缓存及已安装Chrome，详见[干净源码复现](CLEAN_CHECKOUT.md)。不会停止当前8000/5188服务。

已有Ubuntu WSL时可补验核心跨平台行为：`.venv\Scripts\python.exe scripts/validate_linux_core.py --output .local/linux-core-new`。从提交导出源码，使用已安装且锁定的纯Python测试包，在Linux临时目录运行480规则/30参考例、金额匹配和备份恢复检查。它不安装完整Linux后端，范围与证据见[LINUX_CORE](LINUX_CORE.md)。

容器模式先确认Docker服务、镜像存储目录和磁盘余量，再 `docker compose config --quiet` 与 `docker compose up --build -d`。绑定127.0.0.1:8080/8000；8080被占用时设置`WEB_PORT`（如8088）。数据库默认口令仅用于不对外暴露的本机环境，可在.env设置POSTGRES_PASSWORD。STORAGE_ROOT指定宿主数据目录，不代表Docker镜像虚拟盘已经迁移。

`docker compose logs api worker scheduler`查看运行；`docker compose down`保留绑定目录。不要使用删除数据目录的清理命令。容器中API启动前执行`alembic upgrade head`，再启动服务；worker与scheduler等待API健康后才启动，只有API执行迁移。LangGraph检查点表由其`saver.setup()`维护，Alembic比对时排除这四张表，其他未知表仍会报告。所有服务设置`restart: unless-stopped`。结构更改需通过Alembic正式迁移，不依赖create_all修改已有表。

容器验收（不调用模型，须置空DeepSeek密钥）：`$env:DEEPSEEK_API_KEY=''; $env:WEB_PORT='8088'; docker compose up --build -d`，然后`.venv\Scripts\python.exe scripts/validate_containers.py --web-url http://127.0.0.1:8088/ --output .local/reports/container-new.json`。每次使用不存在的报告路径；脚本先独占创建报告，再验证六个服务、API模式与空密钥、当前源码迁移头、结构漂移和pgvector可用性。预检失败停止，不创建测试账套或停止worker。异常与键盘中断记为未通过；强制杀进程可能仅留下running/未通过记录，不能当作成功。停机注入会在发出stop前登记本轮需恢复的worker/scheduler；普通异常或键盘中断退出时逐个尝试start并查询容器状态，恢复失败不会跳过另一个服务，原验收仍为失败。报告service_restoration记录每个结果。强制终止、宿主故障或恢复失败仍需人工检查并恢复服务，running状态不代表Celery业务就绪。2026-10-14已在真实容器对“stop返回后键盘中断”做两次复验并通过，stop执行中途的信号和强制终止仍未覆盖，见[容器复验摘要](container-revalidation-validation.json)。

升级已有容器数据前，先停止栈并整目录复制`runtime/docker`（或STORAGE_ROOT）作备份；API启动时只向前迁移。Docker Desktop若因`%LOCALAPPDATA%\Docker\run\dockerInference`无法访问而启动失败，属于非正常停止遗留的套接字，在Docker未运行时删除该文件后再启动。

## 模型配置

`.env`中设置DeepSeek密钥/端点/模型，重启后端。401、额度不足为明确失败，不自动改用未授权模型；429及暂时网络失败在有限预算内重试。远程调用可能已经计费而响应丢失，预算记录因此保守预留。

运行`scripts/test_deepseek_live.py --live`可验收账户模型列表、非思考JSON和思考工具往返，最多5次计费尝试。产品调查用`scripts/run_investigation_smoke.py --case-id ID --mode single --live`启动一次并验收；已有run用`--run-id ID`只读验收。只读模式的账簿哈希只能证明验收读取期间一致，不能补证历史run开始前的账簿状态。

模型结论中的`output_validation_failed`保存安全错误类型及字段路径，不保存被拒绝的字段值或私有推理。历史运行可以在调查页“调查记录”中选择；`?book=ID&case=ID&run=ID`只读打开对应运行，不触发调查。HTTP402显示额度不足，本轮基准不会继续向同一配置派发模型样本。

## 运行恢复

本地API重启将queued/running案件重新派发，持久检查点恢复；单请求尝试预算不重置。Celery调度器每分钟检查，10分钟无heartbeat的running租约被替换，再派发；配置worker并发1。租约失效的旧执行者不能保存结果。

案件新增资料后原run仍绑定旧快照，可继续查看但复核失效。应新建检查任务及新案件，不在旧快照内偷偷添加资料。

## 备份

先停止API/worker写入，再运行：

```powershell
.venv\Scripts\python.exe scripts/backup.py --runtime runtime --output F:\LedgerLensBackups\backup-001
```

备份包含业务SQLite、可选检查点SQLite、来源文件及v2清单（逐文件SHA-256、字节数和检查点是否包含）。校验成功才发布到全新目录。单数据库backup操作一致，但多个文件的整体一致性要求暂停写入。PostgreSQL容器部署先`docker compose stop api worker scheduler`，再用`scripts/postgres_backup.py backup --output <新目录>`；恢复用`restore --backup <备份> --storage-root <新目录>`后以该STORAGE_ROOT启动，步骤与限制见[备份恢复](BACKUP_RESTORE.md)。不得把SQLite备份命令套用到PostgreSQL。

恢复到新目录，先校验所有文件哈希和SQL完整性，再指向新DATA_DIR/DATABASE_URL。业务Document保存的原始存储路径需要迁移，不能仅移动数据库就声称来源可读取。恢复脚本提供显式路径修复，保留原备份不修改。

```powershell
.venv\Scripts\python.exe scripts/restore_local.py --backup F:\LedgerLensBackups\backup-001 --destination F:\LedgerLensRestored\restore-001
```

恢复器在复制前后验证v2清单、业务库、检查点库及每份来源文件，再在临时目录修复路径并移入一个全新目录。不会覆盖现有运行目录。`.env`、模型、公开资料索引、日志与基准报告不在此备份格式内；旧调查的参考片段已经冻结在业务快照，但完整校验仍需其绑定的受信资料注册表。旧格式的校验限制、恢复后再次备份方式及实跑证据见[备份恢复验证](BACKUP_RESTORE.md)。

备份恢复后的任务续跑可在全新合成目录离线验收：`.venv\Scripts\python.exe scripts/validate_restored_run.py --output .local/restored-run-new`。不读取工作库或调用模型；覆盖两个已提交检查点后的进程退出、恢复续跑和重复派发。详见[恢复副本调查续跑](RESTORED_RUN.md)。

## ERPNext 只读接入

`scripts/export_erpnext.py --help`列出参数。Token需要读取Company和GL Entry权限；公司本位币必须明确为CNY，科目映射必须提供。金额直接从JSON数值解析为Decimal，不以浮点或缺失值补零。分页导出不是原子快照，应在源端暂停记账时执行。当前仅适配总账，不从总账猜出采购/收货资料。

本地ERPNext联调：`docker compose -f infra/erpnext/compose.yaml up -d`（镜像`frappe/erpnext:v16.50.0`约6.7GB，站点创建需数分钟，端口127.0.0.1:8090，口令仅为本机演示值），然后在LedgerLens本地服务运行时执行`.venv\Scripts\python.exe scripts/validate_erpnext.py --output <新报告>`。脚本通过setup向导建立CNY公司、生成本机API密钥、过账合成凭证（含一张已取消凭证与一张期外凭证，按备注幂等复用），用生产适配器导出并与手写预期及ERPNext试算平衡表核对，再导入LedgerLens核对余额。停止：`docker compose -f infra/erpnext/compose.yaml stop`。

## 常见问题

- 页面API不可用：检查8000/5188端口及runtime日志；排队状态与模型等待分开。
- 导入失败：查看行号/字段，不删除错误行冒充全量成功；相同来源ID不同内容需明确源版本。
- 模型未配置：先使用规则模式。规则成功不证明模型调用已通过。
- 公开来源502：采集报告保留失败，可以之后重试；不要从失效页面编造规则正文。
- 余额不一致：先确认期间、期初、科目映射和导入完整性。
- SQLite与Postgres结果应分别验证；Compose语法通过不代表容器已成功启动。
