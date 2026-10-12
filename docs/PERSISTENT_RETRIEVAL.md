# 可选持久化参考索引

2026-10-13，BGE/pgvector 已从连接临时表扩展到独立数据库中的版本化持久表。**当前是已验证的索引基础设施和管理命令，尚未接入产品 API、用户开关或调查快照选择器。默认关键词检索保持不变。** 前轮向量检索未优于关键词的[负面结果](RETRIEVAL_VALIDATION.md)原样保留，本轮没有新的相关性成绩或模型有效性结论。

## 版本、来源与失败边界

- `backend/app/retrieval_index.py` 使用独立 `ledgerlens_retrieval.versions/segments` 表；没有改动产品账务表或默认语料。数据库连接仅接受固定参数化语句，不向运行 Agent 提供 SQL。
- 管理命令先按独立来源登记重新校验原始文件和 `reference-corpus-v2`，再切片。片段保留父条文、字符起止、制度和期间；逐字符核对父片段，不能把窗口伪装成新来源。
- 索引标识是完整清单的 SHA256：绑定语料文件、登记文件、已核验父条文、固定模型/查询前缀/窗口参数、片段集合及 512 维向量。向量按数据库实际 float32 二进制计算哈希，避免 JSON 浮点表示差异。
- 每个索引最多 20,000 个窗口；编码前检查上限。此实现面向有限本地语料，使用精确余弦排序，没有声称达到大规模近似索引性能。
- 发布在一个数据库事务中进行，提交前重新读取并验证所有行；失败则整笔回滚。相同标识重建只核验已有版本，返回 `created=false`。不会自动修补损坏版本，也不覆盖旧版本。
- 读取必须提供独立保留的成功构建清单，不能相信数据库自己给出的“正确哈希”。核验完整版本后在同一 `REPEATABLE READ READ ONLY` 事务内排序，防止核验后并发改变行而返回未经核验内容。缺行、额外行、正文/向量/清单改变明确失败，不静默切回关键词或伪装空结果。
- 查询的编码器身份必须与索引一致，制度和期间筛选沿用前轮协议。结果仍是参考候选，相似度不是概率，也不代表问题有答案。CLI 返回可回查的原父条文冻结引用；这不等于已经进入账套快照。
- 命令只能连接配置端口的 `127.0.0.1` 专用库，拒绝其他网络连接和外部域名解析；固定本地模型不下载。没有读取评测真值或付费模型接口。报告拒绝覆盖旧文件，也不能写进来源或模型目录。

外部清单与数据库内容校验属于本地完整性边界；同时有权篡改清单、程序及数据库的管理员不在防护范围内。组织权限、数据库只读角色和多用户管理尚未实现。

## 实跑证据

沿用6份已核验财政部原文、94个父片段和156个窗口，模型/窗口/查询前缀均未调整。第一次构建因已安装驱动返回 `Vector` 而不是数组，触发 `TypeError`；失败报告保留。按驱动源码使用 `to_list()` 后成功，索引标识：

`3e814e281f4349aeed5afc6c7b200accfdd32945e30cbb9135ee1fd0180d0d8c`

第二个独立进程重建同一索引，返回 `created=false`。随后真正重启 `ledgerlens-retrieval-pg` 容器，启动时间已变；新的独立进程完整核验156行，再对“存货采购成本包含哪些费用”查询，前后6个片段、分数和原父条文引用完全一致。此查询用于持久化回放，没有以命中结果宣称相关性提升。

新增27项检查，包括9项真实 PostgreSQL 测试：断开/重新连接、同版本重建、制度与期间过滤、手写向量点积排序、正文/向量/缺行/多行/清单篡改拒绝、并发修改期间的读事务一致性、故障注入后的整笔回滚。连同原检索检查55项通过（1.44秒）；Ruff、生成契约一致性通过。本轮未改前端或产品 API，不重复浏览器与后端全量；前轮全量1917通过、2项Windows链接权限跳过和浏览器42通过仍是独立历史记录。

原始报告位于 `.local/retrieval-index-*.json`；[公开摘要](persistent-retrieval-validation.json)保留成功/失败文件哈希、重启证据、索引清单及前后结果。未改前轮20条查询、真值、阈值、排序或已报告的负面成绩。没有新增依赖，沿用[可选检索环境及许可证](dependencies-retrieval.json)。

## 复现命令

先按 [RETRIEVAL_VALIDATION](RETRIEVAL_VALIDATION.md)准备已固定的模型、来源和专用本地数据库。以下报告路径必须不存在；不要删除历史失败报告来复用路径。

```powershell
docker start ledgerlens-retrieval-pg
.local/retrieval-env/Scripts/python.exe scripts/retrieval_index.py build --corpus .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1 --model-root .local/bge-small-zh-v1.5 --output .local/index-build-new.json
.local/retrieval-env/Scripts/python.exe scripts/retrieval_index.py verify --manifest .local/index-build-new.json --corpus .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1 --output .local/index-verify-new.json
.local/retrieval-env/Scripts/python.exe scripts/retrieval_index.py query --manifest .local/index-build-new.json --corpus .local/retrieval-corpus-v1.json --registry docs/retrieval-sources-v1.json --source-root .local/retrieval-sources-v1 --model-root .local/bge-small-zh-v1.5 --query '存货采购成本包含哪些费用' --output .local/index-query-new.json

# 显式使用专用测试库；测试版本有独立随机标识，不修改真实语料版本。
$env:RETRIEVAL_INDEX_TESTS='1'
.venv/Scripts/python.exe -m pytest backend/tests/test_retrieval_index.py backend/tests/test_retrieval_vectors.py -q
```

后续需要把用户明确选择的索引和返回父条文绑定到新调查快照，历史快照保持原参考资料；还需 API/网页状态、失败呈现与边界回归。在完成这些之前，不把管理命令的通过当作产品检索接入完成。独立相关性/无答案验证、专业复核及规模性能仍待验证。
