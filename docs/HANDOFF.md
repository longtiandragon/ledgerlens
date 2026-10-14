# 交接文档

更新：2026-10-14。本文给接手的开发者或编程 Agent 使用：先读这里，再按需查 [STATUS](STATUS.md)（逐项验证记录）、[GOALS](GOALS.md)（目标与验收门槛）和 [AGENTS.md](../AGENTS.md)（必须遵守的协作规则）。

## 1. 一句话现状

账证智核（LedgerLens）是本地运行的会计复核研究与辅助平台：导入账套 → 固定快照 → 确定性规则检查 → 证据调查（规则或 DeepSeek 模型）→ 隔离模拟 → 复核底稿，另有官方政策检查、BPI 流程证据、年报勾稽和 ERPNext 只读接入。工程主链路已完成分阶段验证，当前基础复现结果和跳过范围见下表；**核心研究主张（模型＋主动补证优于同预算确定性方法）在本项目两次自建封存测试（v2、v3）上均未获支持**，见 [原因调查轨道](ROOT_CAUSE_TRACK.md)。

|项|当前值|
|---|---|
|产品版本|0.1.0（本地工程与仿真版本；2026-10-14起推送到用户同意的私有GitHub远端 `origin`，main已按用户要求快进合并，CI通过；未公开发布）|
|调查提示/工具版本|`investigation-v27` / `snapshot-tools-v11`；root_cause窄契约（v27只改JSON解析与修复提示）；v3封存集单Agent 0.508、0/27结构失败，未超过基线，见ROOT_CAUSE_V3|
|政策规则包|`policy-consistency-v5`，33 条（CAS 31 / SCAS 2）覆盖全部32份归档问答，191 个手写投影全部通过；专业判断须人工确认|
|流程规则包|`bpi2019-process-rules-v1`，9 条；流程模型解释 `process-investigation-v1`|
|数据库迁移|冻结至 `0007`（多用户审签：用户、会话、审批表与可空归属列，只追加；只允许向前迁移）；CI optional作业在真实PostgreSQL上迁移至head通过，0006及以前的本机记录见postgres-migration-validation.json|
|本地旧库升级|启动器执行结构核验、自动备份和向前迁移；无版本旧库仅接管完整匹配的冻结结构。30项专项及隔离启动器实跑通过，原工作库仅做副本演练，见LOCAL_DATABASE_UPGRADE|
|本地备份恢复|v2逐文件哈希与检查点清单，复制前后核验；32项专项通过，实际副本3257行业务数据和4179行检查点保持，199快照/26OCR/117参考上下文重验通过，见BACKUP_RESTORE。另建合成库两个规则调查进程中断点恢复、预算保持及重复派发通过，见RESTORED_RUN；非空白机器完整灾难恢复。Compose PostgreSQL用`scripts/postgres_backup.py`停写备份／新STORAGE_ROOT恢复，恢复栈API视图一致、容器验收20/20，见BACKUP_RESTORE末节|
|测试|最新：提交c9ac746的GitHub CI（全新ubuntu-24.04）后端2208通过/38可选跳过，480/30规则契约与空库网页42通过/10可选跳过（含原因调查评测面板），多用户签署浏览器流程1通过，optional作业68项0跳过；本机后端全量2221通过/25可选或本机条件跳过（191.6秒）。以下为历史记录：提交ce7caf2干净源码基础环境2131通过、52项可选/本机条件跳过；网页42通过、9项可选场景跳过，见CLEAN_CHECKOUT。此前启用实际PG/本机模型资料的后端全量2017通过、2项Windows文件链接权限跳过（144.31秒）；浏览器48通过、0跳过（4.4分钟），两种环境范围不同，不互相替代。GitHub CI（全新ubuntu-24.04）后端2170通过/38可选跳过、480/30规则契约及空库网页42通过/9可选跳过；optional作业以pgvector服务和固定Beancount执行默认作业跳过的31项（所在3个文件共68项全部执行、0跳过），见github-ci-validation.json及github-ci-optional-validation.json|
|DeepSeek 余额|¥5.79（2026-10-14 06:41 查询；当日付费实际¥6.79）；配置模型 `deepseek-flash`|

## 2. 本轮收尾与待决事项（2026-10-14）

### 研究结论（定稿，负面）

- 核心主张“模型＋主动补证优于同预算确定性方法”在两次独立封存测试中均未获支持。
- **v3封存集**：新数据v3、窄契约`investigation-v27`，9个从未使用的案例×3次。
  - 单Agent原因F1 0.508、结论70.4%、结构失败0/27。
  - 最强同预算基线是规则＋固定补证（与关键词补证并列），0.563／77.8%。
  - 差值−0.054，场景族自助区间−0.547至+0.400。
  - F1≥0.85、增益≥0.05、补证≥90%三项预注册目标均未达成。
