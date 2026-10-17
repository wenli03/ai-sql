from __future__ import annotations

import html
import json
import uuid
from datetime import datetime

from .models import AskResponse, Report, ReportBlock


def block_from_answer(answer: AskResponse, row_limit: int = 200) -> ReportBlock:
    return ReportBlock(
        id=f"blk-{uuid.uuid4().hex[:10]}",
        question=answer.question,
        title=answer.title,
        sql=answer.sql,
        explanation=answer.explanation,
        chart=answer.chart,
        columns=answer.columns,
        rows=answer.rows[:row_limit],
        provenance=answer.provenance,
        created_at=datetime.now(),
    )


def _esc(value: str) -> str:
    return html.escape(value or "")


def _provenance_html(provenance) -> str:
    if provenance is None:
        return '<div class="muted">本次结果未生成溯源信息。</div>'
    is_vector = provenance.source_type in {
        "qdrant", "chroma", "weaviate", "milvus", "pinecone", "pgvector",
    }
    physical_label = "向量库请求" if is_vector else "Wren 改写后的物理 SQL"
    status_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    checks = "".join(
        f'<div class="check {check.status}">{status_icon.get(check.status, "•")} '
        f'<b>{_esc(check.label)}</b>：{_esc(check.detail)}</div>'
        for check in provenance.checks
    )
    sources = "".join(
        f"<tr><td>{_esc(ref.model)}</td><td>{_esc(ref.kind)}</td>"
        f"<td>{_esc('.'.join(p for p in [ref.catalog or '', ref.schema_name or '', ref.table] if p))}</td>"
        f"<td>{_esc(ref.location or '')}</td></tr>"
        for ref in provenance.source_tables
    ) or '<tr><td colspan="4" class="muted">无来源模型</td></tr>'
    lineage = "".join(
        f"<tr><td>{_esc(edge.output_column)}</td>"
        f"<td>{_esc('.'.join(p for p in [edge.source_catalog or '', edge.source_schema or '', edge.source_table] if p))}."
        f"{_esc(edge.source_column)}</td><td><code>{_esc(edge.expression or '')}</code></td></tr>"
        for edge in provenance.lineage
    ) or '<tr><td colspan="3" class="muted">未能自动计算字段级血缘，请查看表级来源。</td></tr>'
    metrics = "".join(
        f"<li><b>{_esc(metric.get('label') or metric.get('name'))}</b>："
        f"<code>{_esc(metric.get('expression') or '')}</code>"
        + (f"；过滤：<code>{_esc(metric.get('filters') or '')}</code>" if metric.get("filters") else "")
        + "</li>"
        for metric in provenance.metrics_used
    ) or "<li class=\"muted\">本次问题未匹配到预定义指标。</li>"
    rules = "".join(f"<li>{_esc(rule)}</li>" for rule in provenance.rules_used) or "<li class=\"muted\">无</li>"
    freshness = "".join(
        f"<tr><td>{_esc(item.model)}</td><td>{_esc(item.table)}</td>"
        f"<td>{item.row_count if item.row_count is not None else '-'}</td>"
        f"<td>{_esc(item.max_time_column or '-')}</td>"
        f"<td>{_esc(item.max_time_value or '-')}</td><td>{_esc(item.note or '')}</td></tr>"
        for item in provenance.freshness
    ) or '<tr><td colspan="6" class="muted">无来源表新鲜度信息</td></tr>'
    return f"""
    <details class="prov">
      <summary>数据溯源与可靠性 · 评分 {provenance.reliability_score}/100</summary>
      <div class="prov-grid">
        <div>
          <h4>来源映射</h4>
          <table class="mini"><thead><tr><th>模型</th><th>类型</th><th>物理对象</th><th>位置</th></tr></thead>
          <tbody>{sources}</tbody></table>
        </div>
        <div>
          <h4>可靠性检查</h4>
          <div class="checks">{checks}</div>
        </div>
        <div>
          <h4>指标口径</h4><ul>{metrics}</ul>
          <h4>业务规则</h4><ul>{rules}</ul>
        </div>
        <div>
          <h4>字段级血缘</h4>
          <table class="mini"><thead><tr><th>结果字段</th><th>来源字段</th><th>表达式</th></tr></thead>
          <tbody>{lineage}</tbody></table>
        </div>
        <div>
          <h4>数据新鲜度</h4>
          <table class="mini"><thead><tr><th>模型</th><th>表</th><th>行数</th><th>时间字段</th><th>最大值</th><th>说明</th></tr></thead>
          <tbody>{freshness}</tbody></table>
        </div>
        <div>
          <h4>执行信息</h4>
          <div class="prov-meta">
            <div>数据源：{_esc(provenance.source_type)} · {_esc(provenance.database)}
              {('/' + _esc(provenance.schema_name)) if provenance.schema_name else ''}</div>
            <div>执行时间：{_esc(str(provenance.generated_at))}</div>
            <div>结果行数：{provenance.row_count} · 耗时：{provenance.elapsed_ms} ms · 尝试：{provenance.attempts}</div>
            <div>模型：{_esc(provenance.llm_provider)} · {_esc(provenance.llm_model)}</div>
            <div>本体生成时间：{_esc(str(provenance.ontology_generated_at or '-'))}</div>
          </div>
        </div>
      </div>
      <details class="sql"><summary>{physical_label}</summary><pre>{_esc(provenance.planned_sql or '未提供')}</pre></details>
    </details>
    """


