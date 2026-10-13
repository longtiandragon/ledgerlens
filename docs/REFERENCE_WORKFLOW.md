# 本地参考条文人工选择

2026-10-13。版本化持久索引已接入本地 API 和“账套与政策”页面。默认仍是原关键词检索；BGE 候选仅在用户阅读并确认选择后进入新快照。此前关键词前6命中16/18、BGE为15/18的负面结果不变，本次是工程验收，不是新的相关性评测。

## 使用流程

1. 在“账套与政策 → 参考条文选择”选一个已配置的本地索引，输入政策问题。查询固定当前账套、制度、期间和修订号，不改变账簿或快照。
2. 查看命中片段、来源页码和字符范围，并展开阅读完整父条文。相似度不是可信度，返回候选也不代表该问题有答案。
3. 勾选适用父条文（最多6段，无预选），明确确认后建立新快照。选择版本、索引清单、查询、结果摘要与完整来源绑定随快照冻结。原记录不变，旧复核失效。
4. 后续数据快照沿用这些固定条文，不重新查询今天的索引。编辑账套政策或期间后选择明确失效，按新政策恢复默认关键词；需重新查询再选择。
5. “恢复默认关键词并建立快照”追加新的选择版本，不覆盖以前的选择或快照。历史快照无需当前数据库、模型、目录或原始文件即可核验冻结引用，但仍需要保留对应受信来源登记文件。

这里是参考材料选择，不修改规则包，不把条文或模型意见转为业务事实，也不替代专业人员判断。

## 启用本机已有索引

先按 [PERSISTENT_RETRIEVAL](PERSISTENT_RETRIEVAL.md) 构建并核验版本，保留成功构建清单。以下目录仅由本机操作者配置，API 不接受任意路径、数据库地址、命令或 SQL。目录示例存于忽略目录 `runtime/reference-index-catalog.json`：

```json
{
  "schema_version": "reference-index-catalog-v1",
  "indexes": [{
    "title": "财政部六份原文 · 实验 BGE",
    "manifest": ".local/retrieval-index-build-v2.json",
    "corpus": ".local/retrieval-corpus-v1.json",
    "registry_key": "mof-retrieval-v1",
    "source_root": ".local/retrieval-sources-v1",
    "model_root": ".local/bge-small-zh-v1.5"
  }]
}
```

`registry_key` 必须在服务端受信登记表中，当前只允许 `docs/retrieval-sources-v1.json`。支持1至8个不同索引。设置进程环境后启动；若既有服务正在运行，先使用 `scripts/stop-local.ps1` 停止该脚本记录的服务。

```powershell
$env:RETRIEVAL_PYTHON = "$PWD/.local/retrieval-env/Scripts/python.exe"
$env:REFERENCE_INDEX_CATALOG = "$PWD/runtime/reference-index-catalog.json"
$env:RETRIEVAL_DB_PORT = '55439'
$env:RETRIEVAL_TIMEOUT_SECONDS = '120'
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -SkipInstall
```

仅 `TASK_MODE=local` 启用；未配置时页面明确显示不可用，仍能查阅历史选择。能力接口只检查目录及构建清单，数据库、原文和模型在实际查询时校验。没有新增主环境依赖。可选环境及许可证沿用固定 BGE/pgvector 清单。

## 接口与失败边界

前缀 `/api/v1/reference-indexes`：`GET /capabilities`、`POST /searches`、`GET /searches?book_id=…`、`GET /searches/{id}?book_id=…`、`GET /choices?book_id=…`、`POST /choices`。请求类型由 OpenAPI 生成；数据库向前迁移 `0006` 新增检索任务和选择版本表，保留原 OCR 资料。

- 单本地进程串行执行，最多4个排队/运行任务；固定独立 Python 子进程和参数数组，无 Shell。网络仅允许专用回环 PostgreSQL，禁止模型下载和付费接口。
- 启动捕获账套修订号；冻结同时检查当前修订、上一选择版本和查询摘要。新资料、政策修改、恢复默认或另一选择均使旧查询不可再次用于冻结。
- 原文父段绑定、窗口正文及位置、制度/期间、索引清单、排序、有限分数及结果摘要逐项核验。跨账套、伪造引用、结果改变、来源登记改变均拒绝。
- 超时、坏配置、断连、篡改或服务重启显示失败；不会伪装空候选或静默切换检索方法。旧工作令牌不能覆盖恢复状态，完成任务重复派发不重算。
- 恢复默认及人工选择均追加版本、递增账套修订并产生新快照；历史引用在调查入口及工具上下文校验。API 不能修改历史选择或快照。
- 浏览器取消旧页面请求，按账套和检索记录区分轮询；迟到查询不会覆盖当前记录。错误时保留明确失败提示。

## 验证与边界

真实本机 BGE + pgvector 查询沿用原156窗口索引；网页完成阅读、人工冻结、规则调查、恢复默认。验收比较前后原始记录、旧快照和新快照引用，并检查390px布局与页面错误。原始 API JSON 保留，不经 JavaScript 重序列化后计算 Python 清单哈希。

可复现命令：

```powershell
$env:RETRIEVAL_INDEX_TESTS='1'
.venv/Scripts/python.exe -m pytest -q
cd frontend
$env:REFERENCE_LOCAL_TESTS='1'
$env:OCR_LOCAL_TESTS='1'
$env:PROCESS_DATASET_TESTS='1'
$env:STATEMENT_DATASET_TESTS='1'
npm run test:e2e
npm run build
```

最终测试数量、时长、源文件及原始证据哈希见 [reference-workflow-validation.json](reference-workflow-validation.json)。保留首次定向真实查询记录 `runtime/reference-e2e-v1` 和全量记录 `runtime/reference-all-e2e-v2`。

未验证：独立未见查询上的检索改进、无答案拒答能力、条文适用性的专业判断、规模性能、多进程任务队列及组织权限。没有改提示词/窗口/模型，没有再次调试或打分原有20条评测查询，没有调用付费模型。