- **v2封存集**：单Agent 0.438／38.9%，6/18输出失败。
- 窄契约消除了结构失败，剩余差距在推理：
  - 模型在靠文字线索的类别上强于基线（作废重开1.00对0，折让0.80对0）。
  - 在需要解读补证资料的类别上全错（错挂发票号、货物在途均为0对1.00）。
- **主动调查（计划×2）与固定多Agent（×1）封存运行被中断**：完成8/18、6/9样本时被Claude Code内存回收终止，未重启。原因是会话空闲加主机内存严重不足；等待时结束回合是操作失误。已完成样本只作描述，是线索而不是支持证据：
  - 主动调查：0.524／50.0%，2次取证计划结构失败。
  - 固定多Agent：0.800／100%，0失败，但每样本20.7次请求（单Agent 4.7次）。
- **证据**：
  - `benchmarks/root_cause_v3_partial.py`（记录在案的偏离）及报告`.local/reports/root-cause-v3-sealed-partial-analysis.json`。预注册合并分析`root_cause_v3_final.py`按设计拒绝中断报告，未修改、未运行。
  - [ROOT_CAUSE_V3](ROOT_CAUSE_V3.md)末节。
  - 评测中心摘要[root-cause-results.json](root-cause-results.json)。

### 用户点名的六项外部条件

|项目|状态|证据|
|---|---|---|
|剩余12份政策问答|已完成：`policy-consistency-v5`共33条人工确认条件规则，191/191手写投影通过，未实现0份|[POLICY_RULE_PACKAGE](POLICY_RULE_PACKAGE.md)、official-policy-validation.json|
|多用户审签|已完成：`AUTH_MODE=multi_user`，调查／复核／审批按不相容职务分离，命令行建户；CI另起多用户API，浏览器签署流程通过|[RUNBOOK](RUNBOOK.md)、STATUS“多用户审签”两节|
|专家查询|替代完成：11条财政部官方问答原题（实务人员与财政部撰写），真值取自官方答复点名的准则与条款。结果见下方注①|official-query-retrieval-validation.json|
|真实单据OCR|替代完成：CORD v2的100张真实收据（CC BY 4.0）。v1暴露缺陷：任一检测框识别为空就整张拒绝，40/100失败。修复后改为记录未识别区域并在复核页提示，v2结果见下方注②|cord-ocr-validation.json|
|真实账套|部分替代：俄克拉何马州政府公开总账（CC-BY，37.3万行），暴露cp1252编码与20条分以下尾数（`money()`拒绝而非舍入）。该导出缺负债／基金余额科目，复式平衡不可检验；企业账套仍须授权|public-ledger-validation.json|
|会计专业复核|材料已备：47项可打印复核材料与答卷，含33条政策规则、9个原因真值口径、5项模拟复核规则，预计60–90分钟；交回前仍记为未复核|`docs/review-kit/`|

注：

① 专家查询：关键词文档级前6命中10/11、BGE 11/11；条款级4/5对3/5；两法各胜2条，无可靠差异，默认检索不变。

② CORD OCR v2：失败0/100，字词2119/2356（89.9%），合计78/95，单价221/246；两次都成功的60张识别文字完全一致。

### 费用、合并与运行环境

- **当日DeepSeek付费实际¥6.79**，余额¥5.79（06:41查询）：
  - v26冒烟¥0.37。
  - v27冒烟¥2.26。
  - S1 ¥1.24。
  - 中断的S2+S3 ¥2.92。
- **main已按用户要求快进合并**，仓库保持私有、未公开发布。提交c9ac746的CI四项作业全部通过，测试数字见第1节。
- **其他项目的容器**已停止并设为不自动重启（未删除），Docker Desktop已关闭。

### 待用户决定

- **是否补跑v3主动调查／固定多Agent**：
  - 运行器不能续跑，只能按冻结计划整批重跑。
  - 只补多Agent约¥2；两批合计约¥5–5.5，余额¥5.79偏紧。
  - 须在低谷时段（工作日北京时间9–12、14–18以外，周末全天）。
  - 须关闭部分占内存程序，或以`CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1`启动Claude Code；运行期间不要结束回合。
  - 未经用户确认不得重跑。
- 补跑结果与本次中断记录须同时保留。v3封存集已用，任何新提示、契约或“规则＋模型”混合方法须在新生成数据上预注册检验。

