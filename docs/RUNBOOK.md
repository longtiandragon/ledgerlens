# 本地运行与故障恢复

## 两种部署

开发模式运行 `scripts/start-local.ps1`。默认本地SQLite，单API进程；不要使用多个uvicorn worker，本地恢复假设只存在一个调度进程。前端Vite代理 `/api` 到8000。

确认首版基础流程不依赖已有账套和密钥，可运行 `scripts/validate_clean_checkout.py --output .local/clean-checkout-new`。它从Git提交导出源码、离线按锁文件安装依赖，再以独立空库执行数据生成、规则和网页验收；需要完整依赖缓存及已安装Chrome，详见[干净源码复现](CLEAN_CHECKOUT.md)。不会停止当前8000/5188服务。

容器模式先确认Docker服务、镜像存储目录和磁盘余量，再 `docker compose config --quiet` 与 `docker compose up --build -d`。绑定127.0.0.1:8080/8000；8080被占用时设置`WEB_PORT`（如8088）。数据库默认口令仅用于不对外暴露的本机环境，可在.env设置POSTGRES_PASSWORD。STORAGE_ROOT指定宿主数据目录，不代表Docker镜像虚拟盘已经迁移。

`docker compose logs api worker scheduler`查看运行；`docker compose down`保留绑定目录。不要使用删除数据目录的清理命令。容器中API启动前执行`alembic upgrade head`，再启动服务；worker与scheduler等待API健康后才启动，只有API执行迁移。LangGraph检查点表由其`saver.setup()`维护，Alembic比对时排除这四张表，其他未知表仍会报告。所有服务设置`restart: unless-stopped`。结构更改需通过Alembic正式迁移，不依赖create_all修改已有表。

容器验收（不调用模型，须置空DeepSeek密钥）：`$env:DEEPSEEK_API_KEY=''; $env:WEB_PORT='8088'; docker compose up --build -d`，然后`.venv\Scripts\python.exe scripts/validate_containers.py --web-url http://127.0.0.1:8088/`。

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

备份包含业务SQLite、检查点SQLite、来源文件及清单。单数据库backup操作一致，但多个文件的整体一致性要求暂停写入。PostgreSQL使用pg_dump，并同时复制来源文件；不得把SQLite备份命令套用到PostgreSQL。

恢复到新目录，先校验所有文件哈希和SQL完整性，再指向新DATA_DIR/DATABASE_URL。业务Document保存的原始存储路径需要迁移，不能仅移动数据库就声称来源可读取。恢复脚本提供显式路径修复，保留原备份不修改。

```powershell
.venv\Scripts\python.exe scripts/restore_local.py --backup F:\LedgerLensBackups\backup-001 --destination F:\LedgerLensRestored\restore-001
```

恢复器先验证业务库、检查点库及每份来源文件，再在临时目录修复路径并移入一个全新目录。不会覆盖现有运行目录。`.env`、公开资料索引、日志与基准报告不在此备份格式内；旧调查的参考片段已经冻结在业务快照，新资料索引可用采集清单重新建立。

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
