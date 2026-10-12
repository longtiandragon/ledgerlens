# 合成扫描件与本地 PaddleOCR 验证

2026-10-12已完成6张合成扫描图的CPU实跑：PaddleOCR 3.7.0、PaddlePaddle 3.3.1、PaddleX 3.7.2，固定PP-OCRv5 mobile检测和识别权重。全部输出为未确认文字观察，不生成账务记录。[公开摘要](ocr-validation.json)记录代码、数据和模型哈希；完整报告与图片保留在被Git忽略的`.local`目录。

## 输入、结果与边界

`datasets/generate_ocr.py`生成清晰、2度倾斜、低对比度、较小字号、易混字母数字和空白共6张PNG。图片明确标注教学合成用途，没有公司真实数据、真实发票版式或印章。字体使用本机Windows微软雅黑，记录字体SHA256，不分发字体文件。尺寸为1400×1000；原文字与24个指定字串独立写入`truth`目录，OCR接口只接受图像像素。评测器先保存所有预测，再读取答案计分。

|项目|结果|解释|
|---|---|---|
|推理任务|6/6完成|不等于6张文字全部正确|
|指定字串出现|23/24|包括编号、日期、币种和金额；只忽略空白，不校正字母、数字和标点|
|整行文字恢复|28/35|半角/全角冒号差异也计为错误|
|空白图|正确返回无文字|状态为`no_text_detected`，并非识别失败的替代状态|
|外部模型请求／账簿写入|0／0|使用本地CPU和已下载权重|

明确失败：`O0-I1-0856`识别成`00-11-0856`；若干半角冒号识别成全角冒号。没有改答案、改字符或去掉该案例提高成绩。金额字串出现不代表归属正确、金额真实或可以入账。六张同字体、简单布局的开发样本不支持真实企业、复杂表格、手写、印章遮挡、扫描PDF或大规模准确率主张。

`backend/app/ocr_observations.py`保留原始文字、置信度、原图SHA256、1起始页/行号和原像素四边形。禁用旋转校正、展平等文档变换，使坐标对应输入图。内容始终`unconfirmed`；高置信度也不自动确认。生产JSON导入器不会把观察当作`records`，测试验证导入无可入账行。缺失数组、错位数组、非有限置信度、图外/零面积坐标均拒绝；空数组才表示没有检测到文字。

读取限制为20MiB、1600万像素、单帧PNG/JPEG、2000行。图像读入一次，哈希与传给模型的像素来自同一份字节，避免模型重新读取已变化路径。观察ID绑定图像、模型与行内容，返回坐标和模型元数据复制后不随底层结果修改。这里只实现本地CLI与可调用适配器，尚无OCR上传页面、人工字段确认工作流或扫描PDF管线。

## 来源、安装与复现

参考[PaddleOCR官方接口文档](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html)，权重来自官方BOS下载地址，其压缩包及解包文件SHA256登记于[ocr-model-sources.json](ocr-model-sources.json)。[检测模型卡](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det)与[识别模型卡](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec)标明Apache-2.0。

可选环境安装在`.local/ocr-env`，不加入主API镜像。72个发行包版本固定于`benchmarks/requirements-ocr-lock.txt`；许可证与随包许可文件哈希记录于[dependencies-ocr.json](dependencies-ocr.json)。Paddle系列为Apache-2.0，间接依赖仍保留各自许可，尤其`python-bidi`为LGPL系列、`crc32c`为LGPL-2.1-or-later，不能统一宣称为Apache-2.0。模型、字体和虚拟环境不进入仓库。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv venv .local/ocr-env --python .venv/Scripts/python.exe
uv pip install --python .local/ocr-env/Scripts/python.exe -r benchmarks/requirements-ocr-lock.txt
# 仅首次显式下载免费权重；已存在的目录不会覆盖。
.venv\Scripts\python.exe scripts/prepare_ocr_models.py --download --output .local/ocr-models-new
.venv\Scripts\python.exe -m datasets.generate_ocr --output .local/ocr-scans-new --font C:/Windows/Fonts/msyh.ttc
.local\ocr-env\Scripts\python.exe -m benchmarks.ocr_images --dataset .local/ocr-scans-new --models .local/ocr-models-new --output .local/reports/ocr-new.json
.venv\Scripts\python.exe scripts/summarize_ocr.py --report .local/reports/ocr-new.json --dataset .local/ocr-scans-new --output .local/reports/ocr-summary-new.json
```

推理入口不自动下载，必须事先存在固定哈希权重。CLI与评测器在本进程安装Python审计钩子，拒绝socket连接和DNS查询；本次实跑记录0次被拦网络操作。这不是操作系统网络沙箱，也不宣称约束任意本机恶意原生代码。安装包和显式下载阶段需要公共网络，不调用收费API。

处理单张本地图并保存可回查观察：

```powershell
.local\ocr-env\Scripts\python.exe scripts/ocr_image.py --input .local/ocr-scans-v1/images/scan-005.png --models .local/ocr-models --output .local/reports/ocr-observations-new.json
```

输出文件拒绝覆盖；异常保留`failed`及错误类型，不以空观察冒充成功。基准v1、固定权重v2、深复制修正后的v3报告分别保留，三个版本均为23/24字串、28/35整行。公开摘要对应v3；单张CLI也实跑通过并保留错误识别字串。23项独立离线回归涵盖边界、篡改拒绝、原图绑定、不可直接入账、预测/失败计数和报告重新计分。