### 用户可自行补齐的外部条件

- **中文真实票据**：网购下载的本人电子发票PDF就是合规的真实中文票据，产品可直接做PDF OCR与人工核实。
- **专业复核**：请会计专业老师或有CPA背景的同学填写`docs/review-kit/`中的答卷，交回后再记录结论。
- **企业账套**：需实习单位或亲友小店等提供经授权、已脱敏的账簿导出。

## 3. 启动与常用命令（PowerShell，仓库根目录）

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

## 4. 代码地图

|位置|内容|
|---|---|
|`backend/app/main.py`|FastAPI 入口与账套、导入、快照、案件、模拟、复核、导出接口|
|`backend/app/domain.py`|确定性规则（16 条）与规则目录（含每条所需资料种类）|
|`backend/app/investigation.py`|LangGraph 调查图：规则/单 Agent/固定多 Agent/主动调查，提案契约与校验，`root_cause` 任务|
|`backend/app/model_gateway.py` / `model_contract.py`|DeepSeek 网关（预算、重试、用量）与系统协议文本|
|`backend/app/evidence_selection.py` / `evidence_planning.py`|主动调查的查询计划编译与选择|
|`backend/app/policy_*.py`, `investigation_policy.py`|官方问答政策规则、条件版本、调查时冻结的政策上下文|
|`backend/app/retrieval_index.py` / `scripts/retrieval_index.py`|独立版本化pgvector持久索引、跨进程核验与管理CLI；尚未进入产品默认检索|
|`backend/app/reference_*.py` / `frontend/src/components/ReferenceIndexes.vue`|本地实验索引API、人工父条文选择、追加版本和快照冻结；默认关键词不变，见REFERENCE_WORKFLOW|
|`backend/app/ocr_workflow.py` / `ocr_api.py`|本地识别任务、原件校验、追加式人工字段复核与导入失效检查（迁移0005）；网页见OCR_WORKFLOW|
|`backend/app/evidence_requests.py`|补证事项与人工确认（迁移 0004）|
|`backend/app/auth.py` / `auth_api.py` / `manage_users.py`|可选多用户审签：账户、会话、职责分离与命令行建户（迁移 0007）|
|`backend/app/process_*.py`|BPI2019 流程证据、流程规则、流程模型解释|
|`backend/app/statement_*.py`|年报观察快照与只读接口|
|`backend/app/connectors.py`|ERPNext 总账只读导出、BPI XES 读取|
|`benchmarks/`|规则标签基准、`root_cause.py`（v1）、`root_cause_v2*.py`（v2/v3 模型运行器）、`root_cause_v3_final.py`（预注册合并分析）与`root_cause_v3_partial.py`（中断后的偏离分析）、官方政策一致性、年报勾稽、费用估算；外部替代：`official_query_retrieval.py`、`public_ledger_checks.py`、`cord_ocr_eval.py`|
|`datasets/`|合成账套生成器（`generate.py`、`generate_v2.py`、`generate_v3.py`）、官方问答、FinQA、BPI、年报采集、CORD收据抽取（`cord_receipts.py`）|
|`scripts/`|启动、验收（容器、ERPNext、政策、知识）、汇总、备份恢复、`guard_paid_run.py`（付费运行费用守护）、`build_review_kit.py`（专业复核材料）|
|`frontend/src/`|Vue 3 工作台；`components/` 下各功能区组件，`generated/contracts.ts` 由脚本生成，勿手改|
|`infra/erpnext/compose.yaml`|本地 ERPNext 测试服务|

## 5. 数据与运行资料位置

|位置|内容|是否入库|
|---|---|---|
|`docs/*.json`|公开摘要（政策验证、原因调查结果、年报勾稽、依赖清单、OpenAPI 等）|是|
|`.local/`|原始外部资料、合成数据集（`datasets`、`datasets-v2`、`datasets-v3`）、官方查询冻结集（`official-retrieval-v1`）、公开总账（`public-ledger`）、CORD收据（`cord-v2`）、全部实验与验收报告（`reports/`）|否（Git 忽略）|
|`runtime/`|本地 SQLite、上传原件、知识语料、流程模型运行记录、容器数据目录|否|
|`.local/datasets*/truth/`|评测真值|否；**运行时不得读取**|

本地 SQLite 中有多次浏览器测试和验收创建的合成账套（名称带“合成”“验收”等），属正常测试残留。

## 6. 必须遵守的规则（摘自 AGENTS.md 与本次经验）

