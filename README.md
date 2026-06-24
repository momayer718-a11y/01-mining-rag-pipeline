# 01 - Mining RAG Pipeline MVP

对应题 #1：矿业新闻 + 关键矿产政策 + 价格三源聚合管线。

这个 MVP 的目标是 5 分钟可跑通：

1. 采集真实公开源，失败或数量不足时自动使用 fixture 补齐。
2. 清洗、去重、分块并写入本地检索索引。
3. 提供 FastAPI `/query` 自然语言查询接口，返回中文答案、编号引用和结构化来源。
4. 内置 20 条 ground truth Q&A，输出 `recall@5` 和 faithfulness。

运行：

```bash
make demo
docker compose up --build
```

可视化控制台：

```bash
make serve
# open http://localhost:8001
```

行业级验收：

```bash
make test
make qa
make package
```

API 输出包含稳定字段：`status`、`warnings`、`source_mode`、`elapsed_ms`、`data_quality`，并新增：

- `intent`: 轻量问题解析结果，包含矿种、地区、问题类型、时间范围。
- `answer_points`: 中文关键判断，每条带 `citation_ids`。
- `citations`: 答案来源顺序，包含原文标题、英文命中段、中文概括和链接。

业务答案不会展示“命中关键词 / relevance”等调试语；这些只保留在折叠 Raw JSON。价格问题优先引用价格源，政策问题优先引用政策源。证据不足或未加载地区/矿种时返回 `limited` / `abstain`，不会硬凑答案。

可选模型增强：

```bash
cp .env.example .env
export APIMART_API_KEY=...
export APIMART_MODEL=gemini-3.5-flash
```

有 APIMart key 时使用 Gemini 生成中文答案；无 key 或模型失败时使用确定性 fallback，demo 仍可运行。真实密钥不得写入项目文件或 zip。

API 示例：

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口价格有何变化?","top_k":5,"days":7}' | jq
```
