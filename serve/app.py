from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from eval.run_eval import run_eval
from pipeline.ingest import run_ingest
from serve.query_engine import query
from pipeline.store import LocalVectorStore


class QueryRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    days: Optional[int] = Field(default=None, ge=1, le=365)
    enhance: bool = False


app = FastAPI(title="Mining RAG Pipeline MVP")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "status": "ok", "warnings": [], "source_mode": "service", "data_quality": {"grade": "service"}, "elapsed_ms": 0}


@app.get("/", response_class=HTMLResponse)
def console() -> str:
    return CONSOLE_HTML


@app.get("/stats")
def stats() -> dict:
    store = LocalVectorStore("data/runtime")
    chunks = store.load_chunks()
    if not chunks:
        ingest_summary = run_ingest(out="data/runtime", per_source=20, fixture=False)
        chunks = store.load_chunks()
    else:
        ingest_summary = None
    by_type: dict[str, int] = {}
    source_modes: dict[str, int] = {}
    usable_by_type: dict[str, int] = {}
    limited_by_type: dict[str, int] = {}
    doc_meta: dict[str, dict] = {}
    documents = set()
    for chunk in chunks:
        documents.add(chunk.document_id)
        doc_meta.setdefault(chunk.document_id, chunk.metadata)
        source_type = chunk.metadata.get("source_type", "unknown")
        mode = chunk.metadata.get("source_mode", "unknown")
        by_type[source_type] = by_type.get(source_type, 0) + 1
        source_modes[mode] = source_modes.get(mode, 0) + 1
    for meta in doc_meta.values():
        source_type = meta.get("source_type", "unknown")
        mode = meta.get("source_mode", "unknown")
        evidence_kind = meta.get("evidence_kind", "")
        if mode != "source_limited" and evidence_kind not in {"source_status", "source_discovery"}:
            usable_by_type[source_type] = usable_by_type.get(source_type, 0) + 1
        else:
            limited_by_type[source_type] = limited_by_type.get(source_type, 0) + 1
    coverage_audit = ingest_summary.get("coverage_audit") if ingest_summary else _coverage_from_chunks(usable_by_type, limited_by_type)
    return {
        "status": "ok",
        "documents": len(documents),
        "chunks": len(chunks),
        "by_source_type": by_type,
        "usable_evidence_by_source_type": usable_by_type,
        "source_limited_by_source_type": limited_by_type,
        "coverage_audit": coverage_audit,
        "source_modes": source_modes,
        "warnings": [],
        "source_mode": ",".join(sorted(source_modes)) if source_modes else "none",
        "data_quality": {"grade": "usable" if _all_coverage_targets_met(coverage_audit) else "limited", "documents": len(documents), "chunks": len(chunks)},
        "elapsed_ms": 0,
    }


def _all_coverage_targets_met(coverage_audit: dict) -> bool:
    return all(coverage_audit.get(source_type, {}).get("meets_target") for source_type in ("news", "policy", "price", "total"))


def _coverage_from_chunks(usable_by_type: dict[str, int], limited_by_type: dict[str, int]) -> dict:
    target = 200
    rows = {}
    for source_type in ("news", "policy", "price"):
        usable = usable_by_type.get(source_type, 0)
        rows[source_type] = {
            "target": target,
            "usable_evidence_count": usable,
            "source_limited_count": limited_by_type.get(source_type, 0),
            "gap": max(0, target - usable),
            "meets_target": usable >= target,
        }
    total = sum(usable_by_type.values())
    rows["total"] = {
        "target": target * 3,
        "usable_evidence_count": total,
        "source_limited_count": sum(limited_by_type.values()),
        "gap": max(0, target * 3 - total),
        "meets_target": total >= target * 3,
    }
    return rows


@app.post("/ingest")
def ingest_endpoint() -> dict:
    return run_ingest(out="data/runtime", per_source=20, fixture=False)


@app.get("/eval")
def eval_endpoint() -> dict:
    return run_eval(index_dir="data/runtime", gt_path="eval/generalization_50.json")


