# v17 实验只读复核

复核日期：2026-10-09。数据来自 `.local/reports/deepseek-pilot-v17.json` 与
`runtime/deepseek-product-multi-v17.json`。本复核没有调用模型 API、读取 `.env` 或展示私有推理。

## 实验终态与边界

- 报告为 `agent-benchmark-v5`、`completed_with_failures`，耗时 1811.639 秒。
- development 数据集版本 1.0.0，选取 `case-01-00`、`case-16-01`，各方法各重复 3 次，共 24 行；24 行均可评分。
- 指标仅衡量可见合成规则标签与状态匹配，不衡量根因或真实企业业务正确率；重复运行只有两个场景族。
- `case-01-00` 的评测标签为空、状态 normal；`case-16-01` 为 RECEIPT_GAP、状态 insufficient。真值只用于执行结束后的评分。
- 总物理授权尝试 110/144，110 个返回用量记录，报告 Token 总计 1,615,713。逻辑 chat 115 次，0 次传输重试。
- 5 次逻辑 chat 被运行内 Token 预留拒绝，未成为物理尝试。`request_not_dispatched=0` 是外层付费闸门的事件计数，不能解释成所有逻辑 chat 都发出。
- “paid requests” 是发送前保守计入的授权尝试数，不是已核实账单费用；Token 不能直接当作人民币。

| 方法 | 完成/失败 | 精确规则匹配 | 状态匹配 | 正常误报 | 正常完成 | 请求 | Token | 工具 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| rules | 6/0 | 100% | 100% | 0% | 100% | 0 | 0 | 0 |
| single | 6/0 | 50% | 66.7% | 0% | 100% | 18 | 153,820 | 43 |
| multi | 0/6 | 0% | 0% | 0% | 0% | 49 | 784,500 | 151 |
| active | 5/1 | 16.7% | 50% | 33.3% | 66.7% | 43 | 677,393 | 54 |

multi 的正常误报 0% 同时伴随正常完成率 0%，不能据此称其可靠。失败行的精确规则与状态匹配均记为失败，即便失败前保存的模型意见碰巧与评测状态相同。
所有方法的引用可定位率为 100%；这只说明已接受意见所引用的 ID 可定位，不证明语义、适用条件或数值结论正确。
固定 16 标签 macro-F1：rules=0.0625，其他方法=0；当前观察标签仅 RECEIPT_GAP，其 F1：rules=1，其他方法=0。
single 和 active 的已完成样本均未检出 RECEIPT_GAP，结构修复不能据此宣称检出能力已经改善。

## 产品 multi 验收

- 2026-10-08 21:42:27 至 21:44:01，93.406 秒后终止。
- 验收 `failed`，调查 `budget_exhausted`；错误为下一次模型请求 Token 预算不足。
- 10 次模型请求、142,569 Token、33 次工具调用；全局上限为 24 次请求、200,000 Token、60 次工具。
- 3 次 agent_completed、4 次 agent_started；没有完成全部五角色流程。
- 原账前后均为 13 条，SHA-256 均为 `d2bdae09df17ed6fbb835d92ca6b8bfd36e6d4adc211487a1fa8da4362a43900`，原账只读检查通过；快照未陈旧。
- 验收失败不能表述为闭环成功，也没有证明会计结论正确或多 Agent 有增益。

## 固定多 Agent 的结构诊断

v17 的五角色为 ledger → evidence → policy → critic → report。五个预算失败中，三个在 policy 完成后、critic 首次请求前失败；一个在 evidence 的最终整理请求前失败；一个在 critic 的首批取证后失败。
另一个正常样本在 evidence 先输出超过 20 个 claims，修复后又引用快照外证据，得到 model_output 失败。
所有 multi 样本都没有完成 report。

v17 强制每个角色重写截至当轮的完整累计结论；每批工具之后重新传输全部共享证据和最近的累计意见。
正常样本首个请求为 15,655 B，缺口样本为 15,767 B；正常样本第三次重复后期达到 83,274 B。
五角色共享同一个 200,000 Token 预算，网关按完整请求 UTF-8 字节数加最大输出上界保守预留。
因此实用量尚低于 200,000 时仍可能无法安全预留下一请求，属于正确失败状态；不能通过降低预留、提升预算或删掉负面观察伪装完成。

