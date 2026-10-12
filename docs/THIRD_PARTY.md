# 借鉴、依赖与许可

自主实现代码Apache-2.0。此工程没有复制ERPNext/Odoo/OCA/Beancount源码。下列项目用于设计参考或可选联调，不能把其许可改写成项目许可。

新增PDF预览使用Mozilla的`pdfjs-dist 6.4.299`（Apache-2.0），按需加载，Node要求≥22.13；来源与渲染API见[官方仓库](https://github.com/mozilla/pdf.js)和[官方示例](https://mozilla.github.io/pdf.js/examples/)。Python与前端锁文件许可元数据由`scripts/dependency_inventory.py`生成，分别保存在`dependencies-python.json`、`dependencies-frontend.json`。

|项目|参考位置|采用方式/许可|
|ERPNext|stock_ledger、GL Entry、immutable ledger、repost tests|只读导出适配器；本地联调以官方镜像`frappe/erpnext:v16.50.0`作为独立服务运行（`infra/erpnext/compose.yaml`），未复制代码；GPLv3|
|Odoo Community|stock_valuation_layer及测试|结构设计对照；LGPLv3，Enterprise不包含|
|OCA account-financial-tools|account_journal_lock_date、account_move_post_date_user|期间/操作者控制思路；逐模块核对，常见AGPL3|
|Beancount|loader、ops/validation、ops/summarize、core/inventory|实际可选评测工具3.2.3，9/9基础差分通过；GPL-2.0-only，不进入应用运行依赖|
|LangGraph|persistence/checkpointer|直接依赖，MIT|
|Langfuse|datasets/experiments/tracing|可选，尚未部署；社区MIT，企业目录另有许可|
|BlackLine|financial-close产品页|产品功能基线，未复制实现|

主要直接依赖：Vue/Vite/NaiveUI/ECharts/Cytoscape（依其发布许可证）、FastAPI/Pydantic/SQLAlchemy/Alembic、Celery、Redis、Postgres/pgvector、OR-Tools（Apache-2.0）、pdfplumber（MIT）、openpyxl（MIT）、httpx（BSD）、Playwright（Apache-2.0）。实际分发需以锁文件版本附带的LICENSE为准，生成依赖清单而非只依据此摘要。

2026-10-12新增`opentelemetry-sdk==1.45.1`（含同版API及`opentelemetry-semantic-conventions==0.66b1`），均为Apache-2.0，已核对发行元数据与[官方仓库LICENSE](https://github.com/open-telemetry/opentelemetry-python/blob/main/LICENSE)，锁入`uv.lock`并更新依赖清单。仅使用本地SDK与自主JSON Exporter，没有添加远端观测服务。见[本地观测](OBSERVABILITY.md)。

BAAI/bge-small-zh-v1.5与PaddleOCR为可选模型/组件，未打包权重。启用前读取该版本模型卡及依赖许可。DeepSeek是配置的外部模型服务，不随开源代码分发模型权重。

2026-10-12已离线实跑BGE固定revision（MIT模型卡）与pgvector0.8.2／PostgreSQL17.10独立容器。sentence-transformers6.1.0（Apache-2.0）、torch2.14.1及56个可选依赖来源、元数据和许可文件哈希见[retrieval依赖登记](dependencies-retrieval.json)；psycopg／psycopg-binary的LGPL-3.0与torch附带多项许可保持原样。tokenizers0.23.3发行元数据标识Apache，wheel未附LICENSE，另核对[官方LICENSE](https://github.com/huggingface/tokenizers/blob/main/LICENSE)。不将模型权重、官方原文、数据库目录加入发行物。固定模型清单、查询结果、复现与限制见[检索验证](RETRIEVAL_VALIDATION.md)。

2026-10-12已在独立`.local/ocr-env`核验并实跑PaddleOCR 3.7.0／PaddlePaddle 3.3.1／PaddleX 3.7.2（Apache-2.0），采用官方PP-OCRv5 mobile检测/识别权重，模型卡Apache-2.0，来源与文件哈希见[OCR模型登记](ocr-model-sources.json)。[可选依赖清单](dependencies-ocr.json)记录72个包及随包许可文件哈希，`python-bidi`的LGPL与`crc32c`的LGPL-2.1-or-later等许可单独保留；未复制或改写第三方源码许可。合成图仅本机使用Windows字体，不分发字体或OCR权重，复现说明见[OCR验证](OCR_VALIDATION.md)。

公开准则、年报、案例、BPI/FinQA各自保留来源和数据许可。可公开读取不等于许可再次打包发布；默认只提交清单、程序和自建合成数据生成器。

## 本轮实际采用的评测工具与公开数据

Beancount 3.2.3已在F盘现有虚拟环境中安装，用于独立基础余额及不平衡差分。没有复制第三方源码、加入应用主锁文件或运行镜像。可选清单为`benchmarks/requirements-beancount.txt`；实际版本及许可见[差分报告](beancount-validation-summary.json)和[安装／验证说明](BEANCOUNT_DIFFERENTIAL.md)。本轮安装的regex 2026.9.29元数据许可为`Apache-2.0 AND CNRI-Python`，其余已记录依赖保留各自许可。项目Apache-2.0不能覆盖这些第三方包。

FinQA采集使用[官方仓库](https://github.com/czyssrs/FinQA)的dev数据及MIT许可原文，保存源字节SHA256、许可副本和检索定位。已核验883条记录的观察／答案分区；原始财报摘录可能涉及另行的原报告权利，不随开源仓库重新分发。未获取官方public/private test。[覆盖报告](finqa-coverage.json)区分数据采集与未开展的推理准确率测试。

新增有界Decimal计算器、来源定位和模型组件评测为自主实现，参考[FinQA官方评测器](https://github.com/czyssrs/FinQA/blob/main/code/evaluate/evaluate.py)的操作语义，未复制其浮点实现，也未新增运行依赖。参考程序重放与只读题目模型试跑分别报告；[组件验证](FINQA_COMPONENT_VALIDATION.md)不将第三方答案宣称为本平台生成的标准答案。

BPI2019由Boudewijn van Dongen发布，来源[官方DOI](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1)，官方元数据明确CC BY 4.0；完整XES及首次1000个完整trace留在忽略的本地目录。公开材料保留作者、来源、许可、哈希及子集选择方法，不将匿名事件日志宣称为本项目原创企业账套。见[覆盖报告](bpi2019-coverage.json)和[数据说明](BPI2019_DATA.md)。

已导入的ProcessDataset／BusinessEvent快照同样留在本地忽略目录，保留CC BY 4.0来源元数据。转换器、独立来源定位验证器及证据页面为本项目自主实现，没有复制ERP或第三方适配代码；使用已有Pydantic、FastAPI和Naive UI，不新增运行依赖。

费用估算器使用2026-10-08核验的[DeepSeek官方价格](https://api-docs.deepseek.com/quick_start/pricing/)，仅登记价格事实和来源，不复制说明文章；价格快照不是模型权重许可或服务可用性承诺。

32条财政部问答原HTML及答案留在本地，开源仓库只保留来源清单、哈希与定位。它们不是复制进生产代码的执行规则，也没有据采集结果宣称专业准则有效性。
