# 交接文档

> 最新接续（2026-10-23，远端验收后）：[PR #57](https://github.com/longtiandragon/ledgerlens/pull/57) 已推送，提交 `2d42f97` 的[CI 9/9通过](https://github.com/longtiandragon/ledgerlens/actions/runs/39823414917)，四份跨主机／中断／有限负载报告已保存在[证据摘要](cross-host-restore-validation.json)。Linux与Windows后端各2649通过／39跳过，可选任务68/68；本地最新参考/OCR为8/8。学生公开资料首版的本轮工程整改可交付，PR仍为草稿、未合并。后续研究另开新计划：AI增益未证明，v5失败冒烟不得自动重跑或挪用封存预算；企业账套和真人会计签字属于未来验证。下面“未推送”等描述只代表当时状态，不得据此重做任务。

> 最新接续（2026-10-23）：用户已确认v5本轮总上限¥8，开发冒烟实际95次请求，8案因固定¥2费用预留不足未派发；封存门控拒绝，两个封存报告均不存在，不自动重跑或挪用预算。详见[执行记录](root-cause-v5-execution.json)与[总览](PUBLIC_GAP_CLOSURE_20261022.md)。参考界面另修复快照更新时的迟到响应竞态，修复前1项失败保留，修复后4/4通过。此次仅更新前端及报告，未重跑完整后端；本地分支未推送。

> 最新接续（2026-10-22）：先读[公开资料缺口整改](PUBLIC_GAP_CLOSURE_20261022.md)及[机器摘要](public-gap-closure-validation.json)。当前本地分支 `codex/public-validation-closure` 补齐检索需求检查、OCR摘要续行来源保存、ERP取消标志检查、幂等写锁修复及有限恢复负载验证；2630后端、7网页通过。v5冻结计划未付费运行，拟总上限¥8须另确认。下文较早“未公开／未提交／没有材料”等为历史盘点，不能当作当前状态；仓库已于10-20公开，但本轮分支尚未推送或获得远端CI。

接手顺序：先读本文顶部最新接续、[公开资料缺口整改](PUBLIC_GAP_CLOSURE_20261022.md)及[机器摘要](public-gap-closure-validation.json)，再按需查 [STATUS](STATUS.md)、[GOALS](GOALS.md)和 [AGENTS.md](../AGENTS.md)。以下第0节是2026-10-20形成的历史盘点；当时的仓库可见性、提交状态、启动方式及材料缺口不代表当前状态，日期和截止时间也不应按阅读时刻重新计算。

## 0. 历史盘点：比赛提交要求与当时计划（2026-10-20）

依据用户提供的大赛截图：报名与作品提交、赛道一／二评审维度、“开源基础软件与解决方案”赛道说明。联网未检索到该赛事的公开页面，以截图和组委会确认邮件为准。本项目对应该赛道“行业应用平台与数据平台”和“AI开发工具链与智能体框架”两个方向。本节在核对Git状态、私有远端、仓库文件及`policy-v9-validation.json`后写成；未重跑测试，未调用模型。

距截止还有11天。工程主链路可运行，验证记录充分；缺的主要是提交材料（公开仓库、介绍PDF、演示视频），以及开源治理和团队层面的证明。

### 0.1 提交物与当时状态

|要求|形式与时间|当前状态|缺口|
|---|---|---|---|
|报名|官网报名表，组委会审核后邮件确认，按邮件指引提交；面向单位主体（企业、高校实验室、科研机构、开源社区、行业联合体），团队8人以内，明确负责人1名|是否已报名、以哪个单位报名均未知；仓库只有1名提交者|用户确认报名状态和确认邮件，定下参赛单位、负责人和成员名单|
|代码仓库链接|包含完整可运行的代码|GitHub私有仓库`longtiandragon/ledgerlens`，main与工作分支都停在`ebc0595`（10-14，政策v5）。10-14至10-20的工作未提交：25个修改文件（+628/−113行）、37个新文件（约5700行），包括政策v5→v9、财政部样例闭环、公开验收入口、离线核验器和测试隔离整改，远端CI没有覆盖。没有Issue、PR、Git标签和Release|①提交并推送私有远端，确认CI通过；②用户决定公开方式（GitHub转公开，或镜像到Gitee／GitCode），评委打不开私有仓库；③公开前复核密钥、个人信息、第三方原件和56个提交的作者邮箱；④打`v0.1.0`标签并发布Release|
|作品介绍文档（推荐PDF）|项目背景、技术架构、应用场景、创新点|没有|新写；素材在PRD、TECHNICAL_DESIGN、GOALS、ROOT_CAUSE_V3、POLICY_RULE_PACKAGE、PUBLIC_DATA_CLOSURE|
|作品演示视频链接|展示核心功能与使用流程|没有；仓库里没有任何截图、GIF或视频|写脚本、录屏，上传到评委无需登录即可观看的平台|
|打包发送|10月31日24:00前，材料作为附件发至bjoscc@oschina.cn|未开始|建议10月29日前发出，留出返工时间|
|线下总决赛（入围后）|11月13日，北京亦庄通明湖会展中心；结果次日公布|—|入围后准备路演PPT、讲稿和行程|

本次只读清查：已跟踪和未跟踪文件中只有`backend/tests/test_investigation_smoke.py`含测试用假密钥，未发现真实密钥；`.env`、`.local/`、`runtime/`均被忽略。公开前须再查一次。

### 0.2 按评审维度的差距

|维度（权重）|已有支撑|主要缺口|
|---|---|---|
|技术创新（30%）|证据优先：原账只读，调查绑定快照，模型建议不自动成为事实，调整只进隔离模拟并由程序复算，新资料使旧复核失效；33条财政部问答政策规则带原文哈希和人工确认条件；预注册封存评测和付费守护；失败与中断如实保留|核心主张“模型＋主动补证优于同预算确定性方法”在v2、v3封存测试中均未获支持（v3单Agent 0.508／70.4%，最强基线0.563／77.8%）。评审写明“简单堆砌现成工具得分较低”，而LangGraph、FastAPI、PaddleOCR、BGE、pgvector都是现成组件。缺同类方案对比。介绍文档应把创新写成“可验证、不越权的AI会计复核框架”和评测方法本身，不写模型增益|
|场景落地（30%）|无密钥、SQLite即可完成规则演示；Compose（PostgreSQL／Redis／Celery）20项验收两次通过；私有CI在全新Linux通过；本机后端2450通过／25跳过，公开API45项、Chrome24项通过；财政部数电发票样例端到端；公开真实数据（俄州政府总账37.3万行、CORD收据100张、BPI2019、30份年报）|无企业试点；无真人会计复核（`docs/review-kit/`47项待填）；中文真实票据OCR未测；启动脚本只有PowerShell；他人在另一台机器按README复现未验证；需求真实性没有用户访谈或反馈|
|开源治理（20%）|Apache-2.0；THIRD_PARTY及Python、前端、OCR、检索四份依赖许可清单；CONTRIBUTING、缺陷模板、PR模板；CI四项作业；CHANGELOG|仓库未公开；全部提交直推main，没有Issue或PR记录；缺CODE_OF_CONDUCT、SECURITY、GOVERNANCE或MAINTAINERS、功能请求模板和里程碑，只有默认标签；CHANGELOG停在10-14；README首屏是版本流水，docs下51份Markdown缺少面向读者的导航|
|长期发展（20%）|ROADMAP按验收门槛分阶段；GOALS列出下一阶段目标和所需外部条件|没有团队、分工和治理结构说明；路线图没有时间表；没有组织或社区支持说明（指导老师、实验室、会计专业合作）；没有维护承诺|

### 0.3 截止前计划（10-20至10-31）

必须完成，缺一项即无法有效提交：

1. 确认报名状态；提交要求以组委会邮件为准。定下参赛单位、负责人和成员名单。
2. 提交10-14以来的工作并补CHANGELOG，推送私有远端，确认CI四项作业通过。私有推送和快进main已获授权，提交仍等用户指示。
3. 用户决定公开方式和时间。公开前复查密钥、个人信息、第三方原件和提交元数据。
4. 写作品介绍PDF：背景与需求、架构图、核心流程、应用场景、创新点、验证结果（含负面结果和边界）、开源治理、路线图与团队。
5. 录演示视频并上传：建议5–8分钟，规则模式即可，不需要付费模型。
6. 10-29前打包发送，并在无痕窗口确认所有链接可打开。

建议做，成本低：

7. 面向评委改写README首屏：一句话定位、启动方法、截图或GIF、架构图、结果摘要；版本流水移到STATUS和CHANGELOG。
8. 增加Linux／macOS启动脚本，或把`docker compose up --build -d`写成首选路径；请同学在另一台机器按README从零复现并记录结果。
9. 补治理文件：CODE_OF_CONDUCT、SECURITY、GOVERNANCE或MAINTAINERS、功能请求模板和标签；把第8节未完成事项拆成公开Issue和里程碑；此后改动走PR；发布v0.1.0。
10. 请会计专业老师填写`docs/review-kit/`（约60–90分钟），结论原样记录。吸收会计专业成员或指导老师，可同时补强“场景落地”和“长期发展”。
11. 写同类方案对比（通用大模型问答、ERPNext／Odoo等ERP、Beancount纯文本记账、商业月结产品），定稿创新点表述。

可选，截止前不建议做：

12. 补跑v3主动调查和固定多Agent封存批次：须用户同意，按冻结计划整批重跑，低谷时段；只补多Agent约¥2，两批约¥5–5.5，余额约¥5.14（10-14）；还须关闭占内存程序或禁用回收。多Agent每样本约20.7次请求（单Agent 4.7次），即使分数高也不满足同预算条件。
13. 用本人电子发票PDF实测中文真实票据OCR，原件不入库。
14. 部署只含合成数据、规则模式的在线体验实例；涉及费用和公开访问，须用户决定。

建议排期：

|日期|事项|
|---|---|
|10-20～10-21|确认报名；提交推送并看CI；决定公开方式；补治理文件|
|10-22～10-24|改写README；画架构图、截图；写介绍PDF初稿与同类对比；联系会计老师填写复核材料|
|10-25～10-27|视频脚本、录制、上传；同学按README复现|
|10-28|公开仓库终检，链接、PDF、视频逐项核对|
|10-29|发送邮件；10-30～10-31留作缓冲|

### 0.4 介绍文档与视频的口径

- 可以写：证据可回查，原账只读，快照固定，新资料使旧复核失效，专业判断由人工确认，处理建议由程序复算，公开资料端到端可复现；测试数字须注明日期和范围。
- 不能写：模型或多Agent优于确定性方法；覆盖全部会计准则；可以替代会计；已在真实企业取得效果；Astra等AI审阅等同会计师意见；v3多Agent的0.800是结论。
- 演示建议：README“体验完整流程”7步；“账套与政策”页的政策检查；财政部数电发票样例上传、重复检测、模拟恢复和底稿导出；评测中心如实展示负面结果。

### 0.5 执行进度（2026-10-20 晚，按已批准的评分维度补强计划）

计划原文在本机 `C:\Users\20987\.claude\plans\humming-puzzling-harbor.md`。用户决定：GitHub 转公开（终检后，执行前再确认一次）；全部提交保留学校邮箱；按单人维护呈现；做约 ¥2–3 的混合方法实验（总硬上限 ¥3.00）。

|计划项|状态|证据|
|---|---|---|
|0.1–0.2 提交积压工作；CI 只在 PR 与 main 上运行|完成：7 个主题提交经 PR #1 合入|#1|
|0.3 公开前全历史审计|完成：843 个对象、0 阻断，报告 `docs/publication-audit.json`；转公开前再生成一次|#37、材料 PR|
|3.1–3.2 治理文件、Issue 表单、PR 模板|完成：社区健康度 71%→100%|#2|
|3.3 许可门控、Dependabot|完成；Dependabot 首批 7 个 PR 已合并|#3、#4–#10|
|3.4 标签、里程碑、路线图 Issue|完成：15 个标签、4 个里程碑、22 个 Issue（#12–#33）|—|
|3.4 转公开、Discussions、分支保护|完成（10-20 21:2x，用户最后确认后执行）：仓库公开；开启 Discussions（招募帖 #45）、私密漏洞报告和主题标签；main 分支保护要求 PR 与 8 项 CI 检查，管理员同样受限，禁止强推|审计报告 862 个对象、0 阻断|
|2.1 跨平台启动与 CI|完成：quickstart-unix、quickstart-windows（含 Windows 后端全量测试）、docker|#34；修复 #40、#41|
|2.2 README 首屏改写、文档导航|导航完成（#36）；首屏改写待合并|#36、#42|
|2.3 截图|完成：9 张，由 `scripts/capture_demo.py` 在隔离环境生成|#39、#42|
|2.6 AI 模拟首次使用试用|完成（2026-10-22）：演示流程走通，自制凭证导入失败，经 #52–#54 修复后从新的克隆复测通过；见 docs/AI_FIRST_USE_TRIAL.md。复测命令为 `node scripts/first_use_walkthrough.cjs <ui_url>`，须在隔离或全新实例上运行|#52、#53、#54|
|2.5 短版专业复核材料|完成（12 项）；**待用户转发给老师**|#35|
|1a 混合方法|预注册（#12、ROOT_CAUSE_V4）→开发冒烟通过（17 次请求，实际 ¥0.13，0 结构失败）→两份封存计划已冻结→**封存运行完成（2026-10-22 00:26–00:48）**。结果为负：hybrid 0.913，低于关键词补证 1.000（T1、T2 未达成），高于单 Agent 0.544（T3–T5 达成），不做产品化。实际花费约 ¥1.20，v4 合计 ¥1.33|#11、#38、本 PR|
|1b 架构图、需求论证、同类对比|架构图完成；官方来源与同类资料已检索并写入介绍文档草稿|#42、材料 PR|
|5 介绍 PDF、演示视频|PDF 草稿 12 页、视频初版 3 分 40 秒（离线合成旁白，用户决定正式版也用合成语音并注明）；封存结果已写入模板与旁白，待重建定稿|#44|

本轮发现并修复的问题：
- `stop-local.sh` 不等进程退出就返回，CI 的端口检查偶发失败（#40）。
- 遥测汇总在 Windows 时钟分辨率下可能把较早的 lease 当成最新（#41）。
- 复核答卷 CSV 中两条规则标题未随 v8/v9 更新（#35）。
- 审计脚本把项目自己的截图当成第三方原件（材料 PR）。
- CI 浏览器用例偶发超时，两次分别出现在不同用例，记为 #33，尚未根治。

已知陷阱（补充）：
- 付费运行要在 PowerShell 中用反斜杠路径启动守护脚本；在 Git Bash 中用正斜杠相对路径会因 WinError 2 启动不了子进程，不发出请求。
- 余额接口在运行后数分钟内可能未更新，记录实际花费前要复查。
- 冻结计划等 JSON 由 Python 在 Windows 上写出时是 CRLF，公布哈希前先改为提交后的 LF 字节。

## 1. 一句话现状

账证智核（LedgerLens）是本地运行的会计复核研究与辅助平台：导入账套 → 固定快照 → 确定性规则检查 → 证据调查（规则或 DeepSeek 模型）→ 隔离模拟 → 复核底稿，另有官方政策检查、BPI 流程证据、年报勾稽和 ERPNext 只读接入。工程主链路已完成分阶段验证，当前基础复现结果和跳过范围见下表；**核心研究主张（模型＋主动补证优于同预算确定性方法）在本项目两次自建封存测试（v2、v3）上均未获支持**，见 [原因调查轨道](ROOT_CAUSE_TRACK.md)。

范围（2026-10-14起有效）：用户明确只能使用公开资料，当前交付按[公开资料可复现闭环](PUBLIC_DATA_CLOSURE.md)推进；真实企业试点、真人专业签字是未来外部验证，不作为本阶段开发前置条件。AI复核不冒充真人意见，原模型指标及负面结果保留。当前规则为policy-consistency-v9，最新验收见[本轮摘要](policy-v9-validation.json)；下文旧版本和测试数字为历史证据。

|项|当前值|
|---|---|
|产品版本|0.1.0（本地工程与仿真版本；2026-10-14起推送到用户同意的私有GitHub远端 `origin`，main已按用户要求快进合并，CI通过；未公开发布）。10-14至10-20的积压工作已于10-20经PR #1合入main；此后的改动都经PR与CI合入；2026-10-20经用户确认转为公开，main受分支保护|
|调查提示/工具版本|`investigation-v27` / `snapshot-tools-v11`；root_cause窄契约（v27只改JSON解析与修复提示）；v3封存集单Agent 0.508、0/27结构失败，未超过基线，见ROOT_CAUSE_V3|
|政策规则包|`policy-consistency-v9`，33 条（CAS 31 / SCAS 2）覆盖全部32份归档问答，191 个手写投影在明确范围假设下全部通过；专业判断须人工确认|
|流程规则包|`bpi2019-process-rules-v1`，9 条；流程模型解释 `process-investigation-v1`|
|数据库迁移|冻结至 `0007`（多用户审签：用户、会话、审批表与可空归属列，只追加；只允许向前迁移）；CI optional作业在真实PostgreSQL上迁移至head通过，0006及以前的本机记录见postgres-migration-validation.json|
|本地旧库升级|启动器执行结构核验、自动备份和向前迁移；无版本旧库仅接管完整匹配的冻结结构。30项专项及隔离启动器实跑通过，原工作库仅做副本演练，见LOCAL_DATABASE_UPGRADE|
|本地备份恢复|v2逐文件哈希与检查点清单，复制前后核验；32项专项通过，实际副本3257行业务数据和4179行检查点保持，199快照/26OCR/117参考上下文重验通过，见BACKUP_RESTORE。另建合成库两个规则调查进程中断点恢复、预算保持及重复派发通过，见RESTORED_RUN；非空白机器完整灾难恢复。Compose PostgreSQL用`scripts/postgres_backup.py`停写备份／新STORAGE_ROOT恢复，恢复栈API视图一致、容器验收20/20，见BACKUP_RESTORE末节|
|测试|远端CI（10-20起每个PR与main）共8项作业：Linux后端全量、可选依赖、许可门控、前端构建、quickstart-unix、quickstart-windows（含Windows后端全量，最近一次2527通过/39跳过）、docker、reproduce（浏览器端到端，偶发超时见#33）。最新本机（2026-10-20）：后端2450通过/25跳过，公开API45项、Chrome24项全部通过，验收门控56项、191政策投影通过，见policy-v9-validation.json。最近一次同机新目录重装是10-15的v8：后端2364通过/53跳过，空库网页43通过/11跳过，见public-checkout-v3-validation.json。最近远端CI：提交c9ac746的GitHub CI（全新ubuntu-24.04）后端2208通过/38可选跳过，480/30规则契约与空库网页42通过/10可选跳过（含原因调查评测面板），多用户签署浏览器流程1通过，optional作业68项0跳过；本机后端全量2221通过/25可选或本机条件跳过（191.6秒）。以下为历史记录：提交ce7caf2干净源码基础环境2131通过、52项可选/本机条件跳过；网页42通过、9项可选场景跳过，见CLEAN_CHECKOUT。此前启用实际PG/本机模型资料的后端全量2017通过、2项Windows文件链接权限跳过（144.31秒）；浏览器48通过、0跳过（4.4分钟），两种环境范围不同，不互相替代。GitHub CI（全新ubuntu-24.04）后端2170通过/38可选跳过、480/30规则契约及空库网页42通过/9可选跳过；optional作业以pgvector服务和固定Beancount执行默认作业跳过的31项（所在3个文件共68项全部执行、0跳过），见github-ci-validation.json及github-ci-optional-validation.json|
|DeepSeek 余额|¥30.28（2026-10-22 00:49只读查询：赠送¥25.14＋自充¥5.14，扣费先从赠送余额走）；v4开发冒烟¥0.13、封存两批约¥1.20；配置模型 `deepseek-flash`。历史：10-14 17:05为¥5.14，较06:41少¥0.65，差额不能直接归为事故账单|

### 近期接续记录（倒序）

- **2026-10-20 离线核验**：`scripts/verify_public_delivery.py`支持复制后的公开验收包离线核验；88项专项通过，真实22份证据/9份附件在禁止访问旧目录的只读探针中通过，篡改副本拒绝。仅核验保存证据，不重演Chrome/API，不证明报告真实执行或专业业务结论。见[说明](OFFLINE_EVIDENCE_VERIFICATION.md)及[摘要](offline-evidence-validation.json)；业务规则仍为v9。
- **2026-10-20 政策v9**：当前政策v9，运输只核对人工确认的本期随收入摊销列示；简化租赁须明确排除同笔已确认租赁负债本金/利息，混合付款要求拆分。Astra两项P2已限定复核关闭。完整后端2450通过/25跳过，API45、Chrome24全部通过，验收门控56通过；372份源码/配置和22份证据核验、前端构建/契约/Ruff均通过。工作库哈希不变，无新增业务模型请求。使用[最新摘要](policy-v9-validation.json)和[整改记录](AI_ACCOUNTING_REVIEW_20261020.md)；下方v8新目录重装、旧CI及模型效果均保留原版本边界。
- **2026-10-15 借款网页增量**：新增4项真实借款政策网页流程，公开验收入口现要求至少20项网页全部通过；最新实跑2份投影、API45、Chrome20、门控专项21全部通过。下载底稿、快照失效与原账不变均核对，工作库哈希不变，无新增模型请求。见[增量摘要](borrowing-live-validation.json)。下方完整新目录复现是此前快照，不能将新增20项网页成绩冒记到旧报告。
- **2026-10-15 政策v8**：政策v8仅对人工确认、直接归属该设备的专门借款利息执行时期规则；辅助费用等退出本规则。Astra发现并复核关闭这项P2，见[AI复核](AI_ACCOUNTING_REVIEW_20261015.md)。最终新目录复现473份源码，后端2364通过/53跳过，空库网页43通过/11跳过，公开样例API45/网页16全部通过；191政策投影、480/30规则、契约/构建/Ruff通过。服务停止，工作库哈希不变。使用[本轮摘要](public-checkout-v3-validation.json)，不要把下文旧版本数字当当前重跑。测试隔离事故的恢复限制仍保留。
- **2026-10-14 公开总账v2**：公开总账校验为`public-ledger-checks-v2`：原文净额与产品可接收子集分开；20条分以下金额合计3,452,227.220 USD，保留来源观察且拒绝入账。全量373,357行重算、15项独立有理数核对、26项专项回归通过。科目齐全或净额为零也不能证明完整性；所有非零分组原因未核实。[本轮证据](public-ledger-v2-validation.json)与[复现说明](PUBLIC_DATA_CLOSURE.md)已保存，旧v1报告不覆盖。

## 2. 本轮收尾与待决事项（2026-10-14）

后续取证：备份中的4179行检查点/写入均逐字段保持，新增只涉及事故时段两个运行；查过24份本地账库候选，未发现可确认补回更晚工作数据的副本。意外运行8次尝试中7次返回用量，第8次费用未知；17:05余额¥5.14，较06:41差¥0.65，不能据此归因。两个清表夹具现共用`conftest`会话库，并在清表前拒绝其他数据库地址。证据与测试见[后续验证](test-isolation-followup-validation.json)，备份后业务缺口仍不保证为零。

最新后续回归为后端2352通过/25跳过、专项48通过、Ruff通过；父进程传入工作库路径和假密钥的完整回归仍使用临时库，结束后工作库哈希与备份一致。本次无新增模型调用；网页保留上一轮版本证据。

优先阅读[测试隔离事故与恢复](TEST_ISOLATION_INCIDENT_20261014.md)：本轮新测试导入顺序错误曾清理工作库并触发已记录8次模型调用。工作库已从2026-10-13 19:48核验备份恢复3257行，受影响文件完整封存，备份后变更与新增费用未完全核实；不要说本轮零调用或无数据损失。测试现有收集前全局临时环境与非回环网络拒绝。

功能整改：服务器校验公开示例包原件/投影并自动保留教学标记，禁止非同名映射；网页已实跑篡改拒绝后原样导入。隔离修复后后端2345通过/25跳过，API45、网页16、专项59及隔离探针7全部通过，构建/契约/Ruff通过；完整测试后工作库哈希未改变。[验收摘要](public-import-validation.json)同时登记事故，上一轮464文件隔离快照不包含本轮代码。

最新独立目录复现：`validate_clean_checkout.py --working-tree --public-archive ...`封存464份当前源码并离线重装依赖。后端2295通过/53跳过、默认网页43通过/11跳过，另跑公开资料API45/Chrome16全部通过；契约、构建、480/30规则基准通过。运行后源码和日志核验一致。见[CLEAN_CHECKOUT](CLEAN_CHECKOUT.md)及[摘要](public-checkout-validation.json)。提交号`ebc0595`只代表基底，当前修改未提交；应结合源码清单和ZIP哈希复现，不冒用旧提交CI结果。

新增单命令入口`scripts/validate_public_delivery.py`：重新采集或读取固定官方ZIP→核验投影→全新SQLite API闭环→Chrome验收→证据哈希汇总。离线原件实跑两份样例、45项API、16项网页全部通过，41项专项测试通过，Ruff通过。见[复现说明](PUBLIC_DATA_CLOSURE.md)和[本轮摘要](public-delivery-validation.json)。这是当前工作目录验收，尚未提交或在远端CI执行；不能沿用旧提交的CI成绩代表新增代码。

新增公开资料里程碑：财政部两XML示例原5行分录及独立重复记账教学变体，API45/45、Chrome16/16、后端2271通过/25跳过；AI租赁审阅后补借款暂停边界。见[本轮摘要](public-data-closure-validation.json)及[复现](PUBLIC_DATA_CLOSURE.md)。Astra后续因工作区额度不足中断，当前未继续运行；不要把它登记为真人专业复核。


### 研究结论（定稿，负面）

- 核心主张“模型＋主动补证优于同预算确定性方法”在两次独立封存测试中均未获支持。
- **v4封存集（2026-10-22，确定性优先混合）**：hybrid 0.913／74.1%，单Agent 0.544／59.3%，最强基线关键词补证1.000／100%。hybrid−关键词补证为−0.088（区间−0.175至0），T1、T2未达成，T3–T5达成，不产品化。入选9例上关键词补证的满分来自案例抽取（完整90例为0.848，事后描述）。详见 [ROOT_CAUSE_V4](ROOT_CAUSE_V4.md)。
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
|付费运行在 Git Bash 中启动失败（WinError 2）|守护脚本用正斜杠相对路径启动子进程时 Windows 找不到程序；按 v3 写法在 PowerShell 中用反斜杠路径运行。失败时没有发出请求|
|冻结计划的公开哈希与仓库不符|Python 在 Windows 上写出的 JSON 是 CRLF，而仓库存 LF；公布哈希前先把工作副本改为提交后的 LF 字节|
|余额刚跑完没变|余额接口有几分钟延迟；记录实际花费前复查一次|
|CI 浏览器用例偶发超时|两次分别出现在不同用例，重跑通过；记为 #33，尚未根治，不要放宽超时或改断言|

## 8. 未完成事项（按建议顺序）

比赛提交相关事项见第0节；本节是产品与研究层面的未完成事项，截止前不必全部处理。

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

所有改动走“主题分支 → PR → CI 全绿 → squash 合并”，不直接推送 main（见 GOVERNANCE.md）。付费运行前先读第 0.5 节与 `docs/ROOT_CAUSE_V4.md` 的流程。