### 传输贡献量化

以下按当时哈希一致的 v17 源码，离线重建 review 系统协议与 schema，再结合实际 context_bytes 统计。
报告没有保留完整请求正文，所以只能精确分离已知系统消息和顶层工具字段；其余消息内部的证据、意见与常量封装无法从报告单独恢复。
固定 body 和列表分隔符留在“其他消息及封装”中。active 协调阶段的 tool_contracts 位于用户正文，计入该列。

| 方法 | 请求正文总字节 | 系统消息 | 顶层 tools 字段 | 其他消息及封装 |
|---|---:|---:|---:|---:|
| single | 420,921 | 117,684 (28.0%) | 74,748 (17.8%) | 228,489 (54.3%) |
| multi | 2,012,293 | 323,797 (16.1%) | 205,557 (10.2%) | 1,482,939 (73.7%) |
| active | 1,663,594 | 401,747 (24.1%) | 124,580 (7.5%) | 1,137,267 (68.4%) |

v17 顶层七工具字段每次 6,229 B。仅精简 parameters 内 schema 节点的注释、保留工具功能说明和同名业务属性后为 4,834 B，减少 1,395 B（22.4%）；multi 的 33 个带 tools 请求合计约省 46,035 B，仅占总传输 2.29%。
内嵌 BaseProposal schema 原文本为 2,393 B，精简注释后 1,785 B；active Proposal 为 4,137 → 3,200 B。
此估算是 v17 参数 schema 的离线精简实验，不是 v18 整体精确节约值；不能误删工具功能说明或名为 title/default/description 的业务属性。

## v18 可执行方向与必须保留的语义

优先将 specialist 输出改为本职责独立意见，最终 synthesis 获得全部已接受角色意见并复核合并。
保留完整历史的只读访问和全部历史索引；critic 必须能定位更早的发现和冲突，不能只看 policy 最后一份独立意见。
最终评分仍取最终模型结论，不能改成各角色 finding 的并集来掩盖最终报告遗漏；意见合并不得自动提升为业务事实。
配合精简模型端 schema 注释和重复协议，可减少每轮开销；服务端校验、五/三角色路径、24/200000/60 上限、保守预留应保持。

协议精简必须保留：固定账套/快照/规则版本、具名只读工具、ID 种类、当前准则 rule_id、政策引用的真实检索来源、assessment 四种含义、程序字符串指标、未请求政策与缺文献的区别、缓存与增量记录的区别、false/null 核验与 requested 未取得状态、普通 review 无预设异常、传输池编号不是证据，以及 active 仅计划的协调权限。
不得把结构合法、引用存在、多个角色同意或原账未改解释成会计语义已经验证。

离线回归重点：早期唯一发现经中间角色空意见仍进入最终 synthesis 的输入；冲突意见同时可见；程序负面核验、未取得资料与缺口保留；拒绝/截断输出不进入历史；模型建议不进入已验证事实；全部结构约束及业务属性名称不被注释精简损坏。
这些回归只证明程序合同与交接行为，不证明真实模型的正确率或最终模型会按协议执行。

新增 `backend/tests/test_model_contract.py` 的五项离线回归于本次复核通过，ruff 检查通过。测试使用 httpx.MockTransport，没有发送实际模型请求；覆盖注释同名业务字段、const/enum 原值、结构约束和深拷贝、早期独立意见的最终交接、冲突及负面程序观察保留，以及 active 最后角色获得全部历史。

## 源码稳定性

在本次复核开始、v18 修改之前，报告记录的七个源码 SHA-256 均与工作区文件一致：investigation.py、model_gateway.py、context_transport.py、business_chain.py、evidence_planning.py、evidence_selection.py、benchmarks/run_agents.py。
该结论仅覆盖报告记录的七个文件和该复核时点，不等于整个仓库或后续 v18 工作区均未变。

## 24 行记录

下表逐行来自报告；规则与状态栏为模型/规则运行保存的结果，错误行仍按失败评分。

