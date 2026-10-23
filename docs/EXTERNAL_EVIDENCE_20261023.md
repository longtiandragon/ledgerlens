# 外部独立数据检索与实际验证

日期：2026-10-23。范围：学生首版的公开资料验证；不替代企业账套或会计师签署。保留 v4、v5 的负面结果。本记录不预设必须得到 AI 优势。

## 已完成：FinRCA 的外部规则基线复现

[FinRCA-AI-Bench](https://github.com/PratikGhawate/FinRCA-AI-Bench) 覆盖应付发票、采购、付款和银行对账，采用外部作者生成的数据及 MIT 许可证。固定源码版本与全部输入 SHA-256 见 [来源清单](external-finrca-source-pin.json)。这不是项目自己的生成器，也不是真实企业账套。

采用作者在 validation 上选定的 F01 参数（7 天、金额相似度 1.00），在 test 全部 439 案例上复算。先保存全部预测，再打开真值，用本项目独立的 Decimal 评分器核算；50 个弃答保留在分母。运行期拦截网络、外部进程和答案资料读取。没有调用付费模型。

|指标|实测|
|---|---:|
|全部案例|439|
|正确分类|373|
|弃答|50|
|准确率|0.8496583143507973|
|固定 16 类宏 F1|0.9517731696856624|
|任何方法相对此基线的最大 F1 增益|0.0482268303143376|

独立评分与作者公布的宏 F1 相差小于 1e-12。原项目要求增益至少 0.05；在**这个固定划分与这个比较基线**上，即使模型得到 F1=1，也无法达到。该结论是数学上界，不是模型实跑结果，也不能外推成“AI 没用”。不能为获得正结论而删除规则表现好的案例、改用较弱基线或事后修改目标。

原始预测、路由和完整报告保存在 `.local/external-research/finrca-audit-v1/`；公开摘要见 [复现结果](external-finrca-audit-v1.json)。源码中的隔离钩子用于审计已检查过的代码，不是运行不可信 Python 的安全沙箱。

复现命令（从仓库根目录；输出目录必须未存在）：

```powershell
.venv/Scripts/python.exe scripts/fetch_finrca_audit_sources.py --destination .local/external-research/finrca
.venv/Scripts/python.exe scripts/audit_finrca_baseline.py --source .local/external-research/finrca --output .local/external-research/finrca-audit-reproduction
```

注意：上游 benchmark_questions 含答案；只在准备阶段提取路由。case_entity_records 是按真值筛选的记录，不能直接当作模型可见证据。比较方法实际读取全量业务表，不读取该文件。公开测试集的预训练污染无法排除。

## 运行中：SIBR 全部 400 张测试票据

[SIBR 作者数据页](https://www.modelscope.cn/datasets/damo/SIBR) 与 [CVPR 2023 论文](https://openaccess.thecvf.com/content/CVPR2023/papers/Yang_Modeling_Entities_As_Semantic_Points_for_Visual_Information_Extraction_in_CVPR_2023_paper.pdf) 对应的原始文件已取得并核验官方哈希。使用官方 test 全部 400 张，不筛选易识别图片；其中包括中文票据和英文单据，不能称为“400 张中文发票”。600 张训练集与测试文件名无交集。

已在任何测试预测前固定 [评测协议](SIBR_OCR_PROTOCOL.md)，现有 PP-OCRv5 mobile CPU 模型正在顺序执行。原始图片、标注和输出只保留本地。完整预测结束后才允许读测试标注，失败仍计入分母。运行目录 `.local/external-research/sibr-ocr-full-v1/`，日志同名 `.log`。**本节尚无完成成绩，运行中不构成通过。**

测量对象是严格文字段恢复率及位置匹配，不是官方 SIBR 实体关系 F1，也不是复杂表格、金额归属、发票合规或原因调查 AI 增益。不能用 OCR 的成功替代这些缺口。

## 已完成：FinBalance 取得、去重与输入隔离

[FinBalance](https://github.com/Devansh1105/finbalance) 的公开多单据数据与项目文档调查方向有关。固定版本、数据 CC-BY-4.0 与代码 Apache-2.0 许可见 [来源清单](external-finbalance-source-pin.json)。它是外部合成场景，不能称为真实票据实测。

已取得 710 条 main 和 143 条 coverage，并在观察字段投影上计算指纹：**143 条 coverage 全部与 main 重合**，main 中仅 567 条不在 coverage。不能把这两份文件直接作为独立开发/测试集。作者另披露 main 中 39 条存在已知元数据问题，不能擅改原答案后声称官方测试通过。

`datasets/finbalance_projection.py` 用白名单投影可见输入；去掉预期分录、预期资产负债表、矛盾标签、场景元数据、文档角色、文件路径和原始案例/文档 ID。期初金额以 Decimal 读取并输出字符串。文档正文与标题仍可能有语义线索，因此结构隔离不等于证明绝无泄漏。

投影结果在 `.local/external-research/finbalance/main-observations-v2/`，状态是 `prepared_not_evaluated`。早期 v1 金额仍为 JSON 浮点数，已保留作准备阶段记录但不用于模型运行；v2 解决这一问题。**没有在这 710 条上运行项目模型，没有准确率或 AI 增益结果。** 如后续开展比较，需要先声明项目任务映射、确定性基线、独立划分、证据评分及新的完整预算；不得挪用 v5 未执行批次预算。

## 其他来源的筛选结果

|原始来源|适配性与本轮结论|
|---|---|
|[SCID](https://davar-lab.github.io/dataset/scid.html)|中文扫描发票相关；官方分享接口本次返回 File not found。未取得，不能算实测；不绕过访问限制。|
|[DocILE](https://docile.rossum.ai/)|定位与行项目提取相关；下载需要申请 token，本轮未取得。|
|[BenchRec](https://www.operartis.com/benchrec)|提供方称为脱敏真实对账数据；需要提交联系信息，本轮未取得，不冒充已验证。|
|[RecBench](https://huggingface.co/datasets/end-close/recbench)|外部合成三方对账数据；不等于真实企业账套，本轮未执行。|
|[FinDER](https://huggingface.co/datasets/Linq-AI-Research/FinDER)|金融问答检索相关；不能直接证明中文会计制度适用性或无答案可靠拒答，本轮未执行。|
|[FinancialAuditBench](https://github.com/modus-audit/financial-audit-bench)|多文件审计任务相关；与当前运行 Agent 契约尚未对接，本轮未执行。|
|[FinQA](https://github.com/czyssrs/FinQA)|项目已有组件验证；程序真值回放不能算新的 AI 推理证据。|
|[BPI 2019](https://research.tue.nl/en/datasets/bpi-challenge-2019/)|真实采购流程事件，项目已有；缺少本项目原因分类真值，不能替代独立 RCA 评测。|

检索范围不是互联网穷尽性证明。这里只把已取得、可固定版本、契约相关的资料推进到本地验证；论文中别人的模型成绩不算本项目成绩。

## 当前判断

新增证据暂未证明项目 AI 增益。FinRCA 复现给出了一个明确的基线上界；FinBalance 的输入准备发现并处理了结构泄漏与开发/测试重叠风险；SIBR 全量 OCR 尚在运行。没有新增企业或专业复核认证，也没有用这些工作覆盖原有 ERP、恢复或负载测试结论。
