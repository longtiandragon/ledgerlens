# 本地运行观测

OpenTelemetry SDK 1.45.1记录每次成功取得执行权的调查运行，默认启用，`TELEMETRY_ENABLED=false`可关闭。落地位置为`DATA_DIR/telemetry/<trace>-<span>.json`，默认在Git忽略的`runtime/telemetry`。没有网络Exporter、自动HTTP埋点或Langfuse连接；环境中的OTEL服务地址不会被此实现使用。

采用独立SDK Provider和本地Exporter，参考[官方Python手动埋点文档](https://opentelemetry.io/docs/languages/python/instrumentation/)。不设置全局Provider，避免自动采集第三方调用或环境Resource。独立文件以排他方式创建，写入失败记录固定告警，财务处理结果不由观测成功与否决定。

## 数据含义

- 一份span对应一个Worker租约，记录单调时钟测得的耗时、终态、运行/租约ID的SHA256。排队时间不包含在内；强制杀进程可能不产生完整span。
- 同一运行恢复后可有多个span，用量是**整个运行累计值**。汇总选择最新开始租约的累计用量，不把每份span的费用相加；租约耗时之和不代表端到端墙钟时间。
- 只保留结构化计数。账务原文、模型输入输出、私有推理、密钥、异常正文不进入观测文件。日志记录的是本地文件故障告警，不附带业务内容。
- 费用为固定2026-10-12官方人民币高峰价、所有输入视作缓存未命中的保守估计，金额为Decimal字符串，依据[官方定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。只为服务实际返回且已登记的模型计价，不用配置模型替代；不是实际低谷费用或服务商账单。
- 未返回用量、未登记模型或失败请求列为`unknown_cost_attempts`。部分已知则只给`known_usage_peak_estimate_cny`；完全未知不输出金额。明确0次模型请求才可记录0费用。
- 重复派发终态运行不产生新span；丢失租约返回`lease_lost`，不把新Worker结果记成旧Worker成功。

## 离线验证与限制

12项专门回归覆盖实际SDK写出、敏感文本不落盘、Decimal估计、超时未知费用、恢复累计费用去重、重复派发、文件故障以及显式关闭。首次测试发现Windows墙钟分辨率导致短span时长为0，改用单调时钟差计算耗时后通过；未放宽测试预期。Worker测试同时把观测目录隔离到临时目录。

实际本地HTTP创建合成账套并执行规则调查，11项知识/快照/原账只读检查通过，匹配到一份真实SDK span，模型请求0次、费用0。公开结果见[observability-validation.json](observability-validation.json)，完整验收在`.local/reports/knowledge-runtime-v26-observability.json`。真实付费模型、远端Collector、Langfuse、分布式跨进程父子链与崩溃后的不完整span恢复尚未验证。

汇总命令只读本地文件，已有报告不可覆盖；空目录返回`no_data`，损坏文件保留在`failures`并以非零退出，不能伪装为成功的零运行：

```powershell
.venv\Scripts\python.exe scripts/summarize_telemetry.py --output .local/reports/local-telemetry-new.json
.venv\Scripts\python.exe -m pytest -q backend/tests/test_observability.py backend/tests/test_runtime_ownership.py
```
