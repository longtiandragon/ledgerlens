# BPI2019真实数据与完整trace子集

来源为Boudewijn van Dongen发布的[BPI Challenge 2019](https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1)，许可[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。数据是匿名采购流程事件日志，适合验证业务事件读取和关联组件，不包含本项目所需的完整中国企业账套或独立异常原因标签。

公开来源由[4TU官方元数据](https://data.4tu.nl/v2/articles/12715853)和[Figshare官方元数据](https://api.figshare.com/v2/articles/12715853)核验。文件ID为24072995，文件名`BPI_Challenge_2019.xes`，官方大小728558522字节，官方MD5为`4eb909242351193a61e1c15b9c3cc814`。采集器只采用通过身份、DOI、许可、文件名、大小和MD5验证的元数据中提供的下载地址。

```powershell
.venv\Scripts\python.exe -m datasets.bpi2019 --case-limit 1000
.venv\Scripts\python.exe -m datasets.bpi2019 --verify-only
.venv\Scripts\python.exe -m pytest backend/tests/test_bpi2019.py -q
```

原始XES、官方元数据副本和JSONL子集留在`.local/bpi2019/`。开源仓库中的`docs/bpi2019-coverage.json`只发布来源、许可、文件哈希、采集状态及完整trace统计。运行调查工具不会自动读取这个目录，事件日志也不会自动转换为账务事实。

2026-10-08已实际完成728558522字节原始文件下载，官方MD5通过；首次1000个完整trace共15351个事件，每trace最少2个、最多923个。SHA256及JSONL子集哈希记录在覆盖报告中。首次网络失败的0字节记录另保存在本地attempts目录，不用成功结果覆盖历史失败。

下载流最多800000000字节。未完成传输写入`.part`；断网、超时、超限、长度或MD5不符均标记失败，并保留实际已写字节数。续传必须取得与已有长度及官方总长度完全一致的Content-Range；服务器忽略Range时从零重写，避免重复追加前缀。只有完整大小及官方MD5均通过才生成原始XES成功记录。SHA256另外记录内容完整性，不将MD5当成身份认证。

子集复用`backend.app.connectors.read_bpi_xes`，选取**源文件顺序中的首次1000个完整trace**。读到trace关闭标签后才输出，保留该trace全部事件；不是前1000个事件，也不是随机采样。源属性名称和值原样保留为字符串，包括原文已有的金额相关字段；不据此推导币种、科目、分录或异常真值。`accounting_framework=null`、`currency_inferred=false`、`journals_inferred=false`、`anomaly_truth_available=false`均在统计中明确记录。

verify-only不访问网络，重新验证保存的官方元数据、完整XES大小／MD5／SHA256、子集哈希和完整trace统计，最后将每条子集与源文件首次完整trace逐项比较。仅重算子集哈希不能掩盖删事件或调整trace顺序。

实际采集完成与否以覆盖报告的`status`为准；只有`collected`配合verify-only的`verified`才表明采集及本地完整性验收成功。这个结果不证明异常调查准确率、中国会计准则适用性或真实企业收益。
