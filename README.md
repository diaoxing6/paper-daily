# Cell Paper Radar

面向单细胞扰动、虚拟细胞、Cell Foundation Model、多组学扰动、药物扰动机制、GRN / regulon 与机制推理的每日论文雷达。

项目每天自动从 arXiv、bioRxiv、PubMed 和 Semantic Scholar 获取新论文，做主题匹配与相关性排序，可选调用 DeepSeek 或 OpenAI 生成中文解读，最后发布为 GitHub Pages 静态网页。没有 API Key 时也能正常运行，会自动使用中文模板与英文摘要摘录作为降级结果。

## 每日流程

1. GitHub Actions 每天 08:17（Asia/Singapore）触发；避开整点的 Actions 高峰。
2. 获取最近 3 天的论文，短暂的源站故障不会中断其他来源。
3. 按标题、摘要、研究主题、新鲜度和引用信息综合排序，并按 DOI / 标题去重。
4. 默认使用 `DEEPSEEK_API_KEY` 调用 `deepseek-v4-flash`，生成中文「核心贡献 / 方法 / 相关性 / 注意点」；没有 Key 时使用本地降级摘要。
5. 更新 `docs/index.html`、`docs/data/latest.json` 和每日归档，并部署 GitHub Pages。

## 本地运行

```bash
python -m pip install -r requirements.txt
python run_pipeline.py
```

生成的页面位于 `docs/index.html`。只验证排版、不访问论文源时，可以运行：

```bash
python run_pipeline.py --fixture tests/fixtures/sample_papers.json
```

## 配置

研究主题、关键词、来源开关、回溯天数和展示数量都在 `config.yaml` 中。工作流使用 IANA 时区 `Asia/Singapore`，当前 cron 为每天 08:17。

### 可选 Secrets / Variables

- `DEEPSEEK_API_KEY`：默认摘要服务的 Key。添加后启用 `deepseek-v4-flash` 中文摘要；不要写入仓库文件。
- `OPENAI_API_KEY`：切换 `SUMMARY_PROVIDER=openai` 时使用的可选 Key。
- `SEMANTIC_SCHOLAR_API_KEY`：可选。可提高 Semantic Scholar 的请求额度与稳定性。
- `SUMMARY_PROVIDER`：可选的 GitHub Actions Variable，可设为 `deepseek` 或 `openai`。
- `SUMMARY_MODEL`：可选的 GitHub Actions Variable，用于覆盖 `config.yaml` 中的默认模型。

在仓库页面进入 **Settings → Secrets and variables → Actions** 添加它们。默认服务为 DeepSeek，默认摘要模型为 `deepseek-v4-flash`，使用 OpenAI 兼容的 Chat Completions API。

## 手动更新

首次使用时，在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。这是 GitHub Pages 对自定义工作流的一次性启用步骤。

进入仓库的 **Actions → Daily paper radar → Run workflow**。首次部署成功后，页面地址通常是：

`https://<GitHub 用户名>.github.io/<仓库名>/`

## 数据与容错

- 单一论文源异常时，其余来源继续运行，错误会记录在 `docs/data/latest.json` 的 `fetch_report` 中。
- 所有来源都暂时不可用时，网页保留上一次成功内容，不会被空页面覆盖。
- LLM 摘要失败时只降级对应论文，不影响整次发布。
- `docs/data/archive/` 保留每日结果，便于后续增加趋势统计或邮件推送。
