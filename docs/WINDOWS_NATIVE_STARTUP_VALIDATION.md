# Windows 原生依赖初始化复验

2026-10-23。对提交 `52c1a69` 的实际源码 ZIP 在新目录安装依赖后，默认 Chrome 验收暴露首次匹配请求阻塞。此前两轮远端 CI 通过仍为历史事实，不能覆盖本次失败。

## 观察与修复

- 初次整组运行没有生成最终报告，保留为中断且已观察到失败，不计算通过数。
- 全新服务的工作台定向复现：0通过、1失败，其余3项因首错停止而跳过；匹配请求没有返回，后续页面等待初始化。
- 对照探针中，直连健康检查返回200，直连匹配超时，代理请求也超时。守护记录没有外网或模型请求。
- 线程栈显示首次匹配在工作线程导入 OR-Tools／NumPy 原生模块，同时服务主线程等待新线程启动。此证据来自本机 Windows、Anaconda Python 与隔离验收服务，不宣称所有平台都存在同一问题。
- 仅在 lifespan 内初始化仍然超时；进一步提前到应用模块加载、辅助线程创建前，原工作台测试4/4通过。修改只改变可选依赖初始化时机，缺少依赖仍保留匹配器既有的有限枚举回退；未改变匹配计算和测试预期。

## 候选复验

完整默认浏览器集：56通过、13跳过、0失败、0 flaky，130.990秒。初始库为空，服务正常停止；除候选 `backend/app/main.py` 外，解压源码仍与原始 ZIP 一致。候选文件SHA-256为 `83f5098af061e9fb36914277babfb822dc0904b359207111d2dee8d84d07232a`。

本地证据目录均保留：`.local/delivery-browser-52c1a69`（中断）、`delivery-browser-workbench-52c1a69`（复现失败）、`delivery-browser-probe-52c1a69`（请求对照）、`delivery-browser-stack-52c1a69`（线程栈）、`delivery-browser-fixed-workbench-52c1a69`（lifespan尝试失败）、`delivery-browser-preload-workbench-52c1a69`（4/4）和 `delivery-browser-preload-full-52c1a69`（56/13）。后六项也均位于 `.local/`。

原交付 ZIP 没有被覆盖，仍代表修复前提交；候选复验不自动成为该旧包的通过证明。PR #57 暂退回草稿，待修复版验证与交付更新。27个冻结v5源码哈希不变，未新增模型请求。

本地后端全量：2663通过、25跳过、1条既有JUnit警告，282.78秒；报告为 `.local/reports/native-preload-full.xml`，日志同名 `.log`。Ruff与diff检查通过。该修复的远端CI结果以PR最新提交检查为准，不能沿用 `52c1a69` 的绿灯。
