# 参考检索的证据需求检查

更新：2026-10-22。新增候选证据需求检查及工作台入口，避免把高相似度当作足够的业务证据。该功能不输出自然语言答案，也不自动判断问题是否可回答；原关键词排序、人工选择条文和历史快照不变。

## 已实现的行为

检索完成后，工作台明确显示“未生成答案”。用户可以声明本次要查阅原文片语、核验企业事实、判断条文适用性，或者尚未明确需求。

|需求|程序行为|不能据此推断|
|---|---|---|
|查阅原文片语|仅在本次实际候选的完整父条文中做逐字查找；返回父条文 ID、Unicode 字符起止位置及父条文哈希|语义支持、法规有效性、事实成立或适用性|
|核验企业交易事实|列出原始合同、票据、交易记录、对应关系与人工核验等缺失材料|公开政策条文能证明某笔企业交易|
|判断条文适用性|要求账套制度、期间、交易事实以及适用条件和例外的专业复核|有同主题条文就可以给出会计处理结论|
|尚未明确需求|要求先明确核对目标|未完成的任务属于成功拒答|

所有结果的 `answer_status` 都是 `not_answered`。片语全部命中只得到 `manual_review`，其余为 `insufficient_evidence`。调用者把私人事实问题错误标成“查阅原文”也不会得到自动回答权限。分数不参与此判定。

新入口是 `POST /api/v1/reference-indexes/support-checks`。服务端先核验账套、期间、账套版本、资料修订号、检索结果摘要、候选内容及来源绑定。跨账套、过期或篡改结果被拒绝；检索执行失败单独保留，不算成功拒答。检查本身不追加参考选择，不建立快照，不写原始账目。人工勾选条文与确认适用范围仍是冻结新快照的必要步骤。

## 实际验证与边界

- 后端接口与原参考工作流：46 项隔离回归通过，包含候选检查→人工冻结→旧检索失效，跨账套、原文篡改、错误摘要、失败任务、部分覆盖、超长/空需求和 Unicode 坐标。
- [冻结协议](retrieval-scope-plan-v2.json) 的合成检查：开发分组 12/12，留出分组 20/20。两组预测全部写完后才读取标签；没有模型请求和账簿写入。来源、输入、标签及实现的哈希在运行前固定，输出目录拒绝覆盖。
- Vue 类型检查、生产构建及请求契约一致性检查通过。浏览器的参考 UI 用例使用明确标记的合成候选与拦截响应；真实服务的验证由上述后端接口测试承担，不将 UI 拦截算作 BGE 实跑。
- 浏览器端到端结果和证据路径见 [机器摘要](retrieval-scope-validation-v2.json)。可选 BGE/pgvector 路径未在本轮重跑。

这些数据由实现者手写，`heldout` 只是协议中的分组名称，不是独立专家数据或未见业务问题。32/32 是合同边界与引用定位回归结果，**不能报告为语义拒答准确率 100%**。

[v1 阈值实验](RETRIEVAL_ABSTENTION_RESULTS.md) 中无答案误接纳 2/12 的负面结果继续有效。自然语言问题的可靠拒答、独立真实用户问题和条文专业适用性仍未验收。v2 的作用是让产品明确暴露这一限制，并为人工核实提供具体缺证提示。

## 复现

所有命令都创建新报告；已存在的目录不能复用。

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_reference_scope.py backend/tests/test_reference_workflow.py -q
.venv/Scripts/python.exe -m benchmarks.reference_scope_v2 --plan docs/retrieval-scope-plan-v2.json --output .local/retrieval-scope-v2-new
.venv/Scripts/python.exe scripts/generate_contracts.py --check
.venv/Scripts/python.exe scripts/validate_reference_ocr_browser.py --output .local/reference-ocr-browser-new --fixture .local/ocr-wrapped-v1/images/wrapped-description.png --ocr-python .local/ocr-env/Scripts/python.exe --models .local/ocr-models
```

最后一条命令还检查 4 个 OCR 表格场景，需要已准备的固定本地 OCR 运行时、模型与合成图像。验收器使用全新 SQLite、空模型密钥和随机端口，结束后停止自己启动的 API/Vite。没有新增依赖。

## 2026-10-23 快照竞态修复

复核发现：证据需求检查尚未返回时，另一客户端补资料并刷新当前工作台，原监听器因 `busy` 跳过快照变化，旧选择与迟到的检查结果仍可能显示。现改为账套或快照改变即中止旧请求、清除选择与检查结果、重载历史；旧异步响应由版本号隔离。新资料不再被“正在处理”的界面状态挡住失效事件。

新增浏览器用例用真实隔离 API 建立新快照，并延迟合成 support 响应：修复前 3 通过、1 失败（旧选择未清除）；修复后 **4/4 通过**，无跳过、flaky 或页面测试失败，没有追加参考选择。失败与成功分别存于 `.local/reference-snapshot-race-20261023-before`、`.local/reference-snapshot-race-20261023-after`，自建服务均已停止。支持性响应仍是明确的测试拦截，不计 BGE 或语义拒答验收。生产构建重新通过。

仅复测参考界面时，可在最后一条命令附加 `--reference-only`；完整模式现在包含 4 项参考界面与 4 项 OCR 用例。本次没有重跑未改变的 OCR 用例，也不把两次浏览器运行合并成一次 8/8 实跑。
