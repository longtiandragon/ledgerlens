# 贡献指南

欢迎参与。动手前请先读 [AGENTS.md](AGENTS.md)（协作底线）、[PRD](docs/PRD.md) 和 [交付状态](docs/STATUS.md)；决策和合入规则见 [GOVERNANCE.md](GOVERNANCE.md)，参与即表示同意遵守 [行为准则](CODE_OF_CONDUCT.md)。

## 提交什么样的改动

一次只提交一个可检查的业务变化，说明输入、输出、适用条件、限制和测试。

- 会计规则 PR 必须提供明确来源、版本和独立的已知答案；没有专业复核的规则保持 experimental。请先用“会计规则请求”Issue 模板讨论。
- 模型提示词修改需保存版本，并运行相同预算的对照，不删除失败样例。
- 导入或实体修改需验证来源冲突、幂等、期间和快照隔离。
- 不提交 `.env`、真实账套、密钥或许可不明确的原始文件。
- 依赖更新须锁定版本，检查许可和安装体积。CI 的 `licenses` 作业用 `scripts/check_licenses.py` 对照 [许可策略](docs/license-policy.json) 检查全部依赖；元数据含糊的包须先在 `resolved` 中记录核对依据，非宽松许可只能作为写明理由和范围的例外。
- 新增模块先定义 Pydantic/OpenAPI 契约，再实现。模型建议与确定性事实始终分别存储。

## 开发环境

需要 Python 3.12、Node.js ≥ 22.13 和 [uv](https://docs.astral.sh/uv/)。

Windows（PowerShell）：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

Linux／macOS：

```bash
cp -n .env.example .env
uv sync --frozen
(cd frontend && npm ci)
export PYTHONPATH=backend:.
uv run python scripts/prepare_local_database.py
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &
(cd frontend && node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188)
```

也可以用 Docker：`docker compose up --build -d`，入口为 <http://127.0.0.1:8080>，见 [运行手册](docs/RUNBOOK.md)。规则演示不需要模型密钥。

## 测试

```bash
uv run pytest -q                                        # 后端全量
uv run ruff check backend scripts datasets benchmarks
uv run python scripts/generate_contracts.py --check     # 改了 API 后先去掉 --check 重新生成
(cd frontend && npm run build)
```

端到端测试（`cd frontend && npm run test:e2e`）会在被测 API 中新建账套。请让 API 使用临时数据目录（设置 `DATA_DIR` 和 `DATABASE_URL` 指向新目录），不要对日常使用的账库运行。做法可参照 `.github/workflows/ci.yml` 中的 reproduce 作业。

请在 PR 中写明实际运行了哪些命令、结果如何，以及没有运行的部分。

## 分支与 PR

1. 从 `main` 新建主题分支，例如 `fix/import-period`、`rule/lease-term`。
2. 提交信息首行用一句话说明改动，可用中文或英文。
3. 开 PR 时按模板填写，特别是“未验证事项”。必需的 CI 检查全部通过后才会合并。
4. 合并方式为 squash，合并后删除分支。

## AI 辅助的贡献

可以使用 AI 编程助手，但须遵守 GOVERNANCE.md 中的 AI 辅助开发规则：在 PR 模板中勾选 AI 参与，不修改测试预期迁就实现，不删除失败记录，不发起未经批准的付费模型调用。AI 给出的会计判断只作为待复核意见。

## 许可

贡献内容按项目的 Apache-2.0 许可发布（贡献即按同一许可授权），不需要另签贡献者协议。请确认你有权提交这些内容。