def export_html(report: Report, echarts_js: str) -> str:
    data = report.model_dump(mode="json")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    blocks = []
    for index, block in enumerate(report.blocks):
        chart_block = ""
        if block.chart.type == "kpi":
            option = block.chart.option or {}
            chart_block = (
                f'<div class="kpi"><div class="kpi-label">{_esc(option.get("label", ""))}</div>'
                f'<div class="kpi-value">{_esc(option.get("formatted", ""))}</div></div>'
            )
        elif block.chart.type != "table" and block.chart.option:
            chart_block = f'<div class="chart" id="chart-{index}"></div>'
        rows_html = ""
        if block.rows:
            head = "".join(f"<th>{_esc(c)}</th>" for c in block.columns)
            body = "".join(
                "<tr>" + "".join(f"<td>{_esc(str(row.get(c, '')))}</td>" for c in block.columns) + "</tr>"
                for row in block.rows[:50]
            )
            more = f'<div class="muted">显示前 {min(50, len(block.rows))} 行，共 {len(block.rows)} 行</div>'
            rows_html = f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'
        sql_html = (
            f'<details class="sql"><summary>{"查看查询计划" if block.provenance and block.provenance.source_type in {"qdrant", "chroma", "weaviate", "milvus", "pinecone", "pgvector"} else "查看 SQL"}</summary>'
            f'<pre>{_esc(block.sql)}</pre></details>'
        )
        prov_html = _provenance_html(block.provenance)
        blocks.append(
            f"""
            <section class="block">
              <div class="block-head">
                <div><h2>{_esc(block.title)}</h2><p class="question">{_esc(block.question)}</p></div>
              </div>
              <p class="explain">{_esc(block.explanation)}</p>
              {chart_block}
              {rows_html}
              {sql_html}
              {prov_html}
            </section>
            """
        )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_esc(report.title)}</title>
