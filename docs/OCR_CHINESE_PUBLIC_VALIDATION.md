# 中文公开票样与换行表格验证

2026-10-22。新增 `ocr-table-candidates-v3`：在完整表头及日期、金额均已识别的前提下，允许把紧邻且左对齐的摘要续行加入同一条待核实候选。每个摘要最多三段；全部原始文字框和证据 ID 保留。日期、金额、编号和交易对手不会跨行继承，跨页必须重新识别完整表头。

这次取得并实跑的是**官方公开空白票样**和**独立手写预期的合成表格**。未取得授权企业账套或真实已开具中文发票，不能据此宣布真实票据识别能力已完成。

## 来源与实际结果

|材料|来源与性质|本地实跑结果|
|---|---|---|
|中文增值税电子专用发票空白票样|[国家税务总局天津市税务局 PDF](https://tianjin.chinatax.gov.cn/uploadFiles/file/20201221/20201221113818_870.pdf)，文件 SHA-256 `122543cc1bad3376dbc9e80e5f2ac1ef4566fdd8e731e6c3e6deb35e3f097212`|PP-OCRv5 CPU、180 DPI，41 段观察；人工预录 15 个表单标签命中 14 个，漏识“税率”；没有生成业务记录|
|两条记录、首条摘要换行的扫描表格|`datasets/generate_ocr_wrapped.py` 创建的教学合成图；预期由脚本中的固定值独立给出，不由提取器产生|既有 OCR 预测在修复后的提取器中复算，2 条记录全部字段精确匹配；第一条摘要保留 2 段原文引用；5 项完整性与边界检查通过|

本机模型版本：PaddleOCR 3.7.0、PaddlePaddle 3.3.1、PaddleX 3.7.2，模型文件集合哈希 `3308911c08f709a8a4334a85f139ba4d4345a6f8af7dbb9f0c0758543d523879`。未新增依赖、模型 API 调用或付费调用。

原 PDF 仅留在 `.local`；公开可访问不代表开放再分发许可。仓库仅保存获取脚本、来源与固定哈希、文字标签和验收摘要，不分发原件或字体文件。标签命中率不是发票金额准确率，也不能验证票据真伪。

## 保留的失败与修复

1. `.local/ocr-chinese-public-v1/evaluation-v1.json`：首次表格提取失败。真实 OCR 的倾斜框转换为轴对齐边界后与前行重叠 4 像素，旧续行条件拒绝合并。现在只容许不超过较小文字高度 15% 的重叠，同时要求上下基线间距至少 75% 文字高度；较大重叠、远距离、横向错位、跨列及无完整锚定字段仍拒绝合并。**原图、OCR 预测和预期未改**。修后复算属于已见样例上的开发修复验证，不是新的留出成绩。
2. `.local/ocr-chinese-public-v1/browser-v1/report.json`：首次真实网页验收失败。提取结果已有两行摘要，但草稿使用单行 HTML `input`，换行被浏览器剥除。改为 `textarea`。同时修复候选加入草稿或历史复核重载时只保留第一个引用的问题：补充原文引用现在保留、可逐条定位或明确移除，保存时完整提交。

没有通过删减断言、修改真值或将漏识算作命中来修复。上面两份失败报告保留，后续成功报告另存。

## 验证与复现

后端专项 `test_ocr_multiline.py`、`test_ocr_tables.py`、`test_ocr_workflow.py` 共 **66 项通过**，覆盖完整来源归属、续行歧义、页间隔离、重复表头和人工复核。Ruff 通过。正式网页复验结果由本轮统一隔离浏览器报告记录；下方摘要列明对应结果及其版本范围。

只复算现有预测，不重复编码、不联网、不调用模型：

```powershell
.venv/Scripts/python.exe -m benchmarks.chinese_ocr_eval --official .local/ocr-official-chinese-v1 --wrapped .local/ocr-wrapped-v1 --output .local/ocr-chinese-public-v1/evaluation-new.json
.venv/Scripts/python.exe -m pytest backend/tests/test_ocr_multiline.py backend/tests/test_ocr_tables.py backend/tests/test_ocr_workflow.py -q
```

从来源重做时，先使用 `datasets/official_chinese_ocr.py` 获取并验证固定 PDF；用 `scripts/ocr_pdf.py --dpi 180` 在既有本地 OCR 环境生成预测。合成样例使用 `datasets/generate_ocr_wrapped.py`，传入本机合法可用的字体路径；再使用 `scripts/ocr_image.py` 生成预测。两个 OCR 执行脚本均拒绝覆盖输出，执行期间禁止联网且不读取 `truth` 目录；评估器在全部预测存在后才读取真值。

## 尚未证明的能力

- 真实已开具中文发票的金额、税额、号码识别及端到端导入准确率。
- 手写、合并单元格、拆分金额、多税率明细、印章遮挡及无表头跨页表格。
- 通用字段提取或自动判断业务类型；摘要续行的语义归属仍须人工核实。
- 企业适用性、专业会计结论或票据真伪。AI 与 OCR 结果不会自动成为业务事实。

## 2026-10-23 引用编辑回归

修复更换主引用时清掉续行补充来源的问题：替换只影响首项，补充来源保留并去重。扩展原换行表格用例，验证更换主引用、保存、刷新重载、再次保存后两条真实来源仍在，金额和摘要真值保持。隔离Chrome参考/OCR整组8/8通过（其中4项是真实本地OCR），无跳过或flaky；证据位于`.local/reference-ocr-release-20261023/report.json`，生产构建通过。不是新的真实发票准确率验收。
