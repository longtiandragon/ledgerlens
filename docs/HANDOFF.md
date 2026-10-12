# 交接文档

更新：2026-10-12。本文给接手的开发者或编程 Agent 使用：先读这里，再按需查 [STATUS](STATUS.md)（逐项验证记录）、[GOALS](GOALS.md)（目标与验收门槛）和 [AGENTS.md](../AGENTS.md)（必须遵守的协作规则）。

## 1. 一句话现状

账证智核（LedgerLens）是本地运行的会计复核研究与辅助平台：导入账套 → 固定快照 → 确定性规则检查 → 证据调查（规则或 DeepSeek 模型）→ 隔离模拟 → 复核底稿，另有官方政策检查、BPI 流程证据、年报勾稽和 ERPNext 只读接入。工程链路完整且全部测试通过；**核心研究主张（模型＋主动补证优于同预算确定性方法）在本项目自建的封存测试上未获支持**，见 [原因调查轨道](ROOT_CAUSE_TRACK.md)。

|项|当前值|
|---|---|
|产品版本|0.1.0（本地工程与仿真版本，已完成首次本地提交；未配置远端，尚未发布）|
|调查提示/工具版本|`investigation-v26` / `snapshot-tools-v11`；root_cause已改窄契约，真实模型尚未验证|
|政策规则包|`policy-consistency-v4`，20 条（CAS 18 / SCAS 2），101 个手写投影全部通过|
|流程规则包|`bpi2019-process-rules-v1`，9 条；流程模型解释 `process-investigation-v1`|
|数据库迁移|冻结至 `0004`（只允许向前迁移）|
|测试|本轮后端全量1817通过、2项Windows文件链接权限跳过；其后新增6项报告校验与原17项OCR回归全部通过。浏览器40项为前轮记录，OCR未改前端|
|DeepSeek 余额|¥2.60（2026-10-12 查询）；配置模型 `deepseek-flash`|

## 2. 启动与常用命令（PowerShell，仓库根目录）

```powershell
# 本地（SQLite，无需 Docker 或密钥）
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -SkipInstall   # 前端 5188，API 8000
powershell -ExecutionPolicy Bypass -File scripts/stop-local.ps1

# 验证
.venv\Scripts\python.exe -m pytest -q                      # 后端全量，约 1.5–2 分钟
.venv\Scripts\ruff.exe check backend scripts datasets benchmarks
.venv\Scripts\python.exe scripts/generate_contracts.py --check   # 改了 API 后先去掉 --check 重新生成
cd frontend; npm run build
$env:PROCESS_DATASET_TESTS='1'; $env:STATEMENT_DATASET_TESTS='1'; npm run test:e2e   # 需先启动本地服务

# 容器（PostgreSQL/Redis/Celery）：8080 被本机其他程序占用，用 WEB_PORT
$env:DEEPSEEK_API_KEY=''; $env:WEB_PORT='8088'; docker compose up --build -d
.venv\Scripts\python.exe scripts/validate_containers.py --web-url http://127.0.0.1:8088/ --output <新报告>

# ERPNext 联调（镜像约 6.7GB，站点创建数分钟）
docker compose -f infra/erpnext/compose.yaml up -d
.venv\Scripts\python.exe scripts/validate_erpnext.py --output <新报告>
```

验收脚本拒绝覆盖已有报告，每次给新文件名；失败报告保留不删。

## 3. 代码地图

|位置|内容|
|---|---|
|`backend/app/main.py`|FastAPI 入口与账套、导入、快照、案件、模拟、复核、导出接口|
|`backend/app/domain.py`|确定性规则（16 条）与规则目录（含每条所需资料种类）|
|`backend/app/investigation.py`|LangGraph 调查图：规则/单 Agent/固定多 Agent/主动调查，提案契约与校验，`root_cause` 任务|
|`backend/app/model_gateway.py` / `model_contract.py`|DeepSeek 网关（预算、重试、用量）与系统协议文本|
|`backend/app/evidence_selection.py` / `evidence_planning.py`|主动调查的查询计划编译与选择|
|`backend/app/policy_*.py`, `investigation_policy.py`|官方问答政策规则、条件版本、调查时冻结的政策上下文|
|`backend/app/evidence_requests.py`|补证事项与人工确认（迁移 0004）|
|`backend/app/process_*.py`|BPI2019 流程证据、流程规则、流程模型解释|
|`backend/app/statement_*.py`|年报观察快照与只读接口|
|`backend/app/connectors.py`|ERPNext 总账只读导出、BPI XES 读取|
|`benchmarks/`|规则标签基准、`root_cause.py`（v1）、`root_cause_v2*.py`（v2 及模型运行器）、官方政策一致性、年报勾稽、费用估算|
|`datasets/`|合成账套生成器（`generate.py`、`generate_v2.py`）、官方问答、FinQA、BPI、年报采集|
|`scripts/`|启动、验收（容器、ERPNext、政策、知识）、汇总、备份恢复、`guard_paid_run.py`（付费运行费用守护）|
|`frontend/src/`|Vue 3 工作台；`components/` 下各功能区组件，`generated/contracts.ts` 由脚本生成，勿手改|
|`infra/erpnext/compose.yaml`|本地 ERPNext 测试服务|

## 4. 数据与运行资料位置

