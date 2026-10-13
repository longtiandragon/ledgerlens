# 恢复副本中的中断调查续跑

2026-10-13。补验 [备份恢复](BACKUP_RESTORE.md) 未覆盖的任务重启环节。两个独立合成库、两个真实进程退出位置全部通过，用时17.38秒；Ruff通过。公开摘要见 [restored-run-validation.json](restored-run-validation.json)。

## 如何复现

从仓库根目录运行，输出目录必须不存在，失败目录保留：

```powershell
.venv\Scripts\python.exe scripts/validate_restored_run.py --output .local/restored-run-new
```

脚本在新目录内创建合成账套，通过真实导入、检查和调查创建事务形成队列任务。后台任务尚未执行时，交给独立子进程运行生产 execute_run。仅验收器包装检查点写入，在 SQLite 提交之后以退出码79立即退出，跳过正常清理；生产代码没有故障开关。

父进程确认子进程已经退出且调查仍为 running，使用正式 backup.py 和 restore_local.py 恢复到全新目录。另一独立进程调用应用的 lifespan 启动恢复任务，等待完成，再重复派发两次并模拟第二次启动。没有启动 HTTP 监听服务，没有连接现有工作库、Docker 或远端服务。子进程清空密钥、禁用模型构造和外部网络；Windows asyncio 的内部回环连接允许使用。

## 独立预期与结果

合成凭证两行：借方100.00、贷方90.00，手写预期差额 **10.00**。恢复前预置预算 `calls=3, tokens=123, tools=2, reserved_tokens=37`，只用于验证历史计数不会被恢复过程清零；这些是**人工合成计数，不是实际模型消耗**。实际模型请求为0。

|进程退出位置|恢复结果|
|---|---|
|detect 检查点已提交、尚无最终结果|从同一检查点继续，结果 supported，差额10.00|
|finalize 最终结果检查点已提交、Run尚为running|复用检查点中的完整结果，不重新计算该结果|

两种情况下均验证：

- 恢复副本与退出后的业务表逐行一致（仅Document存储路径重定位）；检查点两张表逐行一致。
- 恢复读取的检查点ID等于故障退出前刚提交的ID，预算四项值保持原样。
- 最终租约和active_slot释放，deterministic_check事件恰好一次。
- 两次并发重复派发及第二次启动后，整个Run字段完全不变，第二次启动没有待恢复任务。
- 除agent_runs外所有业务表保持原值，原合成库不改动，原备份哈希清单仍通过。

原始证据位于 `.local/restored-run-v2`，每个场景保留 seed/crash/recover JSON 和进程日志。首次 `.local/restored-run-v1` 因验收器把 Windows 事件循环 socketpair 回环连接也视为外部联网而失败；放行回环后重跑通过。没有修改产品逻辑、手写差额或预算预期。

## 结论边界

本次证明这两个确定性调查中断点在新目录恢复后可以续跑或复用结果，并保持重复派发幂等。没有模拟断电、SQLite提交中途损坏、跨文件并发备份、Celery/PostgreSQL恢复、OCR/检索任务恢复或真实模型请求中断。应用 lifespan 在进程内实际执行，未覆盖 HTTP 服务进程管理。此前工作库199个历史快照的只读核验与本次新合成任务续跑是两组不同证据，不能声称已重跑所有历史调查。
