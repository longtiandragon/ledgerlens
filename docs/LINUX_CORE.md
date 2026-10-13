# WSL Linux 核心子集复现

2026-10-13。Docker因宿主通信端点错误无法启动后，使用已安装Ubuntu WSL和Python 3.12.3完成了核心子集离线验证。验证提交 `562f255`，公开摘要见 [linux-core-validation.json](linux-core-validation.json)。这不是完整Linux安装、全量后端或独立机器验收。

## 结果

|范围|结果|
|---|---|
|原有合成规则基准|480/480标签完全匹配，含160正常例|
|独立手写参考例|30/30通过|
|金额匹配检查|24通过，实际使用 bounded_enumeration 回退实现|
|原恢复与v2清单检查|11＋21通过|
|新增真实符号链接边界|12通过，0跳过|
|整体专项|68通过、0失败、0跳过；最终pytest 1.15秒|
|规则指纹|Windows与Linux相同|

最终验证进程用时2.79秒（不含Windows打包和WSL启动时间）。源码归档393个文件前后字节不变；测试位于Linux临时目录，文件系统探针返回 `ext2/ext3`。不是在Windows挂载目录直接运行测试。所有日志和完整基准报告在进程退出前复制回 `.local/linux-core-v2`。

12项新增链接检查覆盖备份/恢复两个入口中的源根目录、业务数据库、检查点库、sources目录、原件文件、目标父目录；出现真实符号链接时拒绝执行，目标目录不发布。同样的12项在Windows环境因链接创建权限不足全部明确跳过，未当作通过。此轮Linux测试实际覆盖了这些边界。

匹配探针确认没有安装OR-Tools，生产既有回退路径将0.10＋0.20精确匹配0.30；回退不意味着完成分配或正式对账。Linux未加载Windows原生扩展，也没有使用模型输出。

## 环境与依赖

Ubuntu WSL已有Python，但没有主API所需的完整依赖，也没有Linux uv/Node。验收器从当前Git提交导出源码，不复制.env、本机运行数据、缓存和模型。只将已有Windows环境中、版本与uv.lock一致的五个 `py3-none-any` 测试包打成临时归档；检查purelib标记、RECORD哈希并保留许可证文件，Linux解压后再次校验。没有系统安装或联网下载，不是一次Linux锁文件安装。

|已有包|版本|许可证元数据|
|---|---|---|
|pytest|9.1.1|MIT|
|pluggy|1.6.0|MIT|
|iniconfig|2.3.1|MIT|
|packaging|26.3|Apache-2.0 OR BSD-2-Clause|
|pygments|2.21.0|BSD-2-Clause|

禁用pytest自动插件，仅运行列明的同步测试。没有将当前含Windows原生扩展的Hypothesis、OR-Tools或其他主后端依赖强行复制到Linux。既有许可证清单不变，没有新增项目依赖或分发第三方包。

## 复现与证据

仓库根目录、已有Windows主虚拟环境和Ubuntu WSL：

```powershell
.venv\Scripts\python.exe scripts/validate_linux_core.py --output .local/linux-core-new
```

输出必须不存在；验收器要求自身代码已经提交，拒绝运行与Git归档不一致的工作副本。留存源码/测试包ZIP、包版本和逐文件哈希、平台探针、每条规则及参考例完整结果、JUnit和日志。报告失败与程序异常不算通过。

首轮 `.local/linux-core-v1` 的480/30和68项已经通过，但后续探针发现其Linux临时目录消失，报pytest不可导入；没有把这个后续探针算作通过。修订验收器，在同一进程生命周期内探测平台并导出完整结果，v2再验通过，首轮记录保留。临时目录可能后续不可用，归档和报告保存在宿主项目目录。

后验汇总重新核验两份ZIP、包文件、日志、完整480条结果、30参考例和68项JUnit，并独立比较Windows当前规则指纹。它没有启动付费模型或原因调查v3新封存集。

仍未验证：完整Linux依赖重装、OR-Tools原生后端、全量API/浏览器、PostgreSQL/容器部署、无缓存联网安装、独立机器和远端CI。原Docker启动故障没有修改或绕过其宿主安全设置；诊断见 [cross-platform-readiness.json](cross-platform-readiness.json)。