| Case | Repeat | Method | Error | Predicted rules | Predicted status | Exact/status match | Calls | Tokens | Tools |
|---|---:|---|---|---|---|---|---:|---:|---:|
| case-01-00 | 1 | rules | - | - | normal | True/True | 0 | 0 | 0 |
| case-01-00 | 1 | single | - | - | normal | True/True | 3 | 24929 | 8 |
| case-01-00 | 1 | multi | model_output | - | insufficient | False/False | 7 | 78675 | 18 |
| case-01-00 | 1 | active | model_output | - | insufficient | False/False | 8 | 125883 | 14 |
| case-16-01 | 1 | single | - | - | normal | False/False | 3 | 33355 | 10 |
| case-16-01 | 1 | multi | budget_exhausted | - | insufficient | False/False | 9 | 148971 | 26 |
| case-16-01 | 1 | active | - | - | insufficient | False/True | 7 | 99811 | 6 |
| case-16-01 | 1 | rules | - | RECEIPT_GAP | insufficient | True/True | 0 | 0 | 0 |
| case-01-00 | 2 | single | - | - | normal | True/True | 3 | 19219 | 5 |
| case-01-00 | 2 | multi | budget_exhausted | - | normal | False/False | 9 | 136289 | 26 |
| case-01-00 | 2 | active | - | BANK_UNMATCHED | normal | False/True | 8 | 134541 | 10 |
| case-01-00 | 2 | rules | - | - | normal | True/True | 0 | 0 | 0 |
| case-16-01 | 2 | multi | budget_exhausted | INVOICE_EVIDENCE,RECEIPT_GAP | insufficient | False/False | 9 | 143824 | 31 |
| case-16-01 | 2 | active | - | - | insufficient | False/True | 6 | 98270 | 9 |
| case-16-01 | 2 | rules | - | RECEIPT_GAP | insufficient | True/True | 0 | 0 | 0 |
| case-16-01 | 2 | single | - | - | insufficient | False/True | 3 | 26830 | 7 |
| case-01-00 | 3 | multi | budget_exhausted | - | normal | False/False | 5 | 117695 | 29 |
| case-01-00 | 3 | active | - | - | insufficient | True/False | 7 | 102377 | 7 |
| case-01-00 | 3 | rules | - | - | normal | True/True | 0 | 0 | 0 |
| case-01-00 | 3 | single | - | - | normal | True/True | 3 | 26149 | 8 |
| case-16-01 | 3 | active | - | - | normal | False/False | 7 | 116511 | 8 |
| case-16-01 | 3 | rules | - | RECEIPT_GAP | insufficient | True/True | 0 | 0 | 0 |
| case-16-01 | 3 | single | - | - | normal | False/False | 3 | 23338 | 5 |
| case-16-01 | 3 | multi | budget_exhausted | - | insufficient | False/False | 10 | 159046 | 21 |

## Per-attempt byte decomposition (v17)

System and top-level tools are reconstructed from the audited v17 protocol; other messages plus body framing are the remainder. No request text or private reasoning is included.