- 金额用 Decimal/字符串；原始账簿只读；调查绑定快照；模型建议不自动变成业务事实。
- 不修改测试答案迁就实现。本次多次调整的都是**版本号、目录数量、测试替身或调用签名**，每次都在 STATUS 说明理由；遇到失败先查原因，确为真实缺陷才改代码。
- 规则变更须同时提交来源（URL+原页哈希）、适用/不适用条件、独立手写回归案例。
- 新增依赖要核对来源与许可证；ERPNext（GPLv3）只作为独立服务运行，不复制代码。
- **付费模型调用**：事先说明预计请求/Token/金额与硬上限并取得同意；只在官方低谷时段运行（工作日北京时间 09–12、14–18 以外，周末全天；以 DeepSeek 服务器 `Date` 头为准，本机时区标签曾显示错误）；用 `scripts/guard_paid_run.py` 包住批次；先小冒烟再扩大；不重复运行已知失败的配置；事后用余额接口核对实际花费并记入 STATUS。
- **数据划分**：v2 原因调查的验证集已被冒烟使用、封存集已最终评测一次；v3 封存集已于2026-10-14用于最终对比。三者都不能再用于调参。新的提示或契约必须在新生成的数据上验证。

## 7. 已知陷阱

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
|Docker Desktop 启动报 `dockerInference ... cannot be accessed by the system`|非正常停止后遗留的推理服务套接字 `%LOCALAPPDATA%\Docker\run\dockerInference`；在 Docker 未运行时删除该文件再启动（2026-10-14 经用户同意处理过一次）。正常 `docker desktop stop` 不会遗留|
|付费或长时间后台运行被突然终止|本机可用内存常不足1GB；Claude Code在会话空闲且内存严重不足时终止后台命令（2026-10-14两次，含v3封存运行）。运行期间不要结束回合，或以 `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1` 启动Claude Code；被终止后不自行重启，先报告|
|启动 Docker Desktop 会拉起其他项目容器|本机另有项目的容器带重启策略，会随 Docker 自动运行；验收结束后停止本项目容器，若会话前 Docker 未运行则 `docker desktop stop` 恢复原状|

## 8. 未完成事项（按建议顺序）

