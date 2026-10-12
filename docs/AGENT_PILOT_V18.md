# v18 终态、执行覆盖与离线成本复核

复核日期：2026-10-09。仅读取 `.local/reports/deepseek-pilot-v18.json`、
`runtime/deepseek-product-multi-v18.json` 及成本计算代码。复核没有调用模型 API、读取 `.env`、查看私有推理或修改原始实验报告。

## 真实终态与执行口径

pilot 已以 `completed_with_failures` 结束，development 数据集版本 1.0.0，计划为两个案例 × 三次重复 × 四方法，共 24 行。耗时 127.360 秒。
报告创建于北京时间 2026-10-09 00:56:51；文件末次写入于 00:58:58。

24 行中，规则方法实际计算 6 行；18 个模型方法样本只有 3 行发起了请求，其中完成 1 行、执行后失败 2 行。
其余 15 行在服务拒绝后明确记录为 `provider_unavailable_not_attempted`，没有再发起模型请求。
所有模型实际执行均为正常案例 `case-01-00` 的第一次重复；缺口案例 `case-16-01` 没有任何模型方法实际执行。

| 方法 | 计划样本 | 实际执行 | 完成 | 执行后失败 | 未执行 | 物理请求 | 已报告 Token | 未结算预留 | 工具 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rules | 6 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| single | 6 | 1 | 1 | 0 | 5 | 4 | 20,884 | 0 | 5 |
| multi | 6 | 1 | 0 | 1 | 5 | 10 | 141,606 | 0 | 41 |
| active | 6 | 1 | 0 | 1 | 5 | 1 | 未知：无用量响应 | 24,540 | 0 |

这里“实际执行”指模型样本至少已发起一次物理请求，包括被 HTTP 402 拒绝的 active；它不表示模型已返回结果。
不能称 multi “真实运行了六次且六次失败”，也不能称 active “跑了六次”。报告组内 failures 包含未执行行。

- single：完成正常案例一次，规则与状态匹配。第三个输出存在无效政策引用，一次受限 schema 修复后通过。
- multi：ledger、evidence、policy 三角色完成；critic 首批查询没有继续提供新资料，准备非思考整理时，第 11 个逻辑 chat 被 Token 保守预留拒绝，终态为 `budget_exhausted`，没有完成 report。
- active：coordinator 首次物理请求返回 `model_access` / HTTP 402，没有模型用量响应或已接受意见；此后同配置模型样本停发。
- rules：两个案例各三次完成，规则与状态匹配；这仍是有限合成案例结果，不是完整会计准则或真实企业正确率证明。

累计 16 个逻辑 chat、15 个发送前授权物理尝试、14 个模型用量响应、0 次传输重试。
已报告 Token 合计 162,490，另有 active 24,540 的保守未结算预留。
Token 预留是请求字节上界与输出上限的安全额度，不能当作服务已报告的消耗。

## 报告指标的准确解释

报告将 24 行全部列为 scorable，并将未执行或失败行的精确规则和状态匹配记为失败。
因此下表是计划运行是否交付正确结果的比例，不能当成“模型已经回答后的识别正确率”。

| 方法 | 报告 completed/failures | 精确规则匹配 | 状态匹配 | 正常误报 | 正常完成 |
|---|---:|---:|---:|---:|---:|
| rules | 6/0 | 100% | 100% | 0% | 100% |
| single | 1/5 | 16.7% | 16.7% | 0% | 33.3% |
| multi | 0/6 | 0% | 0% | 0% | 0% |
| active | 0/6 | 0% | 0% | 0% | 0% |

multi/active 的正常误报 0% 伴随正常完成率 0%；active 没有已接受模型意见。这些 0% 不构成无误报能力证据。
单次正常样本通过、部分角色完成或上下文缩短都不能推断 v18 优于 v17；异常检出、重复稳定性、active 能力和固定 multi 完成能力仍未获得充分真实验证。

## 产品 multi 验收

产品验收于北京时间 2026-10-09 00:57:14 至 00:59:06 运行，111.656 秒后结束。
验收 `failed`、调查 `failed`，底层错误为 `model_access` / HTTP 402，属于服务访问失败。

- 12 个模型授权物理尝试，其中 11 次返回已记录用量，1 次 HTTP 402 未知用量。
- 已报告 127,646 Token、25 次工具调用；未知请求保留 59,524 的保守预留。
- 3 次 agent_completed、4 次 agent_started，没有完成五角色闭环。
- 原账前后均为 13 条，SHA-256 均为 `d2bdae09df17ed6fbb835d92ca6b8bfd36e6d4adc211487a1fa8da4362a43900`，`original_records_unchanged=true`，快照未陈旧。

原账只读检查通过不能把整体验收失败表述为成功，也没有证明会计结论正确。
本产品验收与 pilot 是两个独立作业；下文成本文件只覆盖 pilot 的 15 个尝试，不包含产品的 12 个尝试。

## 新增离线成本报告

已在确认目标不存在后执行：

```powershell
.\.venv\Scripts\python.exe -m benchmarks.costs --report .local\reports\deepseek-pilot-v18.json --output .local\reports\deepseek-pilot-v18-costs.json
```

新文件为 `list-price-estimate-v1`；使用现有 2026-10-08 价格快照及 Decimal 计算，金额字段为字符串。
这是已报告用量在峰时与非峰时目录价下的离线区间，不是账单、余额、确定收费或当前价格保证；没有人民币转换或对请求实际时段的推断。