|位置|内容|是否入库|
|---|---|---|
|`docs/*.json`|公开摘要（政策验证、原因调查结果、年报勾稽、依赖清单、OpenAPI 等）|是|
|`.local/`|原始外部资料、合成数据集（`datasets`、`datasets-v2`）、全部实验与验收报告（`reports/`）|否（Git 忽略）|
|`runtime/`|本地 SQLite、上传原件、知识语料、流程模型运行记录、容器数据目录|否|
|`.local/datasets*/truth/`|评测真值|否；**运行时不得读取**|

本地 SQLite 中有多次浏览器测试和验收创建的合成账套（名称带“合成”“验收”等），属正常测试残留。

## 5. 必须遵守的规则（摘自 AGENTS.md 与本次经验）

- 金额用 Decimal/字符串；原始账簿只读；调查绑定快照；模型建议不自动变成业务事实。
- 不修改测试答案迁就实现。本次多次调整的都是**版本号、目录数量、测试替身或调用签名**，每次都在 STATUS 说明理由；遇到失败先查原因，确为真实缺陷才改代码。
- 规则变更须同时提交来源（URL+原页哈希）、适用/不适用条件、独立手写回归案例。
- 新增依赖要核对来源与许可证；ERPNext（GPLv3）只作为独立服务运行，不复制代码。
- **付费模型调用**：事先说明预计请求/Token/金额与硬上限并取得同意；只在官方低谷时段运行（工作日北京时间 09–12、14–18 以外，周末全天；以 DeepSeek 服务器 `Date` 头为准，本机时区标签曾显示错误）；用 `scripts/guard_paid_run.py` 包住批次；先小冒烟再扩大；不重复运行已知失败的配置；事后用余额接口核对实际花费并记入 STATUS。
- **数据划分**：v2 原因调查的验证集已被冒烟使用、封存集已最终评测一次，二者都不能再用于调参。新的提示或契约必须在新生成的数据上验证。

## 6. 已知陷阱

|现象|原因与处理|
|---|---|
|批次报告写入偶发 `PermissionError`|Windows 下其他进程读取时 `os.replace` 失败；`run_agents` 已加有界重试，监控脚本仍应尽量少读|
|`docker compose up` 后 API 起不来|旧版本无重启策略；现已 `restart: unless-stopped`；API 启动前执行 `alembic upgrade head`|
|Alembic 比对提示删除 `checkpoint*` 表|这些是 LangGraph 检查点表，已在 `alembic_include_object` 排除，切勿生成删除它们的迁移|
|ERPNext 手工凭证报 `StockAccountInvalidTransaction`|永续盘存下存货科目只能经库存业务更新，联调凭证改用费用科目|
|ERPNext REST 拒绝 `sum(...)` 字段|新版 Frappe 安全限制；独立核对改用试算平衡表报表|
|Compose YAML 中长命令被拆开执行|折叠块里更缩进的行保留换行，长命令写成一行|
|Git Bash 里 heredoc 含中文/引号时报 `unexpected EOF`|改为先写脚本文件再执行|
|C 盘空间|Docker WSL 数据盘在 `F:\UtilityTool\WSL\Ubuntu\DockerDesktopWSL`；C 盘曾不足 1GB，启动 Docker 前先看余量|
|本机 8080 端口被占|容器 Web 用 `WEB_PORT=8088`，ERPNext 用 8090|

## 7. 未完成事项（按建议顺序）

1. **原因调查v3真实模型验证**：窄契约、270例新数据、逐请求及人民币费用预留、冻结开发计划均已实现；还未调用模型。开发/验证确定性基线、新计划与命令见 [ROOT_CAUSE_V3](ROOT_CAUSE_V3.md)。开发冒烟预计¥0.50–0.90，保守高峰价预留上限¥1.20、60次请求，用户已明确“暂不付费，继续离线工作”，保留计划但不执行，也不反复询问费用。新封存集仍未运行，最终3次重复另行冻结与审批。
2. **官方问答离线扩展已完成一轮**：新增5条规则；剩余12条已逐条阅读并记录缺失材料与实现路径，见 [POLICY_RULE_PACKAGE](POLICY_RULE_PACKAGE.md)。暂不把专业判断自动化为事实。
3. **时间快照与模拟复核的效果测量**：已有4个时间挑战、6份草稿的离线机制消融，保留了“合法且平衡的错误业务用途仍能通过”反例，见 [ROOT_CAUSE_V3](ROOT_CAUSE_V3.md)。真实模型闭环与去反证实验仍未测。
4. **检索**：参考语料扩充后再接入 bge-small-zh 与 pgvector，并与关键词检索对比；当前 13 个片段不足以比较。
5. **OCR离线验收已完成**：6张合成图实跑，23/24字串、28/35整行，空白图正确；保留字母数字混淆错误。只输出待核实观察，尚无扫描PDF和字段确认页面，见 [OCR_VALIDATION](OCR_VALIDATION.md)。
6. **观测**：OpenTelemetry本地span与费用未知值处理已实现、真实规则调查验收通过；Langfuse和远端Collector未接入，见 [OBSERVABILITY](OBSERVABILITY.md)。
7. **需要外部条件**：企业授权真实账套试点、会计专业复核、多用户审签与社区发布。

## 8. 交接时的状态核对

```powershell
git status                      # .gitignore 排除 .local、runtime、.env；不提交本地来源或密钥
.venv\Scripts\python.exe -m pytest -q
cd frontend; npm run build
```

`.env` 含 DeepSeek 密钥，仅本机使用，不得提交或写入报告。
