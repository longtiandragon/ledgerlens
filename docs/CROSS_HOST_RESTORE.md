# Windows 到 Linux 的恢复验收

2026-10-23。新增跨执行主机的合成资料恢复协议；通过与否以同一 GitHub Actions 运行中的两阶段报告为准。没有报告时，不把工作流实现算作验收通过。

`quickstart-windows` 在 Windows 2025 runner 中创建全新 SQLite 及两行合成凭证，执行真实规则调查后停止写入，生成备份、预期数据及文件哈希。随后 `cross-host-restore` 在另一个 Ubuntu 24.04 runner 中下载该备份，在新目录恢复并启动真实 API，对账业务行、检查点、调查结果和来源下载。目标阶段必须核验来源/目标主机标识及源码哈希；同机重跑不能记为异机通过。

独立的最小计算预期是借方 `100.00`、贷方 `90.00`、差额 `10.00`。备份搬运不能改写该预期。运行器只使用隔离目录，模型密钥为空，外网与模型调用被拒绝；输入清单、文件或预期缺失均判失败。原 bundle 必须保持不变。

GitHub 作业间传递使用官方 [workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)；新增的 [download-artifact](https://github.com/actions/download-artifact) 使用 MIT 许可证，不新增应用运行时依赖。下载设置摘要不匹配时报错，脚本仍独立核对内容哈希。

## 本地及两台机器复现

源机器，输出目录必须不存在：

```powershell
.venv/Scripts/python.exe scripts/validate_cross_host_restore.py prepare --output .local/cross-host-source
```

把 `.local/cross-host-source/bundle` 整个目录复制到另一台机器；使用相同源码版本和锁定依赖，再执行：

```sh
.venv/bin/python scripts/validate_cross_host_restore.py verify --bundle /path/to/bundle --output /path/to/new-result
```

两阶段的 `report.json` 分别保留成功或失败；来源机器的 bundle 不能原地覆盖。工作流另执行真实进程中断恢复，以及固定 120 秒、两读客户端加周期规则调查的有限负载。它们是不同检查，不合并成企业压力测试。

## 证据边界

这是同版本 SQLite、合成小账套、停写备份与两个 CI 执行主机之间的迁移验证。主机标识和工作流记录不证明物理硬件独立性，也不证明企业大账套、在线/时间点恢复、断电恢复、PostgreSQL/Celery 或真实模型任务恢复。原有本机失败记录继续保留。

## 2026-10-23 远端执行结果

[PR #57](https://github.com/longtiandragon/ledgerlens/pull/57) 的[运行39823414917](https://github.com/longtiandragon/ledgerlens/actions/runs/39823414917)九项任务全部通过。分支提交为 `2d42f97e973122a1ec6d736eefbcb337eb2431e2`，PR测试合并提交为 `30313e29e8251dfe705cd5ac96dc375d674a224c`。源／目标报告的全部后端源码及恢复脚本哈希相同，归档时也逐项核对了分支提交中的文件；后续文档提交不冒充这一次测试版本。

|独立检查|实跑结果|原始报告|
|---|---|---|
|Windows源端备份|prepared，4.06秒，停止写入后业务行和非空检查点一致|[source.json](evidence/ci57-39823414917/source.json)|
|Linux目标端恢复|passed，2.21秒，不同执行主机；业务行、检查点、API视图、调查、检查ID、原件下载及bundle不变性均通过|[target.json](evidence/ci57-39823414917/target.json)|
|真实进程中断恢复|detect与finalize两点均以退出码79中断并恢复，重复派发和第二次重启保持不变，10.06秒|[crash.json](evidence/ci57-39823414917/crash.json)|
|有限并发负载及停写恢复|固定120秒、两读客户端及周期规则调查；939次读／检查、24次调查、0失败；含恢复共123.06秒|[load.json](evidence/ci57-39823414917/load.json)|

四份报告的模型请求均为0。源端与目标端使用同一bundle清单，SHA-256为 `2304a8a1d9b7a6c9e693fa09b4570cafaae4d24f2de464991662e80d87ce7fbc`。报告原始字节与各自SHA-256长期保存在[机器摘要](cross-host-restore-validation.json)及仓库内，无需依赖30天后会过期的Actions附件；哈希仅证明内部一致性，不证明会计结论或作者身份。