| Case | Repeat | Method | Request | Role | Stage | Total bytes | System entry | Tools member | Other messages + framing |
|---|---:|---|---:|---|---|---:|---:|---:|---:|
| case-01-00 | 1 | single | 1 | single | role_initial | 15646 | 6538 | 6229 | 2879 |
| case-01-00 | 1 | single | 2 | single | evidence_context_refreshed | 23591 | 6538 | 6229 | 10824 |
| case-01-00 | 1 | single | 3 | single | tool_stage_finalization | 29505 | 6538 | 0 | 22967 |
| case-01-00 | 1 | multi | 1 | ledger | role_initial | 15655 | 6547 | 6229 | 2879 |
| case-01-00 | 1 | multi | 2 | ledger | evidence_context_refreshed | 20852 | 6547 | 6229 | 8076 |
| case-01-00 | 1 | multi | 3 | ledger | tool_stage_finalization | 18368 | 6547 | 0 | 11821 |
| case-01-00 | 1 | multi | 4 | evidence | role_initial | 30921 | 6583 | 6229 | 18109 |
| case-01-00 | 1 | multi | 5 | evidence | evidence_context_refreshed | 43085 | 6583 | 6229 | 30273 |
| case-01-00 | 1 | multi | 6 | evidence | tool_stage_finalization | 46835 | 6583 | 0 | 40252 |
| case-01-00 | 1 | multi | 7 | evidence | schema_repair | 46806 | 6583 | 0 | 40223 |
| case-01-00 | 1 | active | 1 | coordinator | role_initial | 18896 | 9563 | 0 | 9333 |
| case-01-00 | 1 | active | 2 | evidence | role_initial | 33491 | 9280 | 6229 | 17982 |
| case-01-00 | 1 | active | 3 | evidence | evidence_context_refreshed | 39387 | 9280 | 6229 | 23878 |
| case-01-00 | 1 | active | 4 | evidence | tool_stage_finalization | 34309 | 9280 | 0 | 25029 |
| case-01-00 | 1 | active | 5 | critic | role_initial | 47958 | 9286 | 6229 | 32443 |
| case-01-00 | 1 | active | 6 | critic | evidence_context_refreshed | 49506 | 9286 | 6229 | 33991 |
| case-01-00 | 1 | active | 7 | critic | tool_stage_finalization | 46652 | 9286 | 0 | 37366 |
| case-01-00 | 1 | active | 8 | critic | schema_repair | 51018 | 9286 | 0 | 41732 |
| case-16-01 | 1 | single | 1 | single | role_initial | 15758 | 6538 | 6229 | 2991 |
| case-16-01 | 1 | single | 2 | single | evidence_context_refreshed | 34932 | 6538 | 6229 | 22165 |
| case-16-01 | 1 | single | 3 | single | tool_stage_finalization | 36662 | 6538 | 0 | 30124 |
| case-16-01 | 1 | multi | 1 | ledger | role_initial | 15767 | 6547 | 6229 | 2991 |
| case-16-01 | 1 | multi | 2 | ledger | evidence_context_refreshed | 23729 | 6547 | 6229 | 10953 |
| case-16-01 | 1 | multi | 3 | ledger | tool_stage_finalization | 27965 | 6547 | 0 | 21418 |
| case-16-01 | 1 | multi | 4 | evidence | role_initial | 39786 | 6583 | 6229 | 26974 |
| case-16-01 | 1 | multi | 5 | evidence | evidence_context_refreshed | 50034 | 6583 | 6229 | 37222 |
| case-16-01 | 1 | multi | 6 | evidence | tool_stage_finalization | 47151 | 6583 | 0 | 40568 |
| case-16-01 | 1 | multi | 7 | policy | role_initial | 59850 | 6739 | 6229 | 46882 |
| case-16-01 | 1 | multi | 8 | policy | evidence_context_refreshed | 70043 | 6739 | 6229 | 57075 |
| case-16-01 | 1 | multi | 9 | policy | model_truncation_recovery | 64046 | 6739 | 0 | 57307 |
| case-16-01 | 1 | active | 1 | coordinator | role_initial | 19008 | 9563 | 0 | 9445 |
| case-16-01 | 1 | active | 2 | coordinator | model_truncation_recovery | 19242 | 9563 | 0 | 9679 |
| case-16-01 | 1 | active | 3 | evidence | role_initial | 24964 | 9280 | 6229 | 9455 |
| case-16-01 | 1 | active | 4 | evidence | evidence_context_refreshed | 42057 | 9280 | 6229 | 26548 |
| case-16-01 | 1 | active | 5 | evidence | model_truncation_recovery | 36060 | 9280 | 0 | 26780 |
| case-16-01 | 1 | active | 6 | critic | role_initial | 48848 | 9286 | 6229 | 33333 |
| case-16-01 | 1 | active | 7 | critic | model_truncation_recovery | 42851 | 9286 | 0 | 33565 |
| case-01-00 | 2 | single | 1 | single | role_initial | 15646 | 6538 | 6229 | 2879 |
| case-01-00 | 2 | single | 2 | single | evidence_context_refreshed | 23850 | 6538 | 6229 | 11083 |
| case-01-00 | 2 | single | 3 | single | evidence_stagnation | 17917 | 6538 | 0 | 11379 |
| case-01-00 | 2 | multi | 1 | ledger | role_initial | 15655 | 6547 | 6229 | 2879 |
| case-01-00 | 2 | multi | 2 | ledger | evidence_context_refreshed | 21111 | 6547 | 6229 | 8335 |
| case-01-00 | 2 | multi | 3 | ledger | tool_stage_finalization | 26339 | 6547 | 0 | 19792 |
| case-01-00 | 2 | multi | 4 | evidence | role_initial | 39812 | 6583 | 6229 | 27000 |
| case-01-00 | 2 | multi | 5 | evidence | evidence_context_refreshed | 41734 | 6583 | 6229 | 28922 |
| case-01-00 | 2 | multi | 6 | evidence | tool_stage_finalization | 41163 | 6583 | 0 | 34580 |
| case-01-00 | 2 | multi | 7 | policy | role_initial | 48051 | 6739 | 6229 | 35083 |
| case-01-00 | 2 | multi | 8 | policy | evidence_context_refreshed | 62481 | 6739 | 6229 | 49513 |
| case-01-00 | 2 | multi | 9 | policy | model_truncation_recovery | 56484 | 6739 | 0 | 49745 |
| case-01-00 | 2 | active | 1 | coordinator | role_initial | 18896 | 9563 | 0 | 9333 |
| case-01-00 | 2 | active | 2 | evidence | role_initial | 34184 | 9280 | 6229 | 18675 |
| case-01-00 | 2 | active | 3 | evidence | evidence_context_refreshed | 39795 | 9280 | 6229 | 24286 |
| case-01-00 | 2 | active | 4 | evidence | tool_stage_finalization | 34807 | 9280 | 0 | 25527 |
| case-01-00 | 2 | active | 5 | critic | role_initial | 47960 | 9286 | 6229 | 32445 |
| case-01-00 | 2 | active | 6 | critic | evidence_context_refreshed | 53027 | 9286 | 6229 | 37512 |
| case-01-00 | 2 | active | 7 | critic | model_truncation_recovery | 47030 | 9286 | 0 | 37744 |
| case-01-00 | 2 | active | 8 | critic | schema_repair | 51278 | 9286 | 0 | 41992 |
| case-16-01 | 2 | multi | 1 | ledger | role_initial | 15767 | 6547 | 6229 | 2991 |
| case-16-01 | 2 | multi | 2 | ledger | evidence_context_refreshed | 23987 | 6547 | 6229 | 11211 |
| case-16-01 | 2 | multi | 3 | ledger | tool_stage_finalization | 28960 | 6547 | 0 | 22413 |
| case-16-01 | 2 | multi | 4 | evidence | role_initial | 42900 | 6583 | 6229 | 30088 |
| case-16-01 | 2 | multi | 5 | evidence | evidence_context_refreshed | 44301 | 6583 | 6229 | 31489 |
| case-16-01 | 2 | multi | 6 | evidence | model_truncation_recovery | 38304 | 6583 | 0 | 31721 |
| case-16-01 | 2 | multi | 7 | policy | role_initial | 44563 | 6739 | 6229 | 31595 |
| case-16-01 | 2 | multi | 8 | policy | evidence_context_refreshed | 56909 | 6739 | 6229 | 43941 |
| case-16-01 | 2 | multi | 9 | policy | tool_stage_finalization | 59392 | 6739 | 0 | 52653 |
| case-16-01 | 2 | active | 1 | coordinator | role_initial | 19008 | 9563 | 0 | 9445 |
| case-16-01 | 2 | active | 2 | evidence | role_initial | 34012 | 9280 | 6229 | 18503 |
| case-16-01 | 2 | active | 3 | evidence | evidence_context_refreshed | 39911 | 9280 | 6229 | 24402 |
| case-16-01 | 2 | active | 4 | evidence | tool_stage_finalization | 39917 | 9280 | 0 | 30637 |
| case-16-01 | 2 | active | 5 | critic | role_initial | 54833 | 9286 | 6229 | 39318 |
| case-16-01 | 2 | active | 6 | critic | model_truncation_recovery | 48836 | 9286 | 0 | 39550 |
| case-16-01 | 2 | single | 1 | single | role_initial | 15758 | 6538 | 6229 | 2991 |
| case-16-01 | 2 | single | 2 | single | evidence_context_refreshed | 23720 | 6538 | 6229 | 10953 |
| case-16-01 | 2 | single | 3 | single | tool_stage_finalization | 31240 | 6538 | 0 | 24702 |
| case-01-00 | 3 | multi | 1 | ledger | role_initial | 15655 | 6547 | 6229 | 2879 |
| case-01-00 | 3 | multi | 2 | ledger | evidence_context_refreshed | 54478 | 6547 | 6229 | 41702 |
| case-01-00 | 3 | multi | 3 | ledger | tool_stage_finalization | 67974 | 6547 | 0 | 61427 |
| case-01-00 | 3 | multi | 4 | evidence | role_initial | 82619 | 6583 | 6229 | 69807 |
| case-01-00 | 3 | multi | 5 | evidence | evidence_context_refreshed | 83274 | 6583 | 6229 | 70462 |
| case-01-00 | 3 | active | 1 | coordinator | role_initial | 18896 | 9563 | 0 | 9333 |
| case-01-00 | 3 | active | 2 | policy | role_initial | 31198 | 9436 | 6229 | 15533 |
| case-01-00 | 3 | active | 3 | policy | evidence_context_refreshed | 40902 | 9436 | 6229 | 25237 |
| case-01-00 | 3 | active | 4 | policy | model_truncation_recovery | 34905 | 9436 | 0 | 25469 |
| case-01-00 | 3 | active | 5 | policy | schema_repair | 39177 | 9436 | 0 | 29741 |
| case-01-00 | 3 | active | 6 | critic | role_initial | 46759 | 9286 | 6229 | 31244 |
| case-01-00 | 3 | active | 7 | critic | model_truncation_recovery | 40762 | 9286 | 0 | 31476 |
| case-01-00 | 3 | single | 1 | single | role_initial | 15646 | 6538 | 6229 | 2879 |
| case-01-00 | 3 | single | 2 | single | evidence_context_refreshed | 23591 | 6538 | 6229 | 10824 |
| case-01-00 | 3 | single | 3 | single | tool_stage_finalization | 31584 | 6538 | 0 | 25046 |
| case-16-01 | 3 | active | 1 | coordinator | role_initial | 19008 | 9563 | 0 | 9445 |
| case-16-01 | 3 | active | 2 | evidence | role_initial | 35996 | 9280 | 6229 | 20487 |
| case-16-01 | 3 | active | 3 | evidence | evidence_context_refreshed | 42079 | 9280 | 6229 | 26570 |
| case-16-01 | 3 | active | 4 | evidence | tool_stage_finalization | 37664 | 9280 | 0 | 28384 |
| case-16-01 | 3 | active | 5 | critic | role_initial | 55385 | 9286 | 6229 | 39870 |
| case-16-01 | 3 | active | 6 | critic | model_truncation_recovery | 49388 | 9286 | 0 | 40102 |
| case-16-01 | 3 | active | 7 | critic | schema_repair | 53734 | 9286 | 0 | 44448 |
| case-16-01 | 3 | single | 1 | single | role_initial | 15758 | 6538 | 6229 | 2991 |
| case-16-01 | 3 | single | 2 | single | evidence_context_refreshed | 20972 | 6538 | 6229 | 8205 |
| case-16-01 | 3 | single | 3 | single | tool_stage_finalization | 29145 | 6538 | 0 | 22607 |
| case-16-01 | 3 | multi | 1 | ledger | role_initial | 15767 | 6547 | 6229 | 2991 |
| case-16-01 | 3 | multi | 2 | ledger | evidence_context_refreshed | 23729 | 6547 | 6229 | 10953 |
| case-16-01 | 3 | multi | 3 | ledger | tool_stage_finalization | 25256 | 6547 | 0 | 18709 |
| case-16-01 | 3 | multi | 4 | evidence | role_initial | 39579 | 6583 | 6229 | 26767 |
| case-16-01 | 3 | multi | 5 | evidence | evidence_context_refreshed | 42163 | 6583 | 6229 | 29351 |
| case-16-01 | 3 | multi | 6 | evidence | model_truncation_recovery | 36166 | 6583 | 0 | 29583 |
| case-16-01 | 3 | multi | 7 | policy | role_initial | 42727 | 6739 | 6229 | 29759 |
| case-16-01 | 3 | multi | 8 | policy | evidence_context_refreshed | 53239 | 6739 | 6229 | 40271 |
| case-16-01 | 3 | multi | 9 | policy | model_truncation_recovery | 47242 | 6739 | 0 | 40503 |
| case-16-01 | 3 | multi | 10 | critic | role_initial | 53619 | 6589 | 6229 | 40801 |
