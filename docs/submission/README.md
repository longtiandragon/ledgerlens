# 比赛提交材料

本目录保存“开源基础软件与解决方案”赛道提交材料的源文件。要求与截止前计划见 [交接文档第 0 节](../HANDOFF.md)。

|材料|源文件|生成方式|
|---|---|---|
|作品介绍 PDF|`intro.template.html`、`meta.json`|`python scripts/build_submission_pdf.py --output .local/submission/账证智核-作品介绍.pdf`|
|截图|`docs/images/*.png`|`python scripts/capture_demo.py --output .local/demo/shots`，人工挑选后复制到 `docs/images/`|
|演示录像|`frontend/tests/demo-capture.spec.ts`|`python scripts/capture_demo.py --output .local/demo/video --video`，再配旁白与字幕|

规则：

- 演示视频旁白为 Windows 离线合成语音（Microsoft Huihui），按维护者决定用于正式版；投稿说明与视频简介中须注明“旁白为合成语音”。

- 模板里的数字全部是占位符，构建时从已提交的验证摘要读取；任何一个值缺失，构建直接失败，不会印出猜测的数字。
- 截图与录像只在隔离环境中生成：临时数据目录、无模型密钥、标注“合成”的演示账套。
- 对外表述按交接文档 §0.4：不宣称模型或多 Agent 优于确定性方法，不宣称覆盖全部准则或已取得真实企业效果。
