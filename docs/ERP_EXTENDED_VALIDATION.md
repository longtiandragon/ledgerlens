# ERPNext 扩展总账契约验证

2026-10-22。本轮完成的是**真实导出适配器上的离线 HTTP 契约验证**：手写的独立总账样例经过 `httpx.MockTransport` 返回给生产 `fetch_erpnext_gl`，再经过正常导入预览解析。没有运行 ERPNext 的单据计算或过账引擎，不能把结果称为复杂业务的 ERP 实跑验收。

报告：[erpnext-extended-validation.json](erpnext-extended-validation.json)。5 个场景共 35 项检查通过，7 个拒绝边界通过；相关测试 82 项通过，Ruff 通过。金额全为 Decimal／字符串，没有新增依赖、模型调用或原工作账簿写入。

## 本轮修复

旧导出器在请求中写了 `is_cancelled = 0`，但没有要求响应返回取消标志，也没有逐行核验。异常后端若忽略过滤条件，已取消分录可能进入有效总账导出。

现在显式请求 `is_cancelled`，仅接收整数 `0`；取消值、缺失值、字符串 `"0"` 和布尔值均拒绝。旧正向 mock 增补真实响应字段，既有金额和预期结果未改变。新增负例直接模拟后端无视过滤条件，确保不能靠请求参数假定响应安全。

依据：ERPNext 的 [Immutable Ledger](https://docs.frappe.io/erpnext/immutable-ledger-in-erpnext) 说明取消单据保留原分录并追加反向分录，普通余额排除已取消记录。本项目只导出有效总账；完整取消审计链仍应查看 ERP 原件。

## 手写参考与适用范围

|场景|独立预期|验证边界|
|---|---|---|
|复合费用、扣缴与 40% 部分退货|`100 + 10 + 5.50 - 2 = 113.50`；退货冲回 `40 + 4 + 2.20 - 0.80 = 45.40`|验证已有 CNY 分录的精确保留；测试费用率不代表税法或抵扣资格|
|应收结算汇兑损失|USD 100 以 7.20 确认 720；以 7.10 收取 710；损失 10|不从外币金额自行换算，读取源系统本位币借贷金额|
|应付结算汇兑收益|USD 100 应付 720；支付 710；收益 10|验证本位币分录、科目映射与来源保持|
|期末未实现重估|USD 100 应收从 700 调整到 720；单独调整 20|不伪造银行收款或发票事件|
|取消原单及反冲|取消原分录与反冲均不进入有效导出；有效凭证保留 25|后端正确过滤时核验有效集；后端漏过滤时拒绝整次导出|

汇兑场景以 ERPNext [结算汇兑差额](https://docs.frappe.io/erpnext/manage-foreign-exchange-difference) 和 [汇率重估](https://docs.frappe.io/erpnext/exchange-rate-revaluation) 的机制为参考，数值预期由独立手算给定。重估改变本位币账面值，不代表发生收付款；这里也不认证具体会计政策适用性。

每个场景检查科目累计借贷、总额、逐凭证平衡、原始行标识、显式公司／币种／字符串金额、导入行数，以及不从 GL 推断订单或发票业务事实。负例另覆盖 USD 本位币公司仍须拒绝。

## 复现

```powershell
.venv/Scripts/python.exe scripts/validate_erpnext_extended.py --output runtime/erp-extended-new.json
.venv/Scripts/python.exe -m pytest backend/tests/test_erpnext_extended_validation.py backend/tests/test_connectors_knowledge.py backend/tests/test_erpnext_returns_validation.py backend/tests/test_erpnext_semantics_validation.py -q
```

报告路径独占创建，已有文件不覆盖。没有 ERP 登录、网络请求或密钥读取。场景数据、预期及报告均为合成资料，可以随源码复现。

## 未完成范围

本机 Docker 未运行且可用内存不足，本轮没有启动 ERP 全栈。复杂税费的单据级舍入、真实退款支付、取消后的重开链、汇率配置和 ERPNext 实际生成的分录仍须在隔离 ERP 环境进行差分。已有 10-13 含税采购／部分退货的实跑证据保留原版本范围，不能与本轮 mock 检查合并成新版本全场景通过。

本位币仍限定 CNY；分页读取仍要求源端暂停记账，不提供原子 ERP 快照。真实企业账套和会计专业复核未完成。
