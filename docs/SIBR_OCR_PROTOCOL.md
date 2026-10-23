# SIBR 外部中文票据 OCR：运行前协议

2026-10-23。在任何 SIBR 测试图片进入项目 OCR 前固定。

来源：Yang et al., CVPR 2023，《Modeling Entities as Semantic Points for Visual Information Extraction in the Wild》。[作者数据页](https://www.modelscope.cn/datasets/damo/SIBR)，官方数据卡标注 Apache License 2.0；原始文件版本、大小、SHA-256 见 external-sibr-source-pin.json。SCID 官方分享接口当日返回 File not found，不能据此伪造已取得数据，改为另行核验的 SIBR。

使用官方 test.txt 全部 400 张，保持原顺序，不按结果或标签筛选；600 张训练集只用于检查一条标注的格式，不用于训练。运行现有冻结 PP-OCRv5 mobile CPU 模型，不修改阈值、模型、图像或测试答案。模型来源与哈希沿用 ocr-model-sources.json。图片、标注和完整 OCR 输出留本地，不写入账套，不调用付费模型。

主指标：官方非空且非 ### 标注段中，NFKC 和去空白后文字完全一致，且预测框与标注框的轴对齐包围框 IoU ≥0.5 的比例。一对一最大匹配，重复数字不能重复使用同一预测框。所有错误及无输出图片的标注仍计入分母。该指标为严格文字段恢复率，不称为官方实体抽取 F1。

次指标：忽略位置的一对一整段文字精确恢复率；分别列 header/question/answer/other 及原文件名前缀分组。不是跨行合并、键值对关系或金额归属准确率。不得用子组代替全量指标，不能按一个命中子串宣称整字段正确。

全部 400 张预测保存并关闭后，评测器才能打开真值。运行期拦截网络连接及 truth 目录读取；保存输入、输出、源码与协议哈希。中断保留 running 或 predicted_not_scored，不冒充完成。失败不原地补跑；任何后续调整均须新协议和报告，旧失败保留。

本次没有预设可用于“产品已达标”的 OCR 数值门槛，因此按测得数值报告能力与失败，不事后设线。其数据作者独立于本项目，但公开数据可能进入 OCR 模型预训练，不能声称无污染、独立企业验证或原因调查 AI 增益。
