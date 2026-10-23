# 外部独立数据检索与实际验证

开始日期：2026-10-23；最终核验：2026-10-24。范围：学生首版的公开资料验证；不替代企业账套或会计师签署。保留 v4、v5 的负面结果。本记录不预设必须得到 AI 优势。

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

## 已完成：SIBR 全部 400 张测试票据

[SIBR 作者数据页](https://www.modelscope.cn/datasets/damo/SIBR) 与 [CVPR 2023 论文](https://openaccess.thecvf.com/content/CVPR2023/papers/Yang_Modeling_Entities_As_Semantic_Points_for_Visual_Information_Extraction_in_CVPR_2023_paper.pdf) 对应的原始文件已取得并核验官方哈希。使用官方 test 全部 400 张，不筛选易识别图片；其中包括中文票据和英文单据，不能称为“400 张中文发票”。600 张训练集与测试文件名无交集。

已在任何测试预测前固定 [评测协议](SIBR_OCR_PROTOCOL.md)，执行现有 PP-OCRv5 mobile CPU 模型。全部预测保存后才读取测试标注，失败仍计入分母。原始图片、标注和输出只保留本地。[结果摘要](external-sibr-ocr-result-v1.json)记录源码、模型、输入、输出及接续哈希。

测量对象是严格文字段恢复率及位置匹配，不是官方 SIBR 实体关系 F1，也不是复杂表格、金额归属、发票合规或原因调查 AI 增益。不能用 OCR 的成功替代这些缺口。

原运行停在102/400，第一次接续停在175/400，均未评分、退出原因未确认。按[第一次接续](SIBR_OCR_CONTINUATION.md)与[第二次接续](SIBR_OCR_CONTINUATION_V2.md)保留原报告及预测；独立隐藏后台进程仅执行剩余图片。最终于2026-10-23 23:40（北京时间）完成，10月24日逐案复算核验。最终目录 `.local/external-research/sibr-ocr-continuation-v2/`。这是三进程接续结果，不是连续运行；未重跑任何已记录的成功或失败图片。

|原文件名前缀|图片数|执行失败|标注段数|文字及位置精确恢复|仅文字整段精确恢复|
|---|---:|---:|---:|---:|---:|
|eng|120|5|9601|3765（39.21%）|4178（43.52%）|
|med|240|1|16866|5826（34.54%）|6237（36.98%）|
|hot|40|0|2668|1564（58.62%）|1590（59.60%）|
|全部|400|6|29135|11155（38.29%）|12005（41.20%）|

6次执行失败均为超过现有图片像素上限，仍贡献530个未恢复标注段。其余394张返回待人工核实的观察结果，**返回结果不等于正确提取**。整段严格匹配会将错字、行合并/拆分及段边界差异计为不匹配，38.29%不能被称为字符准确率；本轮未测量字符错误率、金额归属或表格结构准确率。没有预注册产品达标阈值，因此不事后设线宣布通过。公开图片可能进入模型预训练，也不构成无污染的企业验证。

## 已完成：FinBalance 取得、去重与输入隔离

[FinBalance](https://github.com/Devansh1105/finbalance) 的公开多单据数据与项目文档调查方向有关。固定版本、数据 CC-BY-4.0 与代码 Apache-2.0 许可见 [来源清单](external-finbalance-source-pin.json)。它是外部合成场景，不能称为真实票据实测。

已取得 710 条 main 和 143 条 coverage，并在观察字段投影上计算指纹：**143 条 coverage 全部与 main 重合**，main 中仅 567 条不在 coverage。不能把这两份文件直接作为独立开发/测试集。作者另披露 main 中 39 条存在已知元数据问题，不能擅改原答案后声称官方测试通过。

`datasets/finbalance_projection.py` 用白名单投影可见输入；去掉预期分录、预期资产负债表、矛盾标签、场景元数据、文档角色、文件路径和原始案例/文档 ID。期初金额以 Decimal 读取并输出字符串。文档正文与标题仍可能有语义线索，因此结构隔离不等于证明绝无泄漏。

投影结果在 `.local/external-research/finbalance/main-observations-v2/`，状态是 `prepared_not_evaluated`。早期 v1 金额仍为 JSON 浮点数，已保留作准备阶段记录但不用于模型运行；v2 解决这一问题。**没有在这 710 条上运行项目模型，没有准确率或 AI 增益结果。** 如后续开展比较，需要先声明项目任务映射、确定性基线、独立划分、证据评分及新的完整预算；不得挪用 v5 未执行批次预算。

## 已完成：FinLongEval 外部拒答阈值迁移

[FinLongEval 作者仓库](https://github.com/valuesimplex/FinLongEval) 的公开样例有 80 条问题及部分原始 PDF。已取得并核对 Git blob 与 SHA-256，见 [来源清单](external-finlongeval-source-pin.json)。它不是完整 347 题。采用固定文件名规范化规则，11 条问题的文档无法匹配；保留为失败，不记为成功拒答。

[运行前协议](FINLONGEVAL_REFUSAL_PROTOCOL.md) 固定旧关键词/BGE 阈值、相同窗口和全部 80 条问题。实际解析 27 份 PDF、1516 页，构成 3429 个窗口；全部预测落盘后才读取问题类型评分。约 472 秒完成，状态为 `completed_with_failures`。本轮没有付费调用，也没有将证券报告注册为会计制度。已渲染检查首份来源 PDF 的首页，确认原始多栏排版；这不是全量表格结构核验。

|固定方法|作者陷阱题接纳/总数|陷阱题拒绝|其他题目接纳/总数|其他题目拒绝|文档缺失失败|
|---|---:|---:|---:|---:|---:|
|关键词旧阈值|9/10|1|48/70|11|11|
|BGE 旧阈值|9/10|1|43/70|16|11|

无阈值时，两种方法对全部 69 条可执行问题都返回候选。按协议的陷阱标签代理，两种阈值都只有 1/10 拒绝率；但**作者的“陷阱问题”不等同于逐题人工确认的“必须完全拒答”**。部分问题可以通过纠正错误前提作答，因此不能将 9/10 接纳率写成模型幻觉率或产品错误回答率。其他问题被接纳也不代表证据正确。

一个具体反例是 `fle-012`：询问某车企在 35–50 万元价格区间的高销量品牌；作者参考答案说明文档没有该区间的具体信息。关键词分数 0.388889、BGE 分数 0.648013，均越过旧阈值。它说明高相似度不足以证明问题所需细节存在；不是对当前生产制度范围校验的反例。

[公开结果摘要](external-finlongeval-refusal-v1.json) 保存模型、阈值、源码、输入与原始报告哈希。完整预测位于 `.local/external-research/finlongeval-refusal-v1/`。已由核验器重新计数；不在本批结果上调参或重新定义主指标。

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
|[CFQA](https://github.com/ygan/CFQA)|中文财报问答含无答案评测，仓库提供问题/答案而不提供配套年报原文；需要单独核对年报，本轮未执行。|
|[FinRAG-Bench](https://github.com/nsmofficial/finrag-bench)|README 明确说明成绩表为占位值；不作为已执行实验或性能证据。|

检索范围不是互联网穷尽性证明。这里只把已取得、可固定版本、契约相关的资料推进到本地验证；论文中别人的模型成绩不算本项目成绩。

## 当前判断

新增证据没有证明项目 AI 增益。FinRCA复现给出明确的基线上界；FinBalance输入准备发现并处理结构泄漏与开发/测试重叠风险；FinLongEval外部阈值迁移发现接纳缺少具体信息的问题的反例；SIBR全量OCR给出38.29%的严格文字及位置段恢复率。三项已执行实验的原始预测、逐项评分及哈希核验全部完成；核验通过表示证据一致，不表示能力达标。

本轮后端回归2682通过、25跳过；接续及核验专项另行通过，Ruff通过。没有新增付费模型请求、企业账套或专业签字，没有改动生产规则、测试答案或原有v5冻结文件。原有ERP、恢复、持续负载结论保持各自适用范围，不能用本轮结果覆盖它们。

后续研究需要新数据或明确隔离的新划分，以及事前固定的任务映射和完整预算；不能继续在这400张或80题上调参后称为独立验证。学生首版可以按辅助核实工具交付；“AI优于确定性基线”“可靠自然语言拒答”“真实票据自动准确入账”仍不能作为已证明的宣传。

`scripts/verify_external_evidence.py` 从保留的预测、真值及文件哈希复核结果。正在运行的实验返回 `pending`，不会为验证报告而提前打开测试标注。该核验只证明所保留字节的一致性，不证明历史执行、专业正确性或无污染。报告哈希记录运行当时原字节，换行转换后的副本不应被说成逐字节相同。
