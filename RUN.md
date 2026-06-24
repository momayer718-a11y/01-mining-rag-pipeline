# RUN

## 本地 5 分钟验证

```bash
cd /Users/Zhuanz/Desktop/面试题目MVP/01-mining-rag-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make demo
```

## Docker 验证

```bash
cd /Users/Zhuanz/Desktop/面试题目MVP/01-mining-rag-pipeline
docker compose up --build
```

Docker 启动后访问 `http://localhost:8001` 查看可视化控制台。

另开终端：

```bash
curl -s http://localhost:8001/query \
  -H 'content-type: application/json' \
  -d '{"question":"近 7 天澳洲锂出口价格有何变化?","top_k":5,"days":7}'
```

返回结果会包含中文 `answer`、编号 `citations` 和折叠后台 JSON。答案中的 `[1]`、`[2]` 对应 `citations` 顺序。

## 可选 APIMart / Gemini

```bash
cp .env.example .env
export APIMART_API_KEY=your_key
export APIMART_BASE_URL=https://api.apimart.ai/v1
export APIMART_MODEL=gemini-3.5-flash
```

无 key 时自动使用本地 fallback；不要把真实 key 写入项目文件或压缩包。

## 说明

- 默认 demo 用 `--fixture` 强制稳定跑通。
- 去掉 `--fixture` 后会先尝试真实公开源；数量不足时仍会补 fixture。
- 不绕过登录墙、付费墙或频控。
- 常见矿种/地区/问题类型会尽量给出证据化回答；缺源时返回 `limited` 或 `abstain`。
- `make qa` 会生成 `QA_REPORT.md` 和 `qa/reports/*.json`。
- `make package` 会生成 `/Users/Zhuanz/Desktop/01-mining-rag-pipeline-tool.zip`。
