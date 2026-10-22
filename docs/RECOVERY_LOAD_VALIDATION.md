# 隔离并发与停写恢复验证

2026-10-22。本轮在全新合成 SQLite 中运行真实 HTTP 客户端、规则调查、备份和恢复，没有接触工作账库，也没有启动 Docker 或模型服务。结论限定为**同机、小数据量、单 API 进程、停写备份**；不是异机恢复或企业持续负载验收。

## 实际发现与修复

第一次并发验证出现 `database is locked` 及请求超时。`check-runs` 命中已有检查、`investigate` 命中活动调查时，已经取得账套写锁，却在返回前没有结束事务。锁一直保留到 FastAPI 依赖清理；事件循环中的调查同步写入可能等待这个锁，阻塞清理机会。

现在两个幂等分支先生成返回内容，再提交并释放锁。两项独立回归故意保持原 Session 打开，用第二个连接执行 `BEGIN IMMEDIATE`：修前两项均因锁定失败，修后通过，并验证检查、调查和审计记录没有重复，重复调查没有再次派发。

随后两次实跑仍有 HTTP `ReadError`，报告全部保留。周期调查间隔 5 秒，恰与服务端及客户端默认空闲连接期限相同；没有服务端应用错误日志。将验收客户端空闲连接期限设为 2 秒、早于服务端 5 秒后，两次 120 秒运行均通过。连接期限竞争是根据时序和对照提出的解释，不能把两次成功扩大为消除了所有网络故障。请求超时仍为 25 秒，**没有传输重试或忽略失败请求**。

## 最终有限负载结果

固定输入为两行不平衡合成凭证，独立预期差额 `10.00`；两个读／检查客户端每次操作后间隔 250 毫秒，另一个客户端周期启动规则调查。最终运行使用 `time.perf_counter` 测延迟，避免 Windows 低分辨率单调时钟产生大量 0 毫秒读数。

|项目|结果|
|---|---|
|持续时间|120 秒|
|读记录／读快照／重复检查／读概览|902 次，失败 0|
|完整规则调查|23 次，全部完成，差额均为 `10.00`|
|四类请求 P95|7.05 / 6.74 / 10.16 / 8.99 毫秒|
|调查完成时间 P95|235.60 毫秒；最大 1727.53 毫秒|
|原始两行记录与快照|运行前后保持|
|模型或外网调用|0；服务进程有拒绝探针|

这不是吞吐上限：有主动间隔、只有两行数据、没有 OCR、模型推理、PostgreSQL 或 Celery；同机其他测试也在运行。P95 不能外推到真实部署。

## 恢复与中断

负载结束后，先正常停写，再备份至独立目录、恢复到新的存储目录、启动新服务。逐项核验业务行、检查点行、HTTP 视图、已完成调查和下载原件哈希，全部一致；原合成源库和备份未被恢复过程修改。

另以真实子进程在 `detect`、`finalize` 检查点后退出，再从副本恢复。验证预算计数保持、终态检查点复用、重复派发不增加有效产物、第二次重启不改变结果。该运行器原来遗漏新增的 `Request` 参数，初次种子创建失败；已修复参数传递并保留失败报告。

最终机器摘要：[recovery-load-validation-v2.json](recovery-load-validation-v2.json)。原始失败包括 `runtime/recovery-load-v1/crash-before-fix`、`http-load-120s`、`runtime/recovery-load-v2-lock-fix` 和 `runtime/recovery-load-v3-instrumented`，均不覆盖。

## 复现

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/test_idempotent_lock_release.py -q
.venv/Scripts/python.exe scripts/validate_recovery_load.py --output .local/recovery-load-new --seconds 120 --clients 2
.venv/Scripts/python.exe scripts/validate_restored_run.py --output .local/recovery-crash-new
```

输出目录必须尚不存在；启动器只终止自己创建的服务。运行失败也落盘错误与部分进度，不把清理成功当成验收通过。

截至10月22日，上述本地结果未覆盖异机／空白机器恢复、在线或时间点恢复、断电、多进程并发升级、大账套长时间负载和真实模型任务恢复。10月23日新增的有限跨执行主机结果见下段；其他边界继续保留。

2026-10-23追加：[跨执行主机恢复](CROSS_HOST_RESTORE.md)已完成Windows源端→Linux目标端实跑；本机源端prepare成功、同机verify被拒绝、32项边界回归保留。提交 `2d42f97` 的[远端运行](https://github.com/longtiandragon/ledgerlens/actions/runs/39823414917)中，跨主机恢复、detect/finalize两个真实中断点恢复、120秒有限负载分别通过；该次负载为939次读／检查、24次规则调查、0失败。本段不覆盖上面的历史同机报告，也不代表企业容量。四份原始报告、哈希和源码版本见[远端摘要](cross-host-restore-validation.json)。
