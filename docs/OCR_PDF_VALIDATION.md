# 扫描PDF的本地识别与原页定位

2026-10-13，本地固定PaddleOCR已完成6页纯图像合成PDF识别：6/6页完成，字串23/24、整行28/35、空白页正确。所有文字仍为`unconfirmed`，不产生可导入账务记录。页面字段确认和产品网页流程尚未接入。

## 输入、坐标和资源边界

`backend/app/ocr_pdf.py`从本地PDF一次读取有界字节，以同一份不可变内容计算SHA256并交给PDFium。模型、CPU设置、文字／置信度／四边形校验沿用已冻结的图像OCR；每页的RGB像素与尺寸另有哈希，禁止模型重新按路径读取被替换的文件。

- 文件上限50MiB，最多20页；72–300整数DPI，默认144；单页最多1600万像素，总计8000万像素。所有页面先预检，再开始推理。
- pypdfium2固定5.14.0，已有OCR环境依赖，没有新增生产依赖。页面、位图、文本页显式关闭，同一模块加锁串行使用；仍应在独立OCR进程运行，不能据此宣称所有其他PDFium调用都线程安全。
- 输出记录原PDF哈希、页码、行号、原像素四边形、归一化显示坐标和PDF原页用户坐标。使用渲染器的仿射坐标变换处理旋转与裁剪；观察ID绑定原PDF、像素、模型、页码和坐标，相同内容出现在不同页也不共用ID。
- 单页异常保留该页错误，后续页继续；部分失败标记`completed_with_failures`并以非零CLI退出码结束，全部失败明确`failed`。只有合法空预测才是`no_text_detected`。
- 交互表单显式拒绝，避免可见外观与真实字段值不一致；损坏或无法打开的加密PDF报失败，不伪装空白页。未实现密码输入或签名有效性验证。

坐标、资源释放与线程限制依据已安装版本源码及[pypdfium2官方API](https://pypdfium2.readthedocs.io/en/stable/python_api.html)。此输出只声明文字观察的来源，不证明字段语义、金额准确性、签名真实性或业务有效性。

## 冻结合成验证

`datasets/generate_ocr_pdf.py`将原6张合成扫描图原样封装为6页PDF，沿用独立手写真值与哈希，不调用OCR生成答案。纯图像PDF的文本层字符数经独立重读确认是0。生成使用随工具提供的reportlab；不加入应用运行依赖，不分发本机字体。

|页|情形|指定字串|整行|
|---|---|---|---|
|1|清晰采购单|5/5|6/7|
|2|倾斜收款单|5/5|7/7|
|3|淡字费用单|5/5|5/7|
|4|小字对账单|5/5|5/7|
|5|易混字符核对单|3/4|5/7|
|6|空白|无字串；空白正确|无行|

只忽略空白，不修正数字或标点。`O0-I1-0856`的字母数字混淆和冒号差异仍保留；没有根据PDF结果改真值或调参。先完成识别落盘，再离线读取答案评分。

公开结果为[ocr-pdf-validation.json](ocr-pdf-validation.json)。摘要脚本重新渲染同一原PDF，逐页核对RGB哈希、全部四边形、仿射变换及观察ID，再计分。原PDF、预测、原始答案保持分区：`.local/ocr-pdf-v1/`与`.local/ocr-scans-v1/truth/`。v1运行因误把PdfPage当作上下文管理器而失败；改为`contextlib.closing`显式释放，v2通过，v3补充源码哈希后结果一致。失败文件未删除。

使用独立Poppler渲染6页检查版面，再把OCR框叠加到预览检查：全部框与对应文字对齐，空白页没有框。0／90／180／270度旋转及裁剪坐标由真实PDFium几何与替身文字做往返回归；这不证明侧转中文文字的实际识别率。

## 复现

在已安装并核验的`.local/ocr-env`和`.local/ocr-models`上执行，权重准备见[原图OCR验证](OCR_VALIDATION.md)。

```powershell
.local/ocr-env/Scripts/python.exe scripts/ocr_pdf.py --input .local/ocr-pdf-v1/synthetic-scans.pdf --models .local/ocr-models --output .local/ocr-pdf-v1/prediction-new.json
.venv/Scripts/python.exe scripts/summarize_ocr_pdf.py --prediction .local/ocr-pdf-v1/prediction-new.json --dataset .local/ocr-pdf-v1 --scans .local/ocr-scans-v1 --output .local/ocr-pdf-v1/summary-new.json
.venv/Scripts/python.exe -m pytest backend/tests/test_ocr_pdf.py backend/tests/test_ocr_observations.py -q
```

CLI仅接受本地路径、拒绝覆盖报告，Python审计钩子禁止socket连接；这不是操作系统沙箱。摘要校验不调用模型。合成PDF可用`datasets/generate_ocr_pdf.py --scans <原扫描数据目录> --output <新目录>`重建，需在有reportlab和Pillow的独立工具环境执行；主API无需安装reportlab。

本轮OCR专项46项通过（新增PDF23项），覆盖来源替换、页数与像素预算、失败页保留、旋转裁剪坐标以及摘要被篡改时拒绝。真实单据识别、手写、扫描表格结构、自动字段提取、人工字段确认和网页导入仍不在本次验证范围。识别文字绝不直接成为会计事实。
