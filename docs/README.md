# 文档导航

本目录共 52 份文档，按读者分组如下。逐项验证记录以 [交付状态](STATUS.md) 为准；带日期的数字只代表当时那一版的结果。

## 第一次了解项目

|文档|内容|
|---|---|
|[项目首页](../README.md)|定位、启动方式与体验流程|
|[产品需求](PRD.md)|用户、工作流、验收与明确不做的事|
|[技术设计](TECHNICAL_DESIGN.md)|系统结构、数据不变量、调查执行与公共接口|
|[目标文档](GOALS.md)|三项技术主张、验收门槛与当前证据（含未达成项）|
|[实施路线图](ROADMAP.md)|分阶段交付与进入下一阶段的条件|
|[交付状态与验证记录](STATUS.md)|每次改动的实测结果、跳过项与付费运行记录|
|[交接文档](HANDOFF.md)|给接手开发者或编程 Agent：现状、命令、陷阱与待办|

## 运行、部署与复现

|文档|内容|
|---|---|
|[运行手册](RUNBOOK.md)|本地运行、Compose 部署、多用户与故障恢复|
|[本地数据库升级](LOCAL_DATABASE_UPGRADE.md)|启动前核验结构、备份与向前迁移|
|[备份与恢复](BACKUP_RESTORE.md)|本地与 Compose PostgreSQL 的备份恢复验证|
|[中断调查续跑](RESTORED_RUN.md)|恢复副本中的中断调查续跑|
|[本地运行观测](OBSERVABILITY.md)|OpenTelemetry 本地 span 与费用估计|
|[干净源码复现](CLEAN_CHECKOUT.md)|从提交或工作区导出源码，在独立目录重装验收|
|[WSL Linux 核心子集](LINUX_CORE.md)|Linux 核心子集复现|
|[公开资料闭环](PUBLIC_DATA_CLOSURE.md)|财政部公开样例的一条命令验收|
|[离线核验](OFFLINE_EVIDENCE_VERIFICATION.md)|复制后的验收包只用标准库核验|

## 会计规则与政策

|文档|内容|
|---|---|
|[政策规则包](POLICY_RULE_PACKAGE.md)|财政部归档问答的一致性规则、条件版本与边界|
|[调查中的政策检查](INVESTIGATION_POLICY_CONTEXT.md)|已保存政策检查如何冻结进调查|
|[AI 审阅 10-14](AI_ACCOUNTING_REVIEW_20261014.md)·[10-15](AI_ACCOUNTING_REVIEW_20261015.md)·[10-20](AI_ACCOUNTING_REVIEW_20261020.md)|AI 会计逻辑审阅发现的问题与整改；不等于专业人员意见|
|[专业复核材料](review-kit/)|47 项完整版与 12 项短版，待会计专业人员填写|

## 调查与模型评测（研究）

|文档|内容|
|---|---|
|[原因调查轨道](ROOT_CAUSE_TRACK.md)|评测设计、v2 封存结论（负面）|
|[原因调查 v3](ROOT_CAUSE_V3.md)|新数据、窄契约与封存对比（单 Agent 未超过基线）|
|[原因调查 v4](ROOT_CAUSE_V4.md)|确定性优先混合方法的预注册检验|
|[数据与验证](DATA_AND_EVALUATION.md)|合成数据、公开数据与专业边界|
|[v15](AGENT_PILOT_V15.md)·[v16](AGENT_PILOT_V16.md)·[v17](AGENT_PILOT_V17.md)·[v18](AGENT_PILOT_V18.md) 试跑|四组真实模型试跑及失败分析|
|[v19 按需证据](AGENT_CONTEXT_V19.md)|按需读取快照证据的实现与边界|
|[FinQA 数值组件](FINQA_COMPONENT_VALIDATION.md)|数值推理组件验证|

## 参考来源与检索

|文档|内容|
|---|---|
|[来源与内容绑定](REFERENCE_PROVENANCE.md)|参考语料的来源、哈希与历史快照|
|[参考条文人工选择](REFERENCE_WORKFLOW.md)|本地索引查询、人工选择与冻结|
|[持久化参考索引](PERSISTENT_RETRIEVAL.md)|可选 pgvector 持久索引|
|[检索离线对比](RETRIEVAL_VALIDATION.md)|关键词与 BGE 的对比（无提升）|
|[无答案阈值实验](RETRIEVAL_ABSTENTION.md)·[结果](RETRIEVAL_ABSTENTION_RESULTS.md)|拒答实验设计与未通过门槛的结果|

## OCR 与原件核实

|文档|内容|
|---|---|
|[合成扫描件验证](OCR_VALIDATION.md)|本地 PaddleOCR 验证|
|[扫描 PDF](OCR_PDF_VALIDATION.md)|逐页识别与原页定位|
|[人工核实与导入](OCR_WORKFLOW.md)|原件高亮、逐字段引用与导入快照|
|[表格候选](OCR_TABLES.md)|有限表格候选与人工核实|
|[表头边界修复](OCR_HEADER_BOUNDARY.md)|表头切换边界的缺陷与修复|

## 外部系统与公开数据

|文档|内容|
|---|---|
|[ERPNext 语义差分](ERPNEXT_SEMANTICS.md)·[含税退货](ERPNEXT_TAX_RETURN.md)|ERPNext 有限场景差分|
|[Beancount 差分](BEANCOUNT_DIFFERENTIAL.md)|基础余额与不平衡检查差分|
|[BPI2019 数据](BPI2019_DATA.md)·[适配设计](BPI2019_ADAPTER_DESIGN.md)|真实采购流程事件日志的接入|
|[年报数据](ANNUAL_REPORT_DATA.md)·[数字组件](ANNUAL_STATEMENT_COMPONENT.md)·[跨单元格标签](ANNUAL_SPLIT_LABEL.md)·[年报工作空间](STATEMENT_WORKSPACE.md)|30 份公开年报的来源与只读探查|

## 治理、许可与事故记录

|文档|内容|
|---|---|
|[项目治理](../GOVERNANCE.md)·[贡献指南](../CONTRIBUTING.md)·[行为准则](../CODE_OF_CONDUCT.md)·[安全策略](../SECURITY.md)·[维护者](../MAINTAINERS.md)|决策、合入、规则变更与 AI 辅助开发规则|
|[借鉴、依赖与许可](THIRD_PARTY.md)·[许可策略](license-policy.json)|第三方许可、例外与 CI 许可门控|
|[测试隔离事故](TEST_ISOLATION_INCIDENT_20261014.md)|10-14 测试误清工作库的经过、恢复与整改|

## 机器可读摘要

`docs/*.json` 是各次验收和实验的机器可读摘要：`*-validation.json` 为验收结果，`root-cause-*-plan*.json` 为付费运行前冻结的实验计划，`root-cause-results.json` 供评测中心展示，`dependencies-*.json` 为依赖许可清单，`openapi.json` 为接口契约。失败与中断的记录同样保留。