1. **原因调查**：v3开发冒烟、三份封存计划与单Agent封存对比已完成，单Agent仍未超过同预算确定性基线（见 [ROOT_CAUSE_V3](ROOT_CAUSE_V3.md) 末节）。主动调查与固定多Agent封存运行在完成8/18、6/9样本时被Claude Code内存回收终止，只作描述。补跑须用户同意：运行器无续跑，只能按冻结计划整批重跑；须低谷时段、保持会话活跃（等待时不要结束回合）或以 `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1` 启动Claude Code。v3封存集已用，固定多Agent或“规则＋模型”混合须在新生成数据上预注册检验。
2. **官方问答已全部实现**：v4新增5条；v5把其余12份问答建为13条人工确认条件规则（191/191），见 [POLICY_RULE_PACKAGE](POLICY_RULE_PACKAGE.md)。暂不把专业判断自动化为事实。
3. **时间快照与模拟复核的效果测量**：已有4个时间挑战、6份草稿的离线机制消融，保留了“合法且平衡的错误业务用途仍能通过”反例，见 [ROOT_CAUSE_V3](ROOT_CAUSE_V3.md)。真实模型闭环与去反证实验仍未测。
4. **检索离线对比及本地人工接入已完成**：独立语料6份原文94段、156窗口；20条人工查询中18条有答案，关键词前6命中16/18、BGE＋pgvector为15/18，两者对2条无答案查询仍返回候选。保留负面结果与2个502来源，不在本批查询调参。持久索引重启回放一致；本地API和网页现支持选择父条文并冻结新快照、恢复默认与历史选择查看，真实BGE查询及规则调查通过。默认关键词不变；评测中心已增加两批检索历史摘要、失败门槛和试验边界，刷新只读，损坏摘要隔离，见STATUS中的检索评测展示记录。新增48条手写查询的阈值实验在留出集均误接纳2/12无答案问题，未达门槛，见 [RETRIEVAL_ABSTENTION_RESULTS](RETRIEVAL_ABSTENTION_RESULTS.md)。独立专家查询、可靠无答案拒答、规模性能及专业复核仍未完成，见 [REFERENCE_WORKFLOW](REFERENCE_WORKFLOW.md)、[PERSISTENT_RETRIEVAL](PERSISTENT_RETRIEVAL.md) 和 [RETRIEVAL_VALIDATION](RETRIEVAL_VALIDATION.md)。
5. **OCR图像、扫描PDF及多记录人工核实已完成，有限表格候选已接入**：6图及其6页纯图像PDF均实跑，23/24字串、28/35整行，空白页正确；保留混淆错误。PDF原页/像素/坐标可回查，页数与像素预算预检、失败页保留。网页支持原件高亮、逐字段引用、1至100条人工记录、追加复核与整批导入快照；图像/单条PDF以及跨页两条记录的真实CPU闭环通过。修订使旧预览失效，任一来源冲突整批回滚。明确表头和单行单元格可按OCR坐标提取待核实字段，两张独立合成图分别验证三行12字段和不支持双金额列；不自动判定业务类型或入账。v2已修复同页后续不支持表头继承前表金额列的问题，76项回归及3个真实OCR网页场景通过，见 [OCR_HEADER_BOUNDARY](OCR_HEADER_BOUNDARY.md)。真实收据仅测了字面恢复（CORD v2，见cord-ocr-validation.json）；中文真实票据、复杂结构及通用字段提取仍未完成，见 [OCR_TABLES](OCR_TABLES.md)、[OCR_WORKFLOW](OCR_WORKFLOW.md) 与 [OCR_PDF_VALIDATION](OCR_PDF_VALIDATION.md)。
6. **观测**：OpenTelemetry本地span与费用未知值处理已实现、真实规则调查验收通过；Langfuse和远端Collector未接入，见 [OBSERVABILITY](OBSERVABILITY.md)。
7. **ERPNext有限场景差分已完成**：外币账户／本位币、多公司、库存21项；订单→部分收货→发票→部分付款21项；含税采购→部分退货24项。各自重复执行记录一致；测试税率不代表税法。保留日期覆盖失败账套，USD本位币仍拒绝，复杂税费、退款、取消、汇兑与真实业务未验证，详见 [ERPNEXT_SEMANTICS](ERPNEXT_SEMANTICS.md) 和 [ERPNEXT_TAX_RETURN](ERPNEXT_TAX_RETURN.md)。
8. **外部条件的替代与剩余**：多用户审签已实现（AUTH_MODE=multi_user，见RUNBOOK）；会计专业复核材料见docs/review-kit（待专业人员填写）；公开政府总账试点与官方问答专家查询见STATUS“外部条件的可行替代”；CORD真实收据OCR已实跑：v1严格校验把含空文字框的收据整张拒绝，40/100失败；修复后v2失败0/100，40张记录了共83个未识别区域。v2字词恢复2119/2356（89.9%），合计金额78/95，单价221/246（见cord-ocr-validation.json）。企业授权真实账套、专业复核结论与公开社区发布仍需外部条件。

本地交付已补做[干净源码复现](CLEAN_CHECKOUT.md)：最新从提交ce7caf2导出、不带本机资料/模型/密钥，以完整现有缓存重新安装、生成480规则案例并在空库跑网页。保留npm缺缓存和可选依赖测试夹具不完整的两次失败；修复后全流程通过。另补[WSL Linux核心子集](LINUX_CORE.md)：提交562f255导出，480规则、30参考例和68项匹配/备份恢复检查通过，无跳过；包含Windows因权限跳过的12项真实链接边界。仅重用五个锁定纯Python测试包。2026-10-14起由私有GitHub远端CI补足完整Linux依赖、后端、规则基准与空库网页及无缓存联网安装，见github-ci-validation.json。容器Dockerfile已补齐原因调查及4份数值评测公开摘要，按文件清单隔离读取的50项相关检查通过，见packaged-evaluation-validation.json；容器验收器已改为读取当前源码迁移头、预检失败即停止及报告路径独占创建，见container-runner-validation.json；停机演练异常退出时的自有服务恢复先以33项离线检查验证，见container-restoration-validation.json。Docker宿主启动错误（cross-platform-readiness.json）经用户同意清除遗留套接字后解决：当前镜像构建运行、20项验收连续两次、真实0003→0006升级旧行保持、两次真实中断后的worker/scheduler恢复均通过，见container-revalidation-validation.json；PostgreSQL停写备份与新目录恢复随后实测通过（postgres-backup-restore-validation.json）；强制终止、宿主崩溃、容器内长时间模型调查及在线／异机恢复仍未验证。

## 9. 交接时的状态核对

```powershell
git status                      # .gitignore 排除 .local、runtime、.env；不提交本地来源或密钥
gh run list --limit 5           # 私有远端 CI；推送前确认仍是私有仓库，公开发布须用户另行同意
.venv\Scripts\python.exe -m pytest -q
cd frontend; npm run build
```

`.env` 含 DeepSeek 密钥，仅本机使用，不得提交或写入报告。