@app.post("/query")
@app.get("/query")
def query_endpoint(payload: QueryRequest | None = None, question: str | None = None, top_k: int = 5, days: int | None = None, enhance: bool = False) -> dict:
    if payload is not None:
        return query(payload.question, payload.top_k, payload.days, enhance=payload.enhance)
    if not question:
        return {"status": "error", "error": "question is required", "warnings": ["missing_question"], "source_mode": "none", "data_quality": {"grade": "invalid"}, "elapsed_ms": 0}
    return query(question, top_k, days, enhance=enhance)


CONSOLE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mining RAG Console</title>
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --ink:#1f2937; --muted:#64748b; --line:#d9dee7; --accent:#0f766e; --warn:#b45309; --ok:#047857; --bad:#b91c1c; --soft:#eef2f7; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background:var(--bg); color:var(--ink); }
    header { padding:24px 32px 16px; border-bottom:1px solid var(--line); background:#fff; }
    h1 { margin:0 0 6px; font-size:24px; letter-spacing:0; }
    h2 { margin:0; font-size:18px; letter-spacing:0; }
    .sub { color:var(--muted); font-size:14px; }
    main { padding:24px 32px 36px; display:grid; gap:18px; max-width:1180px; margin:0 auto; }
    .grid { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }
    .metric { font-size:28px; font-weight:700; margin-top:8px; }
    label { display:block; font-weight:650; margin-bottom:8px; }
    textarea { width:100%; min-height:84px; resize:vertical; border:1px solid var(--line); border-radius:6px; padding:10px; font:inherit; }
    input { border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; width:110px; }
    button { border:0; border-radius:6px; background:var(--accent); color:#fff; padding:9px 13px; font-weight:700; cursor:pointer; }
    button.secondary { background:#334155; }
    button:disabled { opacity:.6; cursor:not-allowed; }
    .row { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
    details { border:1px solid var(--line); border-radius:8px; background:#fff; }
    details + details { margin-top:12px; }
    summary { cursor:pointer; padding:12px 14px; font-weight:750; color:#0f766e; }
    details[open] summary { border-bottom:1px solid var(--line); }
    .details-body { padding:14px; }
    pre { white-space:pre-wrap; overflow:auto; margin:0; background:#0f172a; color:#e5e7eb; padding:14px; border-radius:8px; font-size:13px; line-height:1.45; }
    .hits { display:grid; gap:10px; margin-top:12px; }
    .hit { border:1px solid var(--line); border-radius:8px; padding:12px; background:#fbfcfd; }
    .hit b { color:#0f766e; display:block; margin-bottom:8px; font-size:16px; }
    .hit .excerpt { color:#1f2937; line-height:1.55; margin:8px 0; }
    .hit .summary { color:#334155; margin:8px 0; }
    .muted { color:var(--muted); font-size:13px; }
    .badge { display:inline-flex; align-items:center; justify-content:center; min-width:54px; padding:3px 7px; border-radius:999px; font-size:12px; font-weight:750; }
    .pass { color:var(--ok); background:#dcfce7; }
    .fail { color:var(--bad); background:#fee2e2; }
    .bar { height:8px; width:100%; max-width:180px; background:var(--soft); border-radius:999px; overflow:hidden; }
    .fill { height:100%; background:var(--accent); border-radius:999px; }
    @media (max-width: 760px) { header, main { padding-left:16px; padding-right:16px; } .grid { grid-template-columns:1fr 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>三源聚合 RAG 控制台</h1>
    <div class="sub">采集、索引、自然语言查询、50 条泛化自动评测</div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><div class="muted">文档数</div><div id="docs" class="metric">-</div></div>
      <div class="panel"><div class="muted">切片数</div><div id="chunks" class="metric">-</div></div>
      <div class="panel"><div class="muted">数据状态</div><div id="quality" class="metric">-</div></div>
      <div class="panel"><div class="muted">模型</div><div id="model" class="metric">-</div></div>
    </section>
    <section class="panel">
      <div class="row" style="justify-content:space-between">
        <div>
          <label for="question">中文问题</label>
          <div class="muted">示例：最近澳洲矿石出口有哪些政策改动?</div>
        </div>
      </div>
      <textarea id="question">最近澳洲矿石出口有哪些政策改动?</textarea>
      <div class="row" style="margin-top:10px">
        <label style="margin:0">Top K <input id="topk" type="number" min="1" max="20" value="5"></label>
        <button id="queryBtn" onclick="ask()">查询</button>
      </div>
    </section>
    <section class="panel">
      <details open>
        <summary>答案</summary>
        <div class="details-body"><pre id="answer">等待查询...</pre></div>
      </details>
      <details>
        <summary>答案来源</summary>
        <div class="details-body"><div id="hits" class="hits">等待查询...</div></div>
      </details>
      <details>
        <summary>后台 JSON 输出</summary>
        <div class="details-body">
          <div class="muted" style="margin-bottom:8px">Raw Output 是后端 API 返回的完整 JSON，用于调试、审计和复现；业务阅读时通常不需要展开。</div>
          <pre id="raw">等待操作...</pre>
        </div>
      </details>
    </section>
  </main>
  <script>
    async function json(url, options={}) {
      const res = await fetch(url, options);
      return await res.json();
    }
    async function refreshStats() {
      const data = await json('/stats');
      docs.textContent = data.documents;
      chunks.textContent = data.chunks;
      quality.textContent = data.data_quality && data.data_quality.grade ? data.data_quality.grade : '-';
    }
    function setBusy(button, busy, text) {
      button.disabled = busy;
      if (busy) {
        button.dataset.label = button.textContent;
        button.textContent = text;
      } else if (button.dataset.label) {
        button.textContent = button.dataset.label;
      }
    }
    async function ask() {
      setBusy(queryBtn, true, '查询中...');
      const payload = {question: question.value, top_k: Number(topk.value)};
      try {
        const data = await json('/query', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload)});
        const statusText = data.status === 'ok' ? '可回答' : data.status === 'limited' ? '证据有限' : '证据不足';
        model.textContent = data.model_name || '-';
        answer.textContent = `状态：${statusText}\n阶段：${data.answer_stage || 'fast_answer'}\n\n${data.answer}`;
        hits.innerHTML = data.citations && data.citations.length ? data.citations.map((c) => {
          return `<div class="hit"><b>${c.id} - ${escapeHtml(c.title)}</b><div class="muted">证据类型：${escapeHtml(c.directness || 'unknown')}；选中原因：${escapeHtml(c.selection_reason || '')}</div><div class="excerpt">命中段：${escapeHtml(c.matched_excerpt_en)}</div><div class="summary">概括：${escapeHtml(c.summary_zh)}</div><div class="muted">链接：${escapeHtml(c.url)}</div></div>`;
        }).join('') : '<div class="muted">没有达到相关性门槛的来源。请补充对应数据源或扩大范围。</div>';
        raw.textContent = JSON.stringify(data, null, 2);
        if (data.citations && data.citations.length) {
          enhanceAnswer(payload, statusText, data);
        }
      } finally {
        setBusy(queryBtn, false);
      }
    }
    async function enhanceAnswer(payload, statusText, fastData) {
      const enhancedPayload = {...payload, enhance:true};
      try {
        const data = await json('/query', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(enhancedPayload)});
        model.textContent = data.model_name || '-';
        if (data.model_status === 'completed') {
          answer.textContent = `状态：${statusText}\n阶段：model_enhanced\n\n${data.answer}`;
          raw.textContent = JSON.stringify(data, null, 2);
        } else {
          answer.textContent = `状态：${statusText}\n阶段：${fastData.answer_stage || 'fast_answer'}\n模型增强：${data.model_status || 'failed'} ${data.retrieval_trace && data.retrieval_trace.model_error_type ? '(' + data.retrieval_trace.model_error_type + ')' : ''}\n\n${fastData.answer}`;
          raw.textContent = JSON.stringify(data, null, 2);
        }
      } catch (err) {
        answer.textContent = `状态：${statusText}\n阶段：${fastData.answer_stage || 'fast_answer'}\n模型增强：failed\n\n${fastData.answer}`;
      }
    }
    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    refreshStats().then(ask);
  </script>
</body>
</html>
"""