| 方法 | 物理尝试 | 已估价响应 | 未知尝试 | 已知用量 USD 下界 | 已知用量 USD 上界 | 用量成本覆盖完整 |
|---|---:|---:|---:|---:|---:|---|
| rules | 0 | 0 | 0 | 0 | 0 | 是：没有模型请求 |
| single | 4 | 4 | 0 | 0.003836634 | 0.007673268 | 是：仅指已发出尝试的用量 |
| multi | 10 | 10 | 0 | 0.026103366 | 0.052206732 | 是：仅指已发出尝试的用量 |
| active | 1 | 0 | 1 | 0：已知部分为空 | 0：已知部分为空 | 否 |

pilot 已知用量目录价合计为 USD 0.029940000–0.059880000；另外存在一个无法定价的 HTTP 402 尝试。
该区间的上界只适用于已知用量，不是整个 pilot 费用上界。
active 的已报告 tokens=0、已知费用字段为字符串 "0"，分别表示没有已报告 Token 与已知部分为空，绝不能改写成该请求没有消耗或免费。
产品 HTTP 402 的用量与费用同样未知；59,524 预留不能当实际消耗，也不能据此反推出确定费用。
15 个未执行模型样本有明确未发送记录，其零尝试不能用来证明 active/multi 更省费用或更有增益。

成本报告的源实验 SHA-256 为 `8d57ca5bdd88ef3ce503af3aba49662154d8ab06eb6db8dcfd83a14babaa4e2e`，与复核时的原报告一致。
价格快照 SHA-256 为 `7a194bbf31ea5be5772d561754a8f6561aaa1201100b96459bcfbde795e12ea4`；来源元数据为 DeepSeek 官方价格页面，快照核验日期 2026-10-08。
成本生成使用排他创建，未覆盖旧实验、旧价格或既有成本报告。

## 结构观察与尚未验证部分

v18 multi 本次首请求 context_bytes 为 13,447 B，v17 相同案例第一次重复的首请求为 15,655 B，静态协议/参数传输确实减少 2,208 B（约 14.1%）。
但 v18 multi 后期仍达到 68,557 B，已有 41 次工具调用，最终依旧因下一请求无法保守预留而停止。
减少协议重复尚未消除共享查询结果、程序观察和角色意见持续增长；这次单个失败轨迹不能证明平均成本、整体性能或准确率的改善。
可以据此继续离线研究职责限定、证据目录与可追溯按需读取；不能删除负面核验/未取得资料、减弱校验、提升预算或把程序生成的答案提供给运行 Agent。

报告记录的八个源码 SHA-256 在本次复核时均与工作区一致，包括 investigation、model_gateway、context_transport、model_contract、business_chain、evidence_planning、evidence_selection 和 benchmark runner。
该结论只覆盖报告记录的文件和复核时点，不代表整个仓库或后续改动均未变。

## 24 行记录

下面逐行区分已计算、已发起请求与服务拒绝后的未执行；错误行仍按照实验原评分保留。

| Case | Repeat | Method | Execution | Error | Rules/status match | Physical calls | Reported tokens | Reserved | Tools |
|---|---:|---|---|---|---|---:|---:|---:|---:|
| case-01-00 | 1 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-01-00 | 1 | single | completed | - | True/True | 4 | 20884 | 0 | 5 |
| case-01-00 | 1 | multi | attempted_failed | budget_exhausted | False/False | 10 | 141606 | 0 | 41 |
| case-01-00 | 1 | active | attempted_failed | model_access | False/False | 1 | unknown (0 reported) | 24540 | 0 |
| case-16-01 | 1 | single | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 1 | multi | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 1 | active | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 1 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-01-00 | 2 | single | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 2 | multi | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 2 | active | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 2 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-16-01 | 2 | multi | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 2 | active | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 2 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-16-01 | 2 | single | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 3 | multi | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 3 | active | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-01-00 | 3 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-01-00 | 3 | single | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 3 | active | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 3 | rules | computed | - | True/True | 0 | 0 | 0 | 0 |
| case-16-01 | 3 | single | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |
| case-16-01 | 3 | multi | not_attempted | provider_unavailable_not_attempted | False/False | 0 | 0 | 0 | 0 |

## Physical attempt metadata

Only metadata is retained here; no prompt body, API credential or private reasoning is shown.

| Method | Request | Role | Stage | Context bytes | Reserved tokens for dispatch | Usage response present |
|---|---:|---|---|---:|---:|---|
| single | 1 | single | role_initial | 13443 | 21635 | True |
| single | 2 | single | evidence_context_refreshed | 16503 | 24695 | True |
| single | 3 | single | tool_stage_finalization | 14770 | 22962 | True |
| single | 4 | single | schema_repair | 14782 | 22974 | True |
| multi | 1 | ledger | role_initial | 13447 | 21639 | True |
| multi | 2 | ledger | evidence_context_refreshed | 21651 | 29843 | True |
| multi | 3 | ledger | tool_stage_finalization | 24200 | 32392 | True |
| multi | 4 | evidence | role_initial | 32984 | 41176 | True |
| multi | 5 | evidence | evidence_context_refreshed | 43034 | 51226 | True |
| multi | 6 | evidence | tool_stage_finalization | 43138 | 51330 | True |
| multi | 7 | policy | role_initial | 49381 | 57573 | True |
| multi | 8 | policy | evidence_context_refreshed | 59120 | 67312 | True |
| multi | 9 | policy | tool_stage_finalization | 57363 | 65555 | True |
| multi | 10 | critic | role_initial | 68557 | 76749 | True |
| active | 1 | coordinator | role_initial | 16348 | 24540 | False |
