# ERPNext 金额、公司与采购单据链差分

更新：2026-10-13。已在本地独立`frappe/erpnext:v16.50.0`完成两组真实合成单据联调：外币账户／多公司／库存21项，采购订单／部分收货／发票／部分付款21项。采购链重复运行后，4张单据编号、6行总账和库存记录均相同。这是固定合成场景的语义验证，不是企业真实账套或专业复核验收。

[结果摘要](erpnext-semantics-validation.json)记录报告哈希。摘要程序重新核对导出金额、ERP试算平衡表、原始单据子行关联与库存余额，不只读取“通过”标记。应用总账适配器没有因测试而改变。

## 外币账户、公司和库存

三个合成公司为`LedgerLens Semantic A Synthetic`、`LedgerLens Semantic B Synthetic`、`LedgerLens Semantic USD Synthetic`。前两家本位币CNY，第三家USD用于验证不支持范围的显式拒绝。

|观察|手写预期及实测|
|---|---|
|A公司USD银行账户付款|账户贷方USD123.45，固定汇率7.20；ERP公司本位币贷方CNY888.84|
|适配器导出|CNY888.84字符串，没有错误导出USD123.45，也没有自行推断汇率|
|B公司独立费用与付款|借费用CNY321.09、贷现金CNY321.09；A、B凭证集合不重叠|
|USD本位币公司|现有适配器明确拒绝，未把它换算后假装支持|
|A公司入库／出库|入库10件×CNY12.34＝123.40；出库3件＝37.02；余7件、CNY86.38|
|库存来源|Stock Ledger Entry的voucher_no和voucher_detail_no分别回到真实Stock Entry及子行|
|导入预览|正常parse_file接受两个CNY导出，全部金额与手写预期、ERP试算平衡表一致|

这里只证明外币账户存在时的本位币导出语义，**没有实现任意本位币账套、自动折算或汇兑损益计算**。

## 采购链

成功场景公司为`LedgerLens Procurement V2 Synthetic`，独立合成商品和供应商；无税、无折扣、无总额取整。

|日期与步骤|原始单据和手写预期|
|---|---|
|09-15采购订单|20件×12.34＝CNY246.80；订单不生成总账|
|09-16部分收货|12件、CNY148.08；PR子行关联准确的PO和PO子行|
|09-17采购发票|12件、CNY148.08；PI子行同时关联PO、PR和各自子行；update_stock=0，不重复入库|
|09-18部分付款|CNY100.00只分配给该发票，未分配额和差额均0|
|剩余状态|已收／已开票60%，未收8件；发票未付CNY48.08；库存12件、CNY148.08|

以上日期均为2026年。独立总账预期与ERP Trial Balance、应用导出吻合：

|映射科目|借方CNY|贷方CNY|
|---|---:|---:|
|1405库存|148.08|0.00|
|2202-UNBILLED暂估／未开票收货|148.08|148.08|
|2202应付|100.00|148.08|
|1002现金|0.00|100.00|

这里只验证显式映射和金额，不把国外默认科目表当成中国企业正式科目政策。三个过账凭证各自平衡，合计6行；只有收货单产生1行库存变动。应用导出仍全部为journal，不从总账生成采购、收货、发票或付款业务事实；采购链原件仅用于独立差分。

## 保留的失败

- ERPNext不接受字符串汇率，付款子行求和不接受字符串分配额，付款绝对值计算不接受字符串付款金额。失败报告保留。对这些第三方接口必须的数值字段，用Decimal写出精确JSON数值，禁止先转二进制float；应用自己的金额API与导出仍是字符串。
- 采购v3的发票未设置set_posting_time，ERPNext将日期覆盖为当日2026-10-13。9月导出正确排除这张发票，4项对账失败。旧公司`LedgerLens Procurement Synthetic`及已提交单据保留，不撤销／改写，不将跨期金额加入9月预期。
- 成功v4使用新合成公司，显式设置过账日期与时间，保留原定“9月17日、148.08”的预期。增加全部日期原样保留检查。v5复用原单据，记录与金额不变。

本地报告：`.local/reports/erpnext-semantics-v1.json`至`v5-evidence.json`；`erpnext-procurement-v1.json`至`v5-replay.json`。公开摘要对应semantics-v5、procurement-v4及v5。原始单据和响应留在忽略目录，不重新分发ERPNext源码。

## 复现与边界

```powershell
docker compose -f infra/erpnext/compose.yaml up -d
.venv/Scripts/python.exe scripts/validate_erpnext_semantics.py --output .local/reports/erpnext-semantics-new.json
.venv/Scripts/python.exe scripts/validate_erpnext_procurement.py --output .local/reports/erpnext-procurement-new.json
.venv/Scripts/python.exe scripts/validate_erpnext_procurement.py --output .local/reports/erpnext-procurement-replay-new.json
.venv/Scripts/python.exe scripts/summarize_erpnext_semantics.py --semantics .local/reports/erpnext-semantics-new.json --procurement .local/reports/erpnext-procurement-new.json --replay .local/reports/erpnext-procurement-replay-new.json --output .local/reports/erpnext-summary-new.json
.venv/Scripts/python.exe -m pytest backend/tests/test_erpnext_semantics_validation.py backend/tests/test_connectors_knowledge.py -q
```

仅针对仓库配置的本地8090服务，需要先完成已有合成环境初始化。验收会创建上述合成公司／单据并生成本机演示管理员API凭据，报告不保存密钥。导出只读，测试布置写入合成公司，未改写用户原始账簿。重跑按固定标记复用单据，遇到草稿、取消或重复匹配拒绝自动替换。报告和导出拒绝覆盖。源环境没有并发记账；分页读取仍不是原子快照。

本轮44项定向测试通过：19项新增ERP语义／报告篡改检查，25项原适配器／知识检查；Ruff及生成契约检查通过。前轮全量1844项通过，此后新增7项检索与19项ERP测试均有定向记录，本轮未重跑全量或浏览器。没有新依赖或付费模型调用。税费、退货、取消采购链、批次／序列号、到岸成本、汇兑损益、并发过账和真实企业数据仍未验证。