<style>
  :root {{
    --bg:#F5F7FB; --panel:#FFFFFF; --ink:#111827; --muted:#6B7280;
    --line:#E5E9F2; --brand:#3E6BFF; --brand-soft:#EEF2FF;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:Inter,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .page {{ max-width:1080px; margin:0 auto; padding:48px 28px 80px; }}
  .hero {{ background:linear-gradient(135deg,#10162A,#243B7A 60%,#3E6BFF);
    color:#fff; border-radius:22px; padding:34px 38px; margin-bottom:26px; }}
  .hero h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:-.02em; }}
  .hero p {{ margin:0; color:#C7D2FE; font-size:14px; }}
  .badge {{ display:inline-block; margin-top:16px; padding:6px 12px; border-radius:999px;
    background:rgba(255,255,255,.14); font-size:12px; color:#E0E7FF; }}
  .block {{ background:var(--panel); border:1px solid var(--line); border-radius:18px;
    padding:26px 28px; margin-bottom:20px; box-shadow:0 10px 30px rgba(17,24,39,.04); }}
  .block h2 {{ margin:0 0 4px; font-size:20px; }}
  .question {{ margin:0; color:var(--muted); font-size:13px; }}
  .explain {{ color:#374151; line-height:1.7; font-size:14px; }}
  .chart {{ width:100%; height:340px; margin-top:14px; }}
  .kpi {{ margin-top:18px; background:var(--brand-soft); border-radius:16px; padding:24px 26px; }}
  .kpi-label {{ color:var(--muted); font-size:13px; }}
  .kpi-value {{ color:var(--brand); font-size:38px; font-weight:700; letter-spacing:-.03em; }}
  .table-wrap {{ overflow:auto; margin-top:16px; border:1px solid var(--line); border-radius:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ background:#F8FAFC; text-align:left; padding:10px 12px; color:#475569; white-space:nowrap; }}
  td {{ padding:9px 12px; border-top:1px solid var(--line); white-space:nowrap; }}
  .muted {{ color:var(--muted); font-size:12px; margin-top:8px; }}
  details.sql {{ margin-top:16px; }}
  details.sql summary {{ cursor:pointer; color:var(--brand); font-size:13px; }}
  details.prov {{ margin-top:16px; border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#F8FAFC; }}
  details.prov > summary {{ cursor:pointer; color:#0F172A; font-size:13px; font-weight:600; }}
  .prov-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:16px; }}
  .prov-grid h4 {{ margin:0 0 8px; font-size:13px; }}
  .prov-grid ul {{ margin:0; padding-left:18px; font-size:12px; line-height:1.8; color:#374151; }}
  table.mini {{ width:100%; border-collapse:collapse; font-size:11.5px; }}
  table.mini th {{ background:#EEF2F7; padding:7px 8px; text-align:left; color:#475569; white-space:nowrap; }}
  table.mini td {{ padding:6px 8px; border-top:1px solid var(--line); vertical-align:top; }}
  .checks {{ display:flex; flex-direction:column; gap:6px; }}
  .check {{ font-size:11.5px; line-height:1.5; padding:6px 8px; border-radius:8px; background:#fff; border:1px solid var(--line); }}
  .check.warn {{ background:#FFFBEB; border-color:#FDE68A; }}
  .check.fail {{ background:#FEF2F2; border-color:#FECACA; }}
  .prov-meta {{ font-size:11.5px; line-height:1.9; color:#475569; }}
  code {{ font-family:"JetBrains Mono","Cascadia Code",Consolas,monospace; font-size:11px; }}
  pre {{ background:#0F172A; color:#E2E8F0; padding:16px; border-radius:12px;
    overflow:auto; font-size:12px; line-height:1.6; }}
  @media (max-width:760px) {{ .prov-grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <h1>{_esc(report.title)}</h1>
    <p>{_esc(report.connection_name)} · {_esc(report.dialect)} · 共 {len(report.blocks)} 个分析模块</p>
    <span class="badge">由 数语 生成 · {generated}</span>
  </header>
  {''.join(blocks)}
</div>
<script>{echarts_js}</script>
<script>
const REPORT = {payload};
function hydrate(option) {{
  if (!option) return option;
  const walk = (node) => {{
    if (Array.isArray(node)) {{ node.forEach(walk); return; }}
    if (!node || typeof node !== 'object') return;
    for (const [key, value] of Object.entries(node)) {{
      if (key === 'formatter' && value === 'compactNumber') {{
        node[key] = (v) => {{
          const n = Number(v);
          if (!Number.isFinite(n)) return v ?? '-';
          const sign = n < 0 ? '-' : '';
          const a = Math.abs(n);
          if (a >= 1e8) return sign + (a / 1e8).toFixed(2) + '亿';
          if (a >= 1e4) return sign + (a / 1e4).toFixed(2) + '万';
          return sign + a.toLocaleString('zh-CN', {{ maximumFractionDigits: 2 }});
        }};
      }} else if (key === 'formatter' && value === 'categoryLabel') {{
        node[key] = (v) => String(v ?? '').length > 12 ? String(v).slice(0, 12) + '…' : v;
      }} else {{
        walk(value);
      }}
    }}
  }};
  walk(option);
  return option;
}}
REPORT.blocks.forEach((block, i) => {{
  const el = document.getElementById('chart-' + i);
  if (el && block.chart && block.chart.option) {{
    const chart = echarts.init(el, null, {{ renderer: 'canvas' }});
    chart.setOption(hydrate(block.chart.option));
    window.addEventListener('resize', () => chart.resize());
  }}
}});
</script>
</body>
</html>
"""
