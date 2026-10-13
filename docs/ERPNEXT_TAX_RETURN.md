# ERPNext 含税采购与部分退货差分

更新：2026-10-13。本地 `frappe/erpnext:v16.50.0` 完成新增合成场景，两次各24项检查通过，重复执行未新增单据或过账。[公开摘要](erpnext-tax-return-validation.json)由保存的原始单据、库存、总账导出及独立试算平衡表重新计算，不只读取通过标记。

## 固定场景与独立预期

新公司 `LedgerLens Tax Return Synthetic`，本位币CNY；独立商品与供应商。两张采购发票均直接更新库存，退货单显式关联原发票及原商品子行。没有付款、折扣、总额取整或其他库存流转。**10%仅为合成测试参数，不代表法定税率、抵扣资格或中国企业会计处理意见**；`TEST-TAX`只是明确映射的测试科目。

|步骤|数量与不含税金额|测试税额|含税总额|
|---|---|---|---|
|2026-09-21采购|10件 × 12.34 = 123.40|12.34|135.74|
|2026-09-22部分退货|-3件 × 12.34 = -37.02|-3.70|-40.72|
|剩余|7件，库存价值86.38|净测试税额8.64|应付净额95.02|

退货税额按该ERP配置落至分。手写预期在运行前固定；没有用导出函数生成标准答案，也没有根据实跑结果改预期。

|显式映射科目|累计借方CNY|累计贷方CNY|
|---|---:|---:|
|1405库存|123.40|37.02|
|TEST-TAX测试税额|12.34|3.70|
|2202应付|40.72|135.74|

两张凭证各3行且各自平衡，总计6行；两次库存变动分别为+10件/+123.40、-3件/-37.02。原件日期、数量、单价、税额、含税总额、退货父单及子行关系均与预期一致。正常导入解析保留六行金额字符串，导出仍仅为journal，没有从总账推断采购或发票业务事实。

## 实跑与重放证据

- 首次：`.local/reports/erpnext-tax-return-v1.json`，13.58秒，24/24检查通过。
- 重放：`.local/reports/erpnext-tax-return-v2-replay.json`，5.24秒，24/24检查通过。
- 两次完整原单据、导出记录、库存行和试算平衡数据完全相同；固定标记查到已提交单据便复用，遇到草稿、取消或多条匹配则拒绝替换。
- 原始单据号为 `ACC-PINV-2026-00003`、`ACC-PINV-2026-00004`。报告保存公司范围和每条真实来源，未保存API密钥。
- 实跑镜像标识：`sha256:ac5b4591e80b52d2e22f6f5cad5d5e7e300f9764dbfeb3f92d325e86baede08d`。

本轮新增23项回归，覆盖税额篡改、退货符号、库存、跨公司、父子行关系、日期、币种、浮点金额、重复来源、多余总账、试算平衡、虚构业务字段、重放变化、源码变化、付费标记、失败状态及草稿/取消/重复匹配。独立测试夹具不由验收器生成。具体测试时长及检查记录见 [STATUS](STATUS.md)。

## 复现

需要现有本地8090合成ERP环境；脚本固定此地址，布置只写上述合成公司及明确命名的测试主数据，应用适配器只读。脚本使用现有本地演示管理员凭据工具，凭据不输出、不入报告。

```powershell
.venv/Scripts/python.exe scripts/validate_erpnext_returns.py --output .local/reports/erpnext-tax-return-new.json
.venv/Scripts/python.exe scripts/validate_erpnext_returns.py --output .local/reports/erpnext-tax-return-replay-new.json
.venv/Scripts/python.exe scripts/summarize_erpnext_returns.py --first .local/reports/erpnext-tax-return-new.json --replay .local/reports/erpnext-tax-return-replay-new.json --output .local/reports/erpnext-tax-return-summary-new.json
.venv/Scripts/python.exe -m pytest backend/tests/test_erpnext_returns_validation.py backend/tests/test_erpnext_semantics_validation.py backend/tests/test_connectors_knowledge.py -q
```

输出拒绝覆盖；失败会留下报告，不撤销或改写已提交单据。没有修改生产适配器、规则或依赖。分页读取仍非原子快照，验收期间源环境无并发记账。

这一场景不能证明退款、取消链、全额退货、估价税费、价内税、多税率/复杂舍入、批次/序列号、到岸成本或汇兑损益；企业真实数据及专业复核也未完成。原两组21项结果保留，不能与本轮24项合并宣称覆盖所有ERP业务。
