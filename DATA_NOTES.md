# DATA_NOTES

## Sources

- `news`: MINING.com RSS、S&P Global Metals/Energy RSS。真实源失败或不足 200 条时补 fixture。
- `policy`: 澳洲 DISR Critical Minerals Strategy、中国稀土相关公开页面。HTML 不规整时保留可解析摘要并补 fixture。
- `price`: LME 铜/锌/镍、SHFE 碳酸锂、上海钢联铁矿石。登录墙、付费墙或频控不绕过，默认 fixture 补齐。

## Document Schema

- `id`: `source + canonical_url + content_hash` 的 SHA256 前 24 位。
- `source`: 数据源名称，例如 `mining.com`、`disr`、`lme`。
- `source_type`: `news`、`policy`、`price`。
- `title`: 标题。
- `url`: 原始链接或 fixture 链接。
- `published_at`: ISO 日期。
- `content`: 清洗后的正文。
- `metadata`: 来源模式、commodity、region 等附加字段。

## Dedup Strategy

1. URL 小写、去掉 fragment 和常见 tracking 参数。
2. 正文 normalize 后计算 `content_hash`。
3. 主键为 `source + canonical_url + content_hash`。
4. 同主键只保留第一条。

## Chunk Schema

- `chunk_id`: `document_id:index`。
- `document_id`: 对应文档 ID。
- `text`: chunk 文本。
- `tokens`: 本地检索词项。
- `metadata`: 继承 source、source_type、url、published_at、title。

## Limitations

这个 MVP 用本地词项检索模拟向量检索，避免 API key 和模型下载依赖。生产版本可替换为 Qdrant + embedding model，外部接口不变。

