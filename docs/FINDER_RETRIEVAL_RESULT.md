# FinDER 检索增益实测：未获支持

2026-10-24 00:36–00:56（北京时间），按预测前提交 `de342a6` 的[固定协议](FINDER_RETRIEVAL_PROTOCOL.md)完成全部 5703 题。5832 个去重父片段切为 15714 个窗口，五种方法均无执行失败，无付费模型调用。先完成全部预测，再读取相关性标签。

|方法|Recall@6|Hit@1|Hit@6|MRR@6|
|---|---:|---:|---:|---:|
|项目关键词排序|13.78%|6.82%|14.29%|9.31%|
|BM25|28.47%|15.73%|29.41%|20.51%|
|TF-IDF|24.14%|9.50%|24.90%|14.68%|
|现有 BGE-small-zh-v1.5|9.07%|4.07%|9.49%|5.88%|
|BM25＋BGE 固定 RRF|20.55%|9.31%|21.25%|13.36%|

最强确定性比较器是 BM25。BGE 主指标低 19.40 个百分点，调整后的 97.5% 配对区间为 [-20.80, -18.02] 个百分点；RRF 低 7.92 个百分点，区间为 [-9.16, -6.77] 个百分点。均未达到预注册的增益至少 5 个百分点、区间下界大于零、零失败三个条件。

聚类为共享任一相关父片段的题目连通分量：5417 个，最大 47 题。独立图遍历与指标实现复算了全题指标、2000 次重采样和区间，与原报告在 1e-12 内一致。六项专项测试及 Ruff 通过。来源、输入、源码和预测哈希校验通过；此处的核验不是执行身份或无污染证明。

## 结论及边界

本轮没有证明 AI 增益，而且显示不能仅因混合方法比旧关键词高 6.77 个百分点就宣布成功：它仍低于更强的 BM25。原生产默认检索保持不变，没有把实验 RRF 接入产品。

数据来自 Choi 等作者的 [FinDER](https://huggingface.co/datasets/Linq-AI-Research/FinDER)，来源版本、文件哈希和 CC-BY-NC-4.0 许可见[来源清单](external-finder-source-pin.json)。这是非商业学生研究，原始数据不加入 Apache-2.0 源码发布包。

这不是原始全量年报检索：候选池仅为作者标注参考片段；也不是中文制度、中文票据或因果调查。已有中文 BGE 向英文金融检索迁移较差，不能据此断言所有 AI 模型或中文任务无效。不能把换成英文模型后的本批结果当成新的独立封存结果；本批已评分，应作为开发证据，后续需要另行固定方法并使用未用于调参的外部数据。

本轮无新增依赖，复用本地 retrieval-env 与 cord-env。模型已有 MIT 来源与哈希清单。分词阶段出现原片段超过 512 token 的警告，随后固定切窗；编码器显式拒绝超长窗口，本轮全部窗口完成，无静默截断或忽略失败。

## 复现和证据

- [完整公开结果](external-finder-retrieval-result-v1.json)：各方法、区间、源码和输入哈希。
- [核验记录](external-finder-verification-v1.json)。
- 本地预测、向量与原始报告：`.local/external-research/finder-run-v1/`；标签隔离于 `finder-input-v1/labels.json`。
- 已完成记录不可覆盖；重新执行需全新输出目录。原冻结协议和运行源码保持不变。

```powershell
.local/cord-env/Scripts/python.exe scripts/prepare_finder_retrieval.py --source .local/external-research/finder-source-v1/train-00000-of-00001.parquet --output .local/external-research/finder-input-reproduction
.local/retrieval-env/Scripts/python.exe benchmarks/finder_retrieval.py --inputs .local/external-research/finder-input-reproduction --models .local/bge-small-zh-v1.5 --output .local/external-research/finder-run-reproduction
.venv/Scripts/python.exe scripts/verify_finder_retrieval.py --inputs .local/external-research/finder-input-reproduction --run .local/external-research/finder-run-reproduction
```

核验脚本独立重算描述指标，原冻结分析器可重算区间；本轮另用独立图遍历实现复核区间。Windows 上模型依赖版本应与 `docs/retrieval-model-v1.json`、`backend/app/retrieval_vectors.py` 及原运行 `environment.json` 一致。
