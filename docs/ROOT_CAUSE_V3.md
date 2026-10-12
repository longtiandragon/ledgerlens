# 原因调查 v3 接续实验

更新：2026-10-12。实现与离线验证已经完成，真实模型冒烟尚未运行，不能据此宣称模型效果提升。v2 的失败报告、封存结果和负面结论保留。

## 输出接口

`investigation-v26` 为 `root_cause` 使用独立的窄契约：`status` 和必填 `cause_assessment`，后者只含类别、简短解释、最多六条证据引用及可选补证请求。主动模式另允许有界 `evidence_plan`。不再要求模型重复提交 `claims/rule_id/calculation`。

服务端仍拒绝快照外引用、未读引用、重复引用、未知类别、类别与状态矛盾、不可见补证编号、额度用完后的补证及未知字段。确定原因须至少引用一条已读证据；证据不足和超出范围不得输出确定类别。新快照直接提供的补证原文必须逐字段等于该快照中的记录，裸编号或改写内容不能取得“已读”资格。规范化后的存储结构继续区分程序规则事实与未经核验的模型原因解释；不生成新业务事实或调整分录。

旧调查任务继续使用完整契约。单 Agent、固定多 Agent、主动调查及两轮补证路径均有离线回归。

## 新数据与冻结边界

`datasets/generate_v3.py` 生成270例：9个原因情形 × 3个划分 × 10个变体。48条新措辞互不重复且不复用v2文本，拆分结构、金额与证据编号也更新。沿用已公布的故障机制与人工真值定义，**属于同一作者的合成复现实验，不能称为独立真实业务验证**。

数据位于 `.local/datasets-v3`；manifest SHA256：`7c78e480895d197335cff8ef02be997f0caeb2868d948d93b500d9a69b435971`。可见资料、扣留资料、评测真值分别保存并散列；非空目录拒绝覆盖。模型运行前核验所选可见/扣留文件，全部预测结束后才读取并核验真值。新契约运行器拒绝在v2数据上调用模型。

本轮仅运行确定性基线，关键词表保持v2原定义，未根据v3验证结果修改：

| 方法 | 开发集原因F1／结论准确率 | 验证集原因F1／结论准确率 |
|---|---|---|
| 仅规则 | 0.1458／77.78% | 0.1458／77.78% |
| 固定补证 | 0.5625／77.78% | 0.5625／77.78% |
| 关键词补证 | 0.9583／100% | 0.5852／77.78% |

报告：`.local/reports/root-cause-v3-development-baselines.json`、`.local/reports/root-cause-v3-validation-baselines.json`。开发集供契约冒烟，不用其成绩证明泛化；验证基线已查看，新封存集尚未运行，不得查看结果后调参。

## 用户暂缓的开发集冒烟

2026-10-12用户明确选择“暂不付费，继续离线工作”。本计划保留但不执行；不会因文档中存在运行命令而发起请求。

冻结配置：[root-cause-v3-smoke-plan.json](root-cause-v3-smoke-plan.json)，固定9个案例、源代码哈希、manifest哈希、模型与运行参数。修改配置或代码后旧计划校验失败，须重新准备和复核。

- 单Agent每案一次，另有三个零费用基线；最多60次实际请求（包括重试、结构修复和第二轮）。
- 预计30–60万Token，低谷费用约¥0.50–0.90，估计来自旧冒烟，不能保证完成率。
- 发请求前按官方人民币**高峰价、全部输入按缓存未命中**保守预留费用，上限¥1.20；已知用量结算、未知用量保留全额预留。输入按UTF-8请求字节加4096预留，输出按请求上限预留。若服务端报告突破预留上界则保留真实用量并停止；不是服务商账单认证。
- 仍只在低谷运行，并使用外层 `guard_paid_run.py`。官方价格于2026-10-12核验：[DeepSeek人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。外层旧脚本的美元换算仅为附加估计，新运行器内部人民币预算负责发出请求前的限制。
- 每次实际派发与用量及时写入报告供守护读取；中断、失败、费用耗尽全部保留，不替换为成功。

批准后命令（运行前核实服务端时间、余额和 `.env` 模型为 `deepseek-flash`）：

```powershell
.venv\Scripts\python.exe scripts/guard_paid_run.py F:\Code\Project\Project18 1.20 .local/reports/root-cause-v3-smoke-v26.json .venv\Scripts\python.exe -m benchmarks.root_cause_v2_models --dataset .local/datasets-v3 --split development --per-family 1 --modes rule_only rule_fixed_request keyword_request single --repeat 1 --max-paid-requests 60 --max-cny 1.20 --plan docs/root-cause-v3-smoke-plan.json --env-file .env --report .local/reports/root-cause-v3-smoke-v26.json
```

该计划只覆盖开发冒烟。新封存集3次重复的最终对比须另行冻结并批准费用；开发冒烟有结构失败时先分析，不自动重复已失败配置。

## 离线机制消融

`benchmarks/mechanism_ablations.py` 调用真实调查图和模拟函数；预期由独立手写夹具给出，不由生产函数生成。完整报告：[mechanism-ablation-results.json](mechanism-ablation-results.json)。

| 比较 | 保留机制 | 去掉机制 |
|---|---|---|
| 4个时间挑战（含2个负对照） | 历史状态正确4/4，错误判正常0/4 | 正确2/4，错误判正常2/4 |
| 6份草稿，其中5份有错 | 程序复算放过错误1/5，总判定正确5/6 | 直接相信建议放过错误5/5，总判定正确1/6 |

复算识别了影响说明差一分、借贷不平、锁定期间和未知科目；**合法科目之间的错误业务用途仍可通过**。全部源资料哈希保持一致，模型请求0次。此处只是固定挑战的机制覆盖，不是模型消融、真实错误率或原因分类提升。去掉反证阶段、真实模型建议到模拟的闭环效果仍未测量。
