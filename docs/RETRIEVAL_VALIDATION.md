# 参考条文检索离线对比

2026-10-12，本地CPU BGE＋实际pgvector与现有关键词评分完成一次冻结配置对比。**向量方法在这组查询上没有优于关键词检索，不替换产品默认实现。** 所有付费模型请求为0，未修改账簿、规则或历史快照。

## 原文与查询

[独立来源登记](retrieval-sources-v1.json)登记8份财政部原文，成功取得存货、固定资产、收入、租赁、小企业会计准则、会计软件规范6份，共94个原始片段。借款费用与会计政策变更两份连续两次返回502，采集报告及历史仍保留，未计为成功。登记没有填入未经核实的生效日期；条文仅为参考，不能因此当成当前可执行政策。

此实验使用独立登记，避免改写产品默认登记的历史绑定。加载沿用`reference-corpus-v2`校验：原始字节、解析内容、登记与片段绑定均核验。未将官方问答、答案、FinQA真值或模型判断放入语料。

查询20条，由实现者阅读这6份原文后手写：18条有答案、2条库中无答案。不是独立会计师盲评，也不是真实用户样本。问题与真值分文件；运行器先保存全部检索预测，再打开评价真值。真值是手选的“来源ID＋完整条文锚点”，只忽略空白，不给生产程序推导答案。运行前已经保存的文件哈希：

- 查询：`b748bda3cd7dd69494345d54cfaeb1532ea056ba8501f8dbfa1286fdc13e1b70`
- 真值：`ac7ceba5daad409d6996f808c9d1da0985cbb4ccc157aad9b9dab624ff0db7d8`

## 固定方法

BGE模型为官方[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，固定revision `7999e1d3359715c523056ef9478215996d62a620`，MIT；[11个文件的哈希](retrieval-model-v1.json)包括safetensors权重、tokenizer、配置与模型卡。加载仅接受本地固定文件、CPU、不信任远程代码、不自动下载。官方模型卡建议的中文查询前缀固定应用于问题，正文不加前缀；512维向量归一化，相似度不是概率。

按fast tokenizer的原字符坐标切480-token窗口、步长400，得到156个片段。关键词和向量使用**完全相同的156段**；切片逐字符与已验证父片段相符，引用仍需回到父片段，不能把切片伪装成新的来源绑定。正文编码与查询均检查长度，拒绝模型静默截断。首次分段时tokenizer提示某篇正文超过512，这是整篇分词提示，实际送入编码器的是经长度检查的窗口。

关键词沿用`knowledge.search`的中文双字词/拉丁词交集比例和严格正分门槛；BGE使用pgvector精确余弦距离，返回最多6段，没有根据本批结果拟合阈值。两者使用相同CAS/SCAS/BOTH与期间范围过滤。实际数据库PostgreSQL17.10、pgvector0.8.2，独立本地端口55439、512MB容器限制；[pgvector官方接口](https://github.com/pgvector/pgvector)的固定参数化SQL，仅实验专用连接临时表。20个查询的数据库前6位与独立NumPy余弦排序全部一致、分数误差不超过1e-5。尚未做持久化产品索引或生产检索开关。

## 结果

|方法|首位命中|前3位命中|前6位命中|MRR@6|无答案仍返回候选|
|---|---|---|---|---|---|
|关键词|12/18（66.7%）|15/18（83.3%）|16/18（88.9%）|0.7454|2/2|
|BGE＋pgvector|8/18（44.4%）|13/18（72.2%）|15/18（83.3%）|0.5898|2/2|

一次运行72.75秒，正文编码9.91秒。此为同一进程先关键词后向量的单次记录，包含初始化，不能据此推断生产延迟。40个“查询×方法”均完成；程序失败会保留空预测、错误和分母，不能当作正确拒答。

“命中”须有正确来源且包含完整人工锚点；不是返回同一文档就算正确，也不是对会计问题给出答案。两种方法都漏掉存货期末计量和收入控制权两条；BGE另漏掉租赁开始日确认条目。完整逐题分数和来源哈希见[公开摘要](retrieval-validation.json)。原报告`.local/retrieval-eval-v1.json`保存156段、全部排名、错误槽、时间和实现哈希，摘要脚本重验来源与标注得分，不重新运行模型。

两条无答案查询都返回资料，说明“检索有结果”不能被当成“证据支持”。这次负面结果保持原样，不在这20条上调整分段、前缀、阈值、排序或答案再重报提升。后续方案必须另立开发查询与未见验证集，解决无答案判定，并由独立人员复核相关性。

## 复现（PowerShell，仓库根目录）

可选环境位于F盘`.local/retrieval-env`，56个包固定在[锁定清单](../benchmarks/requirements-retrieval-lock.txt)；不进入主API环境。许可元数据和随包许可文件哈希见[依赖清单](dependencies-retrieval.json)，psycopg的LGPL-3.0和torch等包附带的多项许可各自保留。tokenizers发行元数据标识Apache，当前wheel未带独立LICENSE文件，另核对[官方LICENSE](https://github.com/huggingface/tokenizers/blob/main/LICENSE)。不分发模型权重或采集原文。

```powershell
uv venv .local/retrieval-env --python .venv/Scripts/python.exe
uv pip install --python .local/retrieval-env/Scripts/python.exe -r benchmarks/requirements-retrieval-lock.txt
# 只有准备阶段下载免费公开资料；以下目录首次运行必须为空。
.venv/Scripts/python.exe scripts/prepare_retrieval_model.py --download --output .local/bge-small-zh-v1.5
.venv/Scripts/python.exe scripts/collect_sources.py --manifest docs/retrieval-sources-v1.json --output .local/retrieval-sources-v1
.venv/Scripts/python.exe scripts/index_sources.py --report .local/retrieval-sources-v1/acquisition-report.json --output .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1

# 仅首次创建。本地实验凭据，不可作为生产凭据；已创建则 docker start ledgerlens-retrieval-pg。
docker run -d --name ledgerlens-retrieval-pg --memory 512m -e POSTGRES_PASSWORD=local-retrieval-only -e POSTGRES_USER=retrieval -e POSTGRES_DB=retrieval -p 127.0.0.1:55439:5432 -v F:/Code/Project/Project18/runtime/retrieval-postgres:/var/lib/postgresql/data pgvector/pgvector:0.8.2-pg17

.local/retrieval-env/Scripts/python.exe benchmarks/reference_retrieval.py --corpus .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1 --model-root .local/bge-small-zh-v1.5 --output .local/retrieval-eval-new.json
.venv/Scripts/python.exe scripts/summarize_retrieval.py --report .local/retrieval-eval-new.json --corpus .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1 --output .local/retrieval-summary-new.json
```

已存在的运行报告、模型目录和摘要拒绝覆盖；下载中断保留partial供排查，新尝试用新目录。准备脚本使用已取得的11个官方文件进行本地回放，文件大小与哈希全部通过，未重复联网。未来原站内容变动或两个失败源恢复会产生不同语料，不应称为同一冻结实验重放。源码换行字节变化也会改变哈希，应保留原报告并产生新记录。

本次没有测试扫描PDF、真实企业资料、专业意见正确性、大规模近似索引、跨进程索引共享或生产切换。模型仅输出向量，SQL仅由程序执行固定语句，不向运行Agent开放任意查询。
