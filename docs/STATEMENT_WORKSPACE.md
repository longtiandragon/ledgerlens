# 公开年报只读工作空间

年报资料独立于会计账套。首批来源为10家公司、2023—2025年共30份完整年报；当前组件从明确的合并资产负债表抽取资产总计、负债合计、全部权益合计及上期金额。当前全量运行有23份产生观察、7份明确拒绝，全部失败保留。4份固定原件具有独立转录金额回归，其他观察尚未独立复核。23/30是解析覆盖，不能宣传为财务准确率。

工作台新增“年报证据”：选择固定快照，按公司/年份搜索，按观察或拒绝筛选，查看原单位、本期、上期、内部勾稽及拒绝原因。证据抽屉核查原 PDF 后显示页码、单元格、原文行、标题/日期/单位依据，并用 PDF.js 展示原页和本期金额定位框。金额保持字符串，不在前端计算财务值。没有账套也可访问本页。

## 快照与来源

`statement-snapshot-store-v1` 保存原始探针、来源清单及规范化投影，三份 JSON 均绑定文件哈希；第一次观察时间固定。相同输入复用快照，不覆盖旧数据；新的探针字节产生新的快照。数据集作用域由来源清单哈希确定；证据 ID 包含快照、报告和指标身份，不能跨快照回查。旧解析失败不会因为新版本成功而消失。

目录默认为忽略的 `.local/annual-reports/statement-snapshots`，原 PDF 仍在 `.local/annual-reports/raw`。原件不复制进开源仓库，许可为 `unspecified`。来源地址及许可登记由采集清单提供，公开可读不等于允许重新分发。

导入检查包括来源清单绑定、身份/年份、原字节长度与哈希、金额字面量和 Decimal 换算、三项总额的内部算术、标题/期间/人民币单位、列头及单元格/文本行的原 PDF 定位。读取快照时重做输入到投影的一致性校验；读取具体证据及原 PDF 时再检查原字节，返回已校验的同一份字节，避免校验后重新按路径打开。

跨行标签仅支持有界的相邻原单元格布局，保留两段 `label_fragments` 和 `table_rows`；不通过资产减负债补出权益。具体几何及原文核查见 [跨行说明](ANNUAL_SPLIT_LABEL.md)。缺少币种或组合列头仍拒绝。

## 接口

所有接口只读，前缀 `/api/v1/statement-datasets`：

|路径|用途|
|---|---|
|根路径|列出校验通过的快照，最新观察时间优先|
|`/{scope}/snapshots/{snapshot}`|读取固定数据集描述|
|`/{scope}/snapshots/{snapshot}/reports`|搜索、状态筛选、分页；最多100条|
|`/{scope}/snapshots/{snapshot}/evidence/{evidence}`|核验当前快照证据并读取定位|
|`/{scope}/snapshots/{snapshot}/reports/{report}/file`|核查哈希后返回本地原 PDF|

没有上传、财务调整、模型运行、任意文件路径或远程下载参数。身份不匹配返回404，校验失败返回409，非法参数返回422。来源、快照及证据均受配置目录边界保护；JSON和PDF分别有16MB、32MB读取上限。OpenAPI和前端响应类型由同一 Pydantic 模型生成。

## 复现

先按 [年报采集方案](ANNUAL_REPORT_DATA.md)取得本机原件，再运行：

```powershell
.venv/Scripts/python.exe -m benchmarks.annual_statement_probe --all-local --output .local/annual-statement-probe/fresh.json
.venv/Scripts/python.exe scripts/import_statement_snapshot.py --probe .local/annual-statement-probe/fresh.json
.venv/Scripts/python.exe scripts/summarize_statement_probe.py --probe .local/annual-statement-probe/fresh.json
.venv/Scripts/python.exe scripts/generate_contracts.py --check
```

探针出现拒绝返回2，完整结果仍保存在新文件。检查拒绝原因后可以导入保留这些失败的快照，不能把非零退出忽略为全成功。原探针文件拒绝覆盖。工作台无需新增依赖或外部模型，默认设置可直接读取导入目录。

## 能力边界

`semantic_status: unverified_extraction`始终保留。原文定位、文件哈希、内部平衡均不能证明财务处理正确，也不构成防篡改认证。当前页面未创建业务凭证、可执行会计规则、异常根因标签或 Agent 工具。公开合并财报不能替代完整企业账套；尚未支持 OCR、组合列头、多币种、完整报表勾稽或专业复核。打开原件用于本机核对，不表示对整份原件有重新分发授权。

## 本轮验证

完整后端1087 passed、2 skipped（69.73秒）；两项跳过是本机Windows普通文件符号链接创建权限缺失，未宣称已验证。年报解析76项包含4份本机原件且无skip；快照/API专项125项通过。独立反例发现并修复“两个年度三项都平衡但本期取到上期”问题；本期/前期金额必须绑定明确列头，续页列头和金额受合并节边界限制，不能借同页母公司表。

前端类型及生产构建通过（22.02秒）；浏览器22项集成验收全过（1.3分钟），包含7项年报检查及实际永辉2023原件第120页的双年度金额和单元格框，两个真实数据开关均显式打开，0 skipped。桌面、390px、手机抽屉均已截图核查。相同新版探针再次导入返回`existing_verified`，未覆盖原快照。原始PDF全量字节及69项金额定位在导入重核中通过，这仍不是财务真值测试。
