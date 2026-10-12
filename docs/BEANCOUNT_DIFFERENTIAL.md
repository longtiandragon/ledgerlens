# Beancount 基础账务差分

本验证只比较单一 CNY、明确金额到分、无成本与价格注释的总账余额，以及借贷不平衡的拒绝行为。它不提供企业会计准则或小企业会计准则的专业判断标准。

`benchmarks/beancount_differential.py` 包含两份独立手写的业务表示：Beancount 文本和 LedgerLens 分录。参考值由第三方 `loader.load_string`、`ops.validation.validate`、`ops.summarize.balance_by_account`、`Inventory.get_currency_units` 产生。生产 `app.domain.balances` 只参与实际值计算；参考值不使用它，也不从生产分录生成 Beancount 文本。

账例覆盖分币加法、大金额最后一分、正常分次结算、退款冲销、连续三个月的独立期初结转、一分借贷不平衡，以及“借贷平衡但科目选错”的边界。Beancount 余额截止日期为排他边界；各月生产记录只包含当月明确期初与当月分录，没有同时加入此前分录而重复结转。配置验证容差为零，并禁用本验证范围之外的 include、plugin、自动金额推断、多币种、价格和成本注释。

独立回归测试会故意改动生产计算函数和另一侧的分录金额，确认第三方参考值不会随之变化；它们不修改已知答案来迁就实现。余额守恒不能发现业务科目选择错误，边界账例明确展示两套算术检查均接受一笔错科目分录。

## 可选工具安装与复现

Beancount 没有加入应用主依赖或主锁文件，仅在本地评测环境使用。缺少工具时 CLI 返回失败状态，相关第三方测试明确跳过，不能作为通过记录。

```powershell
uv pip install --python .venv\Scripts\python.exe --index-url https://pypi.org/simple --cache-dir F:\Code\Project\Project18\.cache\uv -r benchmarks\requirements-beancount.txt
.venv\Scripts\python.exe -m benchmarks.beancount_differential
.venv\Scripts\python.exe -m pytest backend/tests/test_beancount_differential.py -q
```

实际结果、版本、来源和每例差异保存在 `docs/beancount-validation-summary.json`。安装命令针对 F 盘现有虚拟环境，未安装到 C 盘全局 Python，也未修改前端环境。

首轮实际运行是 8/9 通过、余额差异为零：分币账例的生产表示误写成三个凭证号，第三方表示是一张凭证的三条 posting。修正为同一个凭证号，并给两条同科目借方记录保留唯一行 ID，没有改变第三方文本或金额答案。首次失败记录保存在本地 `.local/beancount/first-trial-summary.json`；最终报告通过实际重跑生成。

## 来源与许可

核验日期：2026-10-08。[官方仓库](https://github.com/beancount/beancount)说明 v3 是当前稳定分支，v2 已冻结；[官方 PyPI 3.2.3 发布](https://pypi.org/project/beancount/3.2.3/)提供 Windows CPython 3.12 wheel，发布时间为 2026-05-05。实际安装 Beancount 3.2.3，元数据许可证为 GPL-2.0-only；[官方 COPYING](https://raw.githubusercontent.com/beancount/beancount/v3/COPYING)保留原许可。

本次安装新增的间接依赖 regex 2026.9.29，其安装包元数据及 LICENSE 为 `Apache-2.0 AND CNRI-Python`。原有 click 8.5.0 为 BSD-3-Clause，python-dateutil 2.9.0.post0 为 Apache-2.0/BSD 双许可；运行结果记录实际版本，不把第三方许可改成项目 Apache-2.0。

没有复制、修改或重新分发第三方源代码。Beancount 不进入业务服务导入链，也不随应用运行镜像打包。本项目编写的差分脚本与虚构账例记录独立来源；将来分发评测环境或第三方依赖时仍需随其实际版本履行各自许可条件。
