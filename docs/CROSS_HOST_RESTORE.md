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

这是同版本 SQLite、合成小账套、停写备份与两个 CI 执行主机之间的迁移验证。主机标识和工作流记录不证明物理硬件独立性，也不证明企业大账套、在线/时间点恢复、断电恢复、PostgreSQL/Celery 或真实模型任务恢复。实跑结果另行追加，原有本机失败记录继续保留。
