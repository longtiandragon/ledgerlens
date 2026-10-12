# BPI2019业务事件证据适配设计

本说明来自实际读取`.local/bpi2019/subset.jsonl`全部1000个完整trace，以及核对[TU/e官方数据说明](https://research.tue.nl/en/datasets/bpi-challenge-2019/)。BusinessEvent契约、纯转换器、独立快照、只读API和证据页面已实现；分类与来源定位通过独立回归和全量指针核验。专用模型调查、语义异常规则、流程模拟和底稿尚未实现，不代表新增会计准则规则或已验证异常标签。

## 已实现与复现

`backend/app/process_events.py`定义独立契约、严格标志解析、过程观察和有界查询；`process_store.py`保存内容固定的manifest、来源JSONL、快照及质量报告，读取时重验哈希与来源映射。`datasets/bpi2019_adapter.py`核验官方元数据、完整原XES和子集后导入；纯转换函数不会自行宣称来源已核验。重复导入实测返回`existing_verified`并保留首次获知时间。

只读`/api/v1/process-datasets`提供数据集清单及固定作用域的采购项、事件、来源定位接口。工作台“采购流程证据”支持搜索、分页、源字段抽屉和手机布局，不依赖Book，不调用模型。过期请求不覆盖当前选择。独立定位脚本核验1000个trace、15351个event及92755个字段指针；11项API回归与真实浏览器流程通过。来源哈希是内容完整性检查，不单独提供来源真实性或专业准确率证明。

```powershell
.venv\Scripts\python.exe -m datasets.bpi2019_adapter
.venv\Scripts\python.exe scripts/verify_process_locators.py
```

已有步骤1、2及HTTP形式的步骤3通过。步骤4的流程观察规则已实现为`backend/app/process_rules.py`（`bpi2019-process-rules-v1`）：9条规则只检查可观察约束，状态为源策略相容／待核实过程差异／证据不足／范围外，规则目录记录官方说明短引、适用分类与不适用条件；金额匹配、取消冲销、付款冻结等仍未实现。底稿导出（JSON／Markdown）只做规则观察，不含模型解释；步骤5的模型调查与反证仍待实现，运行Agent目前不能调用BPI查询或读取它的全部日志。

```powershell
.venv\Scripts\python.exe scripts/summarize_process_rules.py
```

## 数据范围与观察结果

当前子集按原XES顺序选取首次1000个完整trace，共15351个事件、36种活动、264种不同活动序列。每trace 2–923个事件。完整trace表示读取了该XES trace关闭标签前的全部记录，**不表示业务已结束、资料已齐全或全部交易已被日志覆盖**。82个trace没有`Clear Invoice`，仅据此不能判定漏付款。

字段与计数均来自本地子集。没有公开原始供应商、公司、人员或采购文档标识；实体原值仍留在本地。

|trace分类|GR-Based Inv. Verif.|Goods Receipt|本地trace数|观察边界|
|---|---|---|---|---|
|`3-way match, invoice before GR`|字符串`false`|字符串`true`|760|52条首次收票早于首次收货，692条相反，16条至少缺一类事件。分类说明允许提前收票，不要求每笔一定提前。|
|`3-way match, invoice after GR`|字符串`true`|字符串`true`|211|197条首次收货早于首次收票，14条至少缺一类事件。|
|`Consignment`|字符串`false`|字符串`true`|29|79个事件全部源数值为0；无`Record Invoice Receipt`或`Clear Invoice`。不能默认判缺票、漏付或金额缺失。|
|两方匹配|官方描述为两标志均false|官方描述为两标志均false|0|当前前1000条没有真实两方匹配样本，不能声称该子集覆盖四种流程。|

另有`Item Type`：`Standard`830条、`Service`141条、`Consignment`29条；`Document Type`：`Standard PO`641条、`EC Purchase order`333条、`Framework order`26条。这些是源系统业务分类，不是CAS科目或会计处理结论。

## 官方四类流程与适配约束

TU/e说明区分：基于收货的三方匹配、允许先收票的三方匹配、无需收货的两方匹配，以及采购项级不处理发票的寄售。前两类需考虑采购项、收货与收票的价值关系；允许先收票时付款解除限制与收货相关。两方匹配不要求独立收货事件，允许部分金额逐步消耗采购项。寄售的开票属于另外流程。官方还指出一项可有多批收货、发票和清账，数值匿名变换可能有舍入差异。[TU/e官方说明](https://research.tue.nl/en/datasets/bpi-challenge-2019/)

因此适配器应先按`Item Category`、`Item Type`及两标志识别流程，矛盾或未知值保留为待核实；不能只按一条标志套用“所有订单必须有收货／发票”的默认链。以上是该数据集流程语义，不转成中国会计准则依据。

## 实际字段与金额、时间含义

每条trace都有16个源属性：

|用途|实际字段|建议保存方式|
|---|---|---|
|采购项与采购文档|`concept:name`、`Purchasing Document`、`Item`|trace是采购**行项目**，不是整张订单。保留源值及稳定匿名显示别名，关联依据来自源字段。|
|流程策略|`GR-Based Inv. Verif.`、`Goods Receipt`、`Item Category`、`Item Type`|原字符串保留；`true`／`false`严格转换为布尔或unknown，禁止用非空字符串的真值判断。|
|来源与主体|`Source`、`Company`、`Vendor`、`Name`|按数据集作用域保存匿名标识，不能推断真实企业身份或和本地账套实体合并。|
|采购分类|`Document Type`、`Purch. Doc. Category name`、`Spend area text`、`Spend classification text`、`Sub spend area text`|只作为检索及流程分类属性，不能自动映射资产／费用科目。|

每个事件都有五个字段：`concept:name`、`time:timestamp`、`Cumulative net worth (EUR)`、`User`、`org:resource`。当前子集没有`lifecycle:transition`，也没有独立事件ID、发票编号、银行交易号或凭证号。不能虚构这些字段来建立一对一关系。

`Cumulative net worth (EUR)`原文数值保留为字符串，必要时用Decimal读取。字段名含EUR，但这是匿名事件价值，不能直接当成可记账的EUR金额，更不能填入CNY金额或推算汇率。子集15350个数值保留一位小数、1个无小数，93个值为0，没有负值。0是明确观察值，不能替换为缺失。

这个字段名**不能证明它是跨全部活动的运行累计余额**。实际短trace中，创建采购项、收货、收票和清账可能分别重复同一数值；将所有活动相加会重复计数。应按活动阶段分别观察，取消、价格修改和后续发票另行解释。当前官方页面及采集元数据未给出足够细的字段级累计／冲销定义；在独立例及资料核实前，只输出原始值与条件化阶段差额，不上线默认求和或冲销规则。

全部15351个时间戳为`YYYY-MM-DDTHH:mm:ss.SSSZ`样式，显式UTC；1000条trace内时间不递减，396条有相邻相同时间戳。应保留原文、带时区datetime和源事件顺序，同刻事件不构成唯一因果先后。不要将UTC日期静默转成北京时间或只截取日期后丢弃时分秒。

实际时间范围为2001-02-23至2019-04-30。年份分布：2001年1个、2016年1个、2017年27个、2018年15118个、2019年204个；早于2018年的29个事件属于供应商发票／借项通知活动。不能为了“2018年数据”而静默删除、改日期或判定会计期间错误。源事件时间与2026年的本机导入时间必须分开；本数据没有逐事件原系统获知时间，不能用它证明防未来信息泄漏机制。

## 活动目录及流程变体

|活动组|本子集实际活动与事件数|适配解释|
|---|---|---|
|采购及往来主链|`Create Purchase Order Item`1000；`Vendor creates invoice`2049；`Record Goods Receipt`2531；`Record Invoice Receipt`2471；`Clear Invoice`1860|源系统状态事件。供应商开票与系统收票是不同活动；清账不能自动等同银行付款事实。|
|服务验收|`Record Service Entry Sheet`1585|127个trace出现服务验收，不能自动当成库存实物收货。|
|取消及后续票据|`Cancel Goods Receipt`31；`Cancel Invoice Receipt`102；`Vendor creates debit memo`95；`Cancel Subsequent Invoice`7；`Record Subsequent Invoice`1|保留取消／后续的事件类型；不能仅凭数值、时间或活动重复确认原票据身份与冲销对象。|
|修改、阻断、确认|`Change Delivery Indicator`30；`Remove Payment Block`281；`Change Price`110；`Delete Purchase Order Item`21；`Change Quantity`112；`Change Final Invoice Indicator`5；`Receive Order Confirmation`91；`Reactivate Purchase Order Item`2；`Update Order Confirmation`3；`Block Purchase Order Item`5；`Change Approval for Purchase Order`4；`Release Purchase Order`1；`Set Payment Block`1|流程版本和控制状态，不写回原事件，不映射成借贷分录。|
|SRM流程|`SRM: Created`409；`SRM: Complete`409；`SRM: Awaiting Approval`409；`SRM: Document Completed`409；`SRM: In Transfer to Execution Syst.`470；`SRM: Ordered`409；`SRM: Change was Transmitted`333；`SRM: Transfer Failed (E.Sys.)`20；`SRM: Deleted`76；`SRM: Transaction Completed`3；`SRM: Incomplete`3；`SRM: Held`3|采购系统前置及传输状态；传输失败活动存在不自动证明财务金额错误。|

125个trace有多次收货、120个有多次收票、113个有多次清账。它们是多次事件的真实计数，不是125／120／113个错误。采购项可能被分批履行，不能把“活动重复”“金额相同”当成重复记账。

常见观察序列包括创建→供应商开票→收货→收票→清账（156条），加入解除付款限制（111条），以及创建→收货→供应商开票→收票→清账（83条）。还有创建→供应商开票→收票→收货→解除付款限制→清账（31条），与允许先收票的流程相容。这里只说明顺序与源策略相容，不为整个trace人工打“正常”标签；缺少隐藏真值时仍需保留判断。

## 与现有平台契约的边界

目前`backend/app/schemas.py`的`BookInput`只支持CAS／SCAS及CNY；`LedgerInput`只接受journal、bank、order、receipt、invoice、opening、trial_balance，业务记录必须有amount。当前BPI日志不能通过这些契约导入：默认为CNY、伪造会计制度或用0补齐缺失账务字段都会改变语义。

已增加独立的ProcessDataset及BusinessEvent证据类型，让真实流程证据可查询、可定位，再扩展专用流程检查。与账套连接必须有独立、明确的ERP实体映射；当前匿名BPI数据不创建这种连接。

|建议字段|含义及生成约束|
|---|---|
|`dataset_scope_id`、`source_document_id`|固定数据集及原XES内容哈希，独立于CAS账套。|
|`source_trace_id`、`source_trace_ordinal`|本地保留源case ID及顺序；UI默认显示匿名别名。|
|`source_event_ordinal`、`source_event_id`|每trace内源顺序；以数据集哈希、源trace ID与事件序号确定性生成ID，不冒充源系统原生ID。|
|`activity_raw`、`event_type`|原活动名及版本化的枚举映射；未知活动保留原值与unsupported状态，不静默丢弃。|
|`occurred_at_raw`、`occurred_at`、`observed_at`|原UTC文本、时区datetime、本机导入获知时间；后者由服务端生成。|
|`source_numeric_text`、`source_numeric_semantics`|原数值及“匿名事件价值、字段标签EUR”的说明；不生成账务currency、debit、credit。|
|`trace_attributes`、`event_attributes`|保留源字段及解析版本，单独保存规范化标志和流程分类。|
|`evidence_id`、`locator`|证据绑定数据集快照；定位到JSONL行号、trace及event序号、原字段键。不能虚构PDF页码。|

显式关联仅限“该事件属于该采购行项目”“该行项目属于该采购文档”等源字段关系。不同收票与清账事件间的业务匹配若无原始编号，只能是待复核候选。来源明确关联、规则推导与模型候选分别标记。

## 建议实现顺序与验收

1. **纯转换器与数据契约**：输入完整trace、原文哈希和导入时间，输出BusinessEvent证据及source locator；不调用会计引擎、模型或数据库。金额保持字符串，未知币种和制度不补默认值。
2. **独立数据集导入与快照**：幂等键采用数据集内容哈希＋源trace＋event序号；重复导入不重复，原事件不修改，字段映射修订新增版本。与账套数据分作用域保存。
3. **受控查询工具**：`query_process_events`按固定dataset／snapshot、trace、活动与日期范围查询，限制返回数；`get_process_trace`返回完整关联及证据定位。不得将全部1000trace塞进单个模型上下文，也不提供任意SQL。
4. **流程观察与单独规则包**：先实现活动存在、标志合法性及已配置流程分类。需要金额、取消、付款限制状态的规则先写独立参考例及适用条件。规则使用BPI流程命名空间，依据为源流程政策，不复用CAS规则编号。
5. **调查与导出**：模型只能解释已返回事件，输出“源策略相容／存在待核实过程差异／证据不足／范围外”。缺单据、真实原因、原系统获知时间和真实付款证据分别列出。模拟只模拟流程假设，不生成或调整账簿分录。

验收至少包括：寄售无发票仍不报缺票；两方匹配无需收货；允许先收票分类不要求一定先收票；多次分批事件不报重复记账；供应商开票与系统收票分开；服务验收不自动当库存收货；取消的正数源值不自动作为新增采购；0保留；同刻事件保留源顺序；旧时间戳不删除；未知活动不丢弃；模型引用必须在本数据集快照；跨账套／跨数据集引用拒绝；每条定位可回到JSONL原字段。

两方匹配的首批测试应采用明确标为独立fixture的例子，或在已核验完整原XES中另选真实完整trace并记录选择方法。不能把fixture充当现有1000trace中的实际数据。异常原因与业务成效仍需独立真值及专业验证，本设计不提供这些结论。

## 流程模型解释（步骤5的模型部分，2026-10-12）

`backend/app/process_investigation.py`（`process-investigation-v1`）对单个采购项运行有界单角色调查：只开放`get_process_trace`、`query_process_events`、`read_rule_review`三个只读工具，范围限定在该快照的这一个采购项；不提供SQL、代码或其他采购项。模型输出结论（四类）、解释、反证与缺少的信息；每条引用必须属于该采购项且已由工具或简报返回，`rule_id`必须是`BPI2019_PROCESS`规则，失败只修复一次，预算耗尽或校验失败如实返回。程序规则观察始终是事实依据，模型意见标记为未经核验。

接口`POST .../trace-investigations`（需配置模型密钥、同一时间只运行一个，记录绑定源子集哈希、适配器版本与规则包）、`GET .../trace-investigations`与`GET /api/v1/process-datasets/process-investigations/{run_id}`；运行记录以不可覆盖的JSON保存在`runtime/process-investigations`，服务重启遗留的运行标记为已中断。采购流程页面需两步确认才会调用付费模型，轮询显示结果并可回查引用。新增6项后端回归与1项浏览器回归，均使用模拟模型服务；真实模型运行情况见STATUS。没有独立异常真值，不计算根因准确率。
