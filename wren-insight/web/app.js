(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    connections: [],
    sourceTypes: [],
    activeId: localStorage.getItem("wren.activeId") || null,
    summary: null,
    suggestions: [],
    messages: [],
    loading: false,
    loadingTimer: null,
    reports: [],
    currentReportId: null,
    currentReport: null,
    theme: localStorage.getItem("wren.theme") || "light",
    charts: [],
  };

  // ------------------------------------------------------------------ utils
  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function compactNumber(value) {
    if (value === null || value === undefined || value === "") return "-";
    if (Array.isArray(value)) return value.map(compactNumber).join(", ");
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    const sign = number < 0 ? "-" : "";
    const abs = Math.abs(number);
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + "亿";
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(2) + "万";
    return sign + abs.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function formatCell(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "number") return compactNumber(value);
    return String(value);
  }

  function toast(message, kind = "") {
    const root = $("#toast-root");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      el.style.transition = "0.25s";
      setTimeout(() => el.remove(), 260);
    }, 3200);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (!response.ok) {
      let detail = null;
      try {
        detail = (await response.json()).detail;
      } catch (_) {}
      const message =
        typeof detail === "string"
          ? detail
          : detail && (detail.message || detail.sql)
            ? detail.message || detail.sql
            : `请求失败（HTTP ${response.status}）`;
      const error = new Error(message);
      error.detail = detail;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }

  // ------------------------------------------------------------------ theme
  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    localStorage.setItem("wren.theme", state.theme);
    $("#theme-label").textContent = state.theme === "dark" ? "浅色模式" : "深色模式";
  }

  function toggleTheme() {
    state.theme = state.theme === "dark" ? "light" : "dark";
    applyTheme();
    renderMessages();
    if (state.currentReport) renderReportDetail();
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function hydrateOption(option) {
    const clone = JSON.parse(JSON.stringify(option));
    const walk = (node) => {
      if (Array.isArray(node)) {
        node.forEach(walk);
        return;
      }
      if (!node || typeof node !== "object") return;
      for (const [key, value] of Object.entries(node)) {
        if (key === "formatter" && value === "compactNumber") {
          node[key] = compactNumber;
        } else if (key === "formatter" && value === "categoryLabel") {
          node[key] = (v) => {
            const text = String(v ?? "");
            return text.length > 12 ? text.slice(0, 12) + "…" : text;
          };
        } else if (key === "valueFormatter" && value === "compactNumber") {
          node[key] = compactNumber;
        } else {
          walk(value);
        }
      }
    };
    walk(clone);

    const muted = cssVar("--muted");
    const line = cssVar("--line");
    const ink = cssVar("--ink");
    const inkSoft = cssVar("--ink-soft");
    const panel = cssVar("--panel");
    const theme = (node) => {
      if (Array.isArray(node)) {
        node.forEach(theme);
        return;
      }
      if (!node || typeof node !== "object") return;
      if (node.axisLabel) node.axisLabel.color = muted;
      if (node.nameTextStyle) node.nameTextStyle.color = muted;
      if (node.axisLine && node.axisLine.lineStyle) node.axisLine.lineStyle.color = line;
      if (node.splitLine && node.splitLine.lineStyle) node.splitLine.lineStyle.color = line;
      if (node.textStyle) node.textStyle.color = inkSoft;
      if (node.legend) {
        node.legend.textStyle = { ...(node.legend.textStyle || {}), color: inkSoft };
      }
      if (node.label && typeof node.label === "object") {
        node.label.color = inkSoft;
      }
      if (node.itemStyle && node.itemStyle.borderColor === "#fff") {
        node.itemStyle.borderColor = panel;
      }
      if (node.type === "pie" && node.itemStyle) {
        node.itemStyle.borderColor = panel;
      }
      for (const value of Object.values(node)) theme(value);
    };
    theme(clone);
    if (clone.textStyle) clone.textStyle.color = ink;
    return clone;
  }

  function disposeCharts() {
    state.charts.forEach((chart) => {
      try {
        chart.dispose();
      } catch (_) {}
    });
    state.charts = [];
  }

  // ECharts is ~1MB. Keep it off the first screen and load it only when the
  // first chart is actually rendered; table/KPI-only responses never fetch it.
  let echartsPromise = null;
  function ensureECharts() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (!echartsPromise) {
      echartsPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/vendor/echarts.min.js";
        script.async = true;
        script.onload = () => resolve(window.echarts);
        script.onerror = () => reject(new Error("ECharts 加载失败，请检查 /static/vendor/echarts.min.js"));
        document.head.appendChild(script);
      });
    }
    return echartsPromise;
  }

  function mountChartsNow() {
    disposeCharts();
    $$("[data-chart-msg]").forEach((el) => {
      const index = Number(el.dataset.chartMsg);
      const message = state.messages[index];
      const answer = message && message.answer;
      if (!answer || !answer.chart || !answer.chart.option) return;
      const chart = echarts.init(el, null, { renderer: "canvas" });
      chart.setOption(hydrateOption(answer.chart.option), true);
      state.charts.push(chart);
    });
    $$("[data-chart-report]").forEach((el) => {
      const index = Number(el.dataset.chartReport);
      const block = state.currentReport && state.currentReport.blocks[index];
      if (!block || !block.chart || !block.chart.option) return;
      const chart = echarts.init(el, null, { renderer: "canvas" });
      chart.setOption(hydrateOption(block.chart.option), true);
      state.charts.push(chart);
    });
  }

  function mountCharts() {
    disposeCharts();
    if (!document.querySelector("[data-chart-msg], [data-chart-report]")) return;
    ensureECharts()
      .then(() => mountChartsNow())
      .catch((error) => toast(error.message, "error"));
  }

  window.addEventListener("resize", () => {
    state.charts.forEach((chart) => {
      try {
        chart.resize();
      } catch (_) {}
    });
  });

  // -------------------------------------------------------------- data load
  async function loadSourceTypes() {
    try {
      state.sourceTypes = await api("/api/source-types");
    } catch (_) {
      state.sourceTypes = [];
    }
  }

  function sourceDef(sourceId) {
    return state.sourceTypes.find((item) => item.id === sourceId) || null;
  }

  function sourceLabel(sourceId) {
    const definition = sourceDef(sourceId);
    return definition ? definition.label : sourceId;
  }

  const VECTOR_SOURCE_TYPES = new Set(["qdrant", "chroma", "weaviate", "milvus", "pinecone", "pgvector"]);
  function isVectorSource(sourceType) {
    return VECTOR_SOURCE_TYPES.has(sourceType);
  }

  async function loadHealth() {
    const el = $("#llm-status");
    try {
      const health = await api("/api/health");
      const llm = health.llm;
      if (llm.configured) {
        el.className = "status-pill";
        const fallbackCount = (llm.fallbacks || []).length;
        el.textContent = `${llm.provider} · ${llm.model}${fallbackCount ? ` (+${fallbackCount} 备用)` : ""}`;
        el.title = fallbackCount
          ? `${llm.base_url}\n备用模型: ${llm.fallbacks.join(", ")}`
          : llm.base_url;
      } else {
        el.className = "status-pill warn";
        el.textContent = "LLM 未配置";
        el.title = "请设置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL";
        toast("未检测到大模型配置，请在 .env 中设置 LLM_API_KEY", "error");
      }
    } catch (error) {
      el.className = "status-pill bad";
      el.textContent = "服务不可用";
    }
  }

  async function loadConnections() {
    state.connections = await api("/api/connections");
    const select = $("#connection-select");
    select.innerHTML = state.connections
      .map(
        (conn) =>
          `<option value="${escapeHtml(conn.id)}">${escapeHtml(conn.name)}</option>`
      )
      .join("");
    if (!state.connections.length) {
      state.activeId = null;
      select.innerHTML = `<option value="">暂无连接</option>`;
      return;
    }
    const exists = state.connections.some((conn) => conn.id === state.activeId);
    if (!exists) state.activeId = state.connections[0].id;
    select.value = state.activeId;
    localStorage.setItem("wren.activeId", state.activeId);
  }

  function activeConnection() {
    return state.connections.find((conn) => conn.id === state.activeId) || null;
  }

  async function loadSummary() {
    const conn = activeConnection();
    if (!conn) {
      state.summary = null;
      renderSidebar();
      renderMessages();
      return;
    }
    try {
      state.summary = await api(`/api/connections/${encodeURIComponent(conn.id)}/summary`);
    } catch (error) {
      state.summary = { built: false, error: error.message };
    }
    renderSidebar();
    renderMessages();
    if (state.summary && state.summary.built) {
      await loadSuggestions();
    } else {
      state.suggestions = [];
      renderSuggestions();
    }
  }

  async function loadSuggestions() {
    if (!activeConnection()) return;
    try {
      const data = await api(`/api/suggestions?connection_id=${encodeURIComponent(state.activeId)}`);
      state.suggestions = data.suggestions || [];
    } catch (_) {
      state.suggestions = [];
    }
    renderSuggestions();
  }

  // ------------------------------------------------------------- sidebar UI
  function renderSidebar() {
    const conn = activeConnection();
    const card = $("#connection-card");
    const summary = state.summary || {};
    $("#edit-ontology").disabled = !(summary && summary.built);
    if (!conn) {
      card.innerHTML = `<div>还没有数据连接</div>`;
      $("#semantic-summary").innerHTML = "";
      $("#metrics-list").innerHTML = "";
      $("#tables-list").innerHTML = "";
      return;
    }
    const source = sourceLabel(conn.dialect);
    let target;
    if (conn.dialect === "duckdb" || conn.dialect === "athena") {
      target = conn.database || "";
    } else if (isVectorSource(conn.dialect)) {
      target = conn.host || "";
    } else {
      target = `${conn.host || ""}${conn.port ? `:${conn.port}` : ""}`;
    }
    const catalog = conn.options && conn.options.catalog ? `catalog=${conn.options.catalog}` : "";
    const collection = conn.options && conn.options.collection ? `collection=${conn.options.collection}` : "";
    const detail = [target, conn.schema_name, catalog, collection].filter(Boolean).join(" / ");
    card.innerHTML = `
      <strong>${escapeHtml(conn.name)}</strong>
      <span class="dialect">${escapeHtml(source)}${conn.database ? ` · ${escapeHtml(conn.database)}` : ""}</span>
      <span title="${escapeHtml(detail)}">${escapeHtml(detail.length > 48 ? detail.slice(0, 48) + "…" : detail)}</span>
      <div class="conn-actions">
        <button class="text-btn" data-action="edit-connection">编辑连接</button>
        <button class="text-btn" data-action="scan">${summary && summary.built ? "重建语义模型" : "扫描并生成语义模型"}</button>
      </div>`;

    if (!summary || !summary.built) {
      $("#semantic-summary").innerHTML = `
        <div class="notice" style="margin-top:0">
          尚未生成语义模型。扫描数据库后会自动生成本体、指标口径和 Wren MDL。
        </div>`;
      $("#metrics-list").innerHTML = "";
      $("#tables-list").innerHTML = "";
      return;
    }

    $("#semantic-summary").innerHTML = `
      <span class="stat-chip"><b>${summary.entities ? summary.entities.length : 0}</b> 实体</span>
      <span class="stat-chip"><b>${summary.metrics ? summary.metrics.length : 0}</b> 指标</span>
      <span class="stat-chip"><b>${summary.dimensions ? summary.dimensions.length : 0}</b> 维度</span>`;
    $("#metrics-list").innerHTML = (summary.metrics || [])
      .slice(0, 8)
      .map(
        (metric) =>
          `<div class="metric-row"><span>${escapeHtml(metric.label || metric.name)}</span><span>${escapeHtml(metric.unit || "")}</span></div>`
      )
      .join("");
    const tables = summary.tables || [];
    $("#tables-list").innerHTML = tables.length
      ? tables
          .map(
            (name) =>
              `<div class="table-row"><span>${escapeHtml(name)}</span><span class="count">${escapeHtml(
                (summary.table_rows && summary.table_rows[name]) || ""
              )}</span></div>`
          )
          .join("")
      : `<div class="table-row"><span>暂无数据表</span></div>`;
  }

  function renderSuggestions() {
    const root = $("#suggestions");
    if (state.messages.length > 0 || state.loading) {
      root.innerHTML = "";
      return;
    }
    root.innerHTML = state.suggestions
      .map((question) => `<button class="chip" data-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`)
      .join("");
  }

  // ------------------------------------------------------------- chat view
  function emptyState() {
    const conn = activeConnection();
    if (!conn) {
      return `
        <div class="hero-empty">
          <span class="eyebrow">开始使用</span>
          <h1>先连接一个<span class="accent">数据库</span></h1>
          <p>支持 PostgreSQL、MySQL、Oracle、SQL Server，以及 Trino / Databricks / Athena / DuckDB 等数据湖与 Qdrant / Chroma / Weaviate 向量数据库。连接后会扫描 schema，自动生成业务本体与语义模型。</p>
          <div style="margin-top:24px"><button class="primary-btn" data-action="add-connection">新增数据连接</button></div>
        </div>`;
    }
    if (!state.summary || !state.summary.built) {
      return `
        <div class="hero-empty">
          <span class="eyebrow">第一步</span>
          <h1>生成你的<span class="accent">语义模型</span></h1>
          <p>数语会读取 ${escapeHtml(conn.database || conn.name)} 的表结构、外键和样例值，自动生成实体、指标、维度与 Wren MDL。之后每个问题都会经过这层语义映射再生成 SQL。</p>
          <div style="margin-top:24px">
            <button class="primary-btn" data-action="scan">扫描 ${escapeHtml(conn.database || conn.name)} 并生成</button>
          </div>
          <div class="hero-features">
            <div class="feature"><b>本体建模</b><span>业务概念、同义词、关系与口径沉淀为可编辑 YAML。</span></div>
            <div class="feature"><b>语义 SQL</b><span>只允许查询已建模对象，自动排除取消/退款订单。</span></div>
            <div class="feature"><b>数据溯源</b><span>报表字段可回溯到模型、物理表/文件、指标口径与 SQL。</span></div>
          </div>
        </div>`;
    }
    return `
      <div class="hero-empty">
        <span class="eyebrow">语义层就绪 · ${escapeHtml(state.summary.domain || "业务数据")}</span>
        <h1>用一句话，<span class="accent">挖掘数据</span></h1>
        <p>问题会先经过本体与指标口径，再生成受约束的只读 SQL。试试下面的问题，或直接输入你的业务问题。</p>
        <div class="hero-features">
          <div class="feature"><b>业务语言优先</b><span>“高价值客户”“销售额”等术语映射到跨表字段与过滤条件。</span></div>
          <div class="feature"><b>受控执行</b><span>SQL 仅允许查询已建模模型/视图，并由 Wren 引擎校验后执行。</span></div>
          <div class="feature"><b>可溯源报告</b><span>保存分析块时记录来源模型、字段血缘、指标口径和数据检查。</span></div>
        </div>
      </div>`;
  }

  function tableHtml(columns, rows, limit = 20) {
    if (!rows || !rows.length) {
      return `<div class="notice">查询没有返回数据。</div>`;
    }
    const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
    const body = rows
      .slice(0, limit)
      .map(
        (row) =>
          `<tr>${columns
            .map((column) => `<td>${escapeHtml(formatCell(row[column]))}</td>`)
            .join("")}</tr>`
      )
      .join("");
    const more =
      rows.length > limit
        ? `<div class="meta-row"><span class="meta-chip">显示前 ${limit} 行，共 ${rows.length} 行</span></div>`
        : "";
    return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
  }

  // --------------------------------------------------------- provenance UI
  function physicalName(ref) {
    return [ref.catalog, ref.schema_name, ref.table].filter(Boolean).join(".");
  }

  function provenanceBodyHtml(provenance, index, interactive = false) {
    if (!provenance) {
      return `<div class="notice">本次结果未生成溯源信息。</div>`;
    }
    const icon = { pass: "✓", warn: "!", fail: "×" };
    const checks = (provenance.checks || [])
      .map(
        (check) =>
          `<div class="check ${escapeHtml(check.status)}"><span class="check-icon">${icon[check.status] || "•"}</span><b>${escapeHtml(check.label)}</b><span>${escapeHtml(check.detail || "")}</span></div>`
      )
      .join("");
    const sources = (provenance.source_tables || [])
      .map(
        (ref) =>
          `<tr><td>${escapeHtml(ref.model)}</td><td>${escapeHtml(ref.kind)}</td><td>${escapeHtml(physicalName(ref))}</td><td>${escapeHtml(ref.location || "")}</td></tr>`
      )
      .join("");
    const lineage = (provenance.lineage || [])
      .map(
        (edge) =>
          `<tr><td>${escapeHtml(edge.output_column)}</td><td>${escapeHtml(physicalName({ catalog: edge.source_catalog, schema_name: edge.source_schema, table: edge.source_table }))}.${escapeHtml(edge.source_column)}</td><td><code>${escapeHtml(edge.expression || "")}</code></td></tr>`
      )
      .join("");
    const metrics = (provenance.metrics_used || [])
      .map(
        (metric) =>
          `<li><b>${escapeHtml(metric.label || metric.name)}</b>：<code>${escapeHtml(metric.expression || "")}</code>${metric.filters ? `；过滤：<code>${escapeHtml(metric.filters)}</code>` : ""}</li>`
      )
      .join("");
    const rules = (provenance.rules_used || []).map((rule) => `<li>${escapeHtml(rule)}</li>`).join("");
    const freshness = (provenance.freshness || [])
      .map(
        (item) =>
          `<tr><td>${escapeHtml(item.model)}</td><td>${escapeHtml(item.table)}</td><td>${item.row_count ?? "-"}</td><td>${escapeHtml(item.max_time_column || "-")}</td><td>${escapeHtml(item.max_time_value || "-")}</td><td>${escapeHtml(item.note || "")}</td></tr>`
      )
      .join("");
    const scoreClass = provenance.reliability_score >= 85 ? "good" : provenance.reliability_score >= 65 ? "warn" : "bad";
    const vector = isVectorSource(provenance.source_type);
    return `
      <div class="prov-score ${scoreClass}">
        <div class="score-number">${provenance.reliability_score}</div>
        <div>
          <div class="score-title">数据可靠性评分</div>
          <div class="score-sub">${escapeHtml(provenance.source_type)} · ${escapeHtml(provenance.database || "")}${provenance.schema_name ? " / " + escapeHtml(provenance.schema_name) : ""} · ${provenance.row_count} 行 · ${provenance.elapsed_ms} ms</div>
        </div>
      </div>
      <div class="prov-section">
        <h4>来源映射</h4>
        <div class="prov-table-wrap"><table class="mini-table">
          <thead><tr><th>模型 / 视图</th><th>类型</th><th>物理对象</th><th>文件 / 位置</th></tr></thead>
          <tbody>${sources || '<tr><td colspan="4">未解析到来源模型</td></tr>'}</tbody>
        </table></div>
      </div>
      <div class="prov-section">
        <h4>可靠性检查</h4>
        <div class="checks">${checks}</div>
      </div>
      <div class="prov-grid">
        <div class="prov-section">
          <h4>使用的指标口径</h4>
          <ul>${metrics || "<li>本次问题未匹配到预定义指标。</li>"}</ul>
          <h4>业务规则</h4>
          <ul>${rules || "<li>无</li>"}</ul>
        </div>
        <div class="prov-section">
          <h4>字段级血缘</h4>
          <div class="prov-table-wrap"><table class="mini-table">
            <thead><tr><th>结果字段</th><th>来源字段</th><th>表达式</th></tr></thead>
            <tbody>${lineage || '<tr><td colspan="3">未能计算字段级血缘，可查看表级来源。</td></tr>'}</tbody>
          </table></div>
        </div>
      </div>
      <div class="prov-section">
        <h4>数据新鲜度 ${interactive ? '<button class="action-btn" data-refresh-freshness>刷新新鲜度</button>' : ""}</h4>
        <div class="prov-table-wrap"><table class="mini-table">
          <thead><tr><th>模型</th><th>表</th><th>行数</th><th>时间字段</th><th>最大值</th><th>说明</th></tr></thead>
          <tbody>${freshness || '<tr><td colspan="6">无新鲜度信息</td></tr>'}</tbody>
        </table></div>
      </div>
      <div class="prov-section">
        <h4>执行信息</h4>
        <div class="prov-meta">
          <div>生成时间：${escapeHtml(provenance.generated_at || "-")}</div>
          <div>本体生成时间：${escapeHtml(provenance.ontology_generated_at || "-")}</div>
          <div>实际模型：${escapeHtml(provenance.llm_provider || "")} · ${escapeHtml(provenance.llm_model || "")} · 尝试 ${provenance.attempts} 次</div>
        </div>
      </div>
      <details class="sql"><summary>${vector ? "查看查询计划 JSON" : "查看语义 SQL"}</summary><pre class="sql">${escapeHtml(provenance.sql || "")}</pre></details>
      <details class="sql"><summary>${vector ? "查看向量库请求" : "查看 Wren 改写后的物理 SQL"}</summary><pre class="sql">${escapeHtml(provenance.planned_sql || "未提供")}</pre></details>`;
  }

  function openProvenanceModal(index) {
    const message = state.messages[index];
    const provenance = message && message.answer && message.answer.provenance;
    if (!provenance) {
      toast("本次结果没有溯源信息", "error");
      return;
    }
    openModal(`
      <div class="modal wide">
        <button class="modal-close" data-close>×</button>
        <h2>数据溯源与可靠性</h2>
        <p class="modal-sub">从问题、本体、指标口径、MDL 模型/视图、物理表/文件到结果字段的完整链路。</p>
        <div id="provenance-body">${provenanceBodyHtml(provenance, index, true)}</div>
      </div>`);
    $$("[data-close]", $("#modal-root")).forEach((button) => button.addEventListener("click", closeModal));
    const refresh = $("[data-refresh-freshness]", $("#modal-root"));
    if (refresh) {
      refresh.addEventListener("click", async () => {
        refresh.disabled = true;
        refresh.textContent = "探测中…";
        try {
          const models = (provenance.source_tables || []).filter((ref) => ref.kind === "model").map((ref) => ref.model);
          const result = await api(`/api/connections/${encodeURIComponent(provenance.connection_id)}/freshness`, {
            method: "POST",
            body: JSON.stringify({ models }),
          });
          provenance.freshness = result.freshness || [];
          openProvenanceModal(index);
          toast("数据新鲜度已刷新", "success");
        } catch (error) {
          toast(error.message, "error");
          refresh.disabled = false;
          refresh.textContent = "刷新新鲜度";
        }
      });
    }
  }

  function answerCard(answer, index) {
    const chart = answer.chart || { type: "table", option: null };
    const provenance = answer.provenance || null;
    const score = provenance ? provenance.reliability_score : null;
    const scoreClass = score == null ? "" : score >= 85 ? "good" : score >= 65 ? "warn" : "bad";
    const vector = isVectorSource(provenance && provenance.source_type);
    let visual = "";
    if (chart.type === "kpi" && chart.option) {
      visual = `
        <div class="kpi-card">
          <div class="kpi-label">${escapeHtml(chart.option.label || "")}</div>
          <div class="kpi-value">${escapeHtml(chart.option.formatted || compactNumber(chart.option.value))}</div>
        </div>`;
    } else if (chart.type !== "table" && chart.option) {
      visual = `<div class="chart-box" data-chart-msg="${index}"></div>`;
    }
    const warnings = (answer.warnings || [])
      .map((warning) => `<div class="notice warn">${escapeHtml(warning)}</div>`)
      .join("");
    const assumptions = (answer.assumptions || [])
      .map((item) => `<div class="notice">假设：${escapeHtml(item)}</div>`)
      .join("");
    const chartTypes = ["line", "bar", "pie", "table"];
    const switcher = answer.rows && answer.rows.length
      ? `<div class="chart-switch">${chartTypes
          .map(
            (type) =>
              `<button data-chart-switch="${type}" data-msg-index="${index}" class="${chart.type === type ? "active" : ""}">${({ line: "折线", bar: "柱状", pie: "占比", table: "表格" })[type]}</button>`
          )
          .join("")}</div>`
      : "";
    const meta = [
      `${answer.row_count} 行`,
      `${answer.elapsed_ms} ms`,
      answer.attempts > 1 ? `${answer.attempts} 次生成` : null,
      answer.provider && answer.model ? `${answer.provider} · ${answer.model}` : null,
    ]
      .filter(Boolean)
      .map((item) => `<span class="meta-chip">${escapeHtml(item)}</span>`)
      .join("");

    return `
      <div class="msg">
        <div class="assistant-card">
          <div class="card-head">
            <div>
              <div class="card-title-row">
                <h2 class="card-title">${escapeHtml(answer.title)}</h2>
                ${score != null ? `<span class="reliability ${scoreClass}" title="数据可靠性评分">可靠性 ${score}</span>` : ""}
              </div>
              <p class="card-question">${escapeHtml(answer.question)}</p>
            </div>
            <div class="chart-switch-wrap">${switcher}</div>
          </div>
          <div class="card-body">
            ${answer.explanation ? `<p class="explain">${escapeHtml(answer.explanation)}</p>` : ""}
            ${visual}
            ${answer.rows && answer.rows.length ? tableHtml(answer.columns, answer.rows) : ""}
            ${warnings}
            ${assumptions}
            <div class="meta-row">${meta}</div>
            <div class="card-actions">
              <button class="action-btn primary" data-action="save-report" data-msg-index="${index}">保存到报告</button>
              ${provenance ? `<button class="action-btn" data-action="provenance" data-msg-index="${index}">数据溯源</button>` : ""}
              <button class="action-btn" data-action="copy-sql" data-msg-index="${index}">复制 SQL</button>
              <button class="action-btn" data-action="csv" data-msg-index="${index}">导出 CSV</button>
              <button class="action-btn" data-action="rerun" data-msg-index="${index}">重新运行</button>
            </div>
            <details class="sql">
              <summary>${vector ? "查看查询计划" : "查看生成的 SQL"}</summary>
              <pre class="sql">${escapeHtml(answer.sql)}</pre>
            </details>
          </div>
        </div>
      </div>`;
  }

  function renderMessages() {
    const root = $("#messages");
    disposeCharts();
    if (!state.messages.length) {
      root.innerHTML = emptyState();
      return;
    }
    root.innerHTML = state.messages
      .map((message, index) => {
        if (message.role === "user") {
          return `<div class="msg user"><div class="bubble">${escapeHtml(message.content)}</div></div>`;
        }
        if (message.loading) {
          return `
            <div class="msg">
              <div class="assistant-card">
                <div class="loading-card">
                  <div class="loader"></div>
                  <div>
                    <div class="loading-title">正在生成受约束的 SQL…</div>
                    <div class="loading-sub" data-loading-sub>理解问题 → 映射本体 → 规划 SQL → 执行查询</div>
                  </div>
                </div>
              </div>
            </div>`;
        }
        if (message.error) {
          const detail = message.error.detail || {};
          const sql = typeof detail === "object" && detail ? detail.sql : null;
          return `
            <div class="msg">
              <div class="assistant-card">
                <div class="card-head"><h2 class="card-title">分析失败</h2></div>
                <div class="card-body">
                  <div class="notice error">${escapeHtml(message.error.message || "未知错误")}</div>
                  ${
                    sql
                      ? `<details class="sql"><summary>查看失败的 SQL</summary><pre class="sql">${escapeHtml(sql)}</pre></details>`
                      : ""
                  }
                  <div class="card-actions">
                    <button class="action-btn" data-action="retry-last">重新提问</button>
                    <button class="action-btn" data-action="open-ontology">检查本体</button>
                  </div>
                </div>
              </div>
            </div>`;
        }
        return answerCard(message.answer, index);
      })
      .join("");
    mountCharts();
    requestAnimationFrame(() => {
      if (state.loading) {
        root.scrollTop = root.scrollHeight;
        return;
      }
      const last = root.querySelector(".msg:last-child");
      if (last) {
        last.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
    if (state.loading) updateLoadingText();
  }

  function updateLoadingText() {
    const el = $("[data-loading-sub]");
    if (!el) return;
    const started = state.loadingStarted || Date.now();
    const seconds = Math.max(0, Math.round((Date.now() - started) / 1000));
    const phases = [
      "理解问题 → 映射本体 → 规划 SQL → 执行查询",
      "正在从语义层召回指标口径…",
      "正在生成 SQL 并通过 Wren 校验…",
      "正在执行查询并准备图表…",
    ];
    const phase = phases[Math.min(phases.length - 1, Math.floor(seconds / 4))];
    el.textContent = `${phase} · ${seconds}s`;
  }

  // ------------------------------------------------------------- ask action
  async function sendQuestion(questionOverride) {
    const input = $("#question-input");
    const question = (questionOverride || input.value).trim();
    if (!question) return;
    if (state.loading) return;
    if (!activeConnection()) {
      toast("请先新增一个数据连接", "error");
      openConnectionModal();
      return;
    }
    if (!state.summary || !state.summary.built) {
      toast("请先生成语义模型", "error");
      return;
    }
    input.value = "";
    input.style.height = "auto";
    const history = state.messages
      .filter((message) => message.role === "user" || (message.answer && message.answer.title))
      .slice(-4)
      .map((message) => ({
        role: message.role,
        content:
          message.role === "user"
            ? message.content
            : `${message.answer.title}：${message.answer.explanation || ""}`,
      }));
    state.messages.push({ role: "user", content: question });
    state.messages.push({ role: "assistant", loading: true });
    state.loading = true;
    state.loadingStarted = Date.now();
    $("#send-btn").disabled = true;
    renderMessages();
    renderSuggestions();
    state.loadingTimer = setInterval(updateLoadingText, 1000);
    try {
      const answer = await api("/api/ask", {
        method: "POST",
        body: JSON.stringify({
          connection_id: state.activeId,
          question,
          history,
          max_rows: 500,
        }),
      });
      state.messages.pop();
      state.messages.push({ role: "assistant", answer });
    } catch (error) {
      state.messages.pop();
      state.messages.push({
        role: "assistant",
        error: { message: error.message, detail: error.detail },
      });
    } finally {
      clearInterval(state.loadingTimer);
      state.loadingTimer = null;
      state.loading = false;
      $("#send-btn").disabled = false;
      renderMessages();
      renderSuggestions();
    }
  }

  // ---------------------------------------------------------------- actions
  function copyText(text) {
    navigator.clipboard
      .writeText(text)
      .then(() => toast("已复制", "success"))
      .catch(() => toast("复制失败", "error"));
  }

  function downloadCsv(answer) {
    const columns = answer.columns || [];
    const escape = (value) => {
      const text = value === null || value === undefined ? "" : String(value);
      return `"${text.replace(/"/g, '""')}"`;
    };
    const lines = [columns.map(escape).join(",")];
    (answer.rows || []).forEach((row) => {
      lines.push(columns.map((column) => escape(row[column])).join(","));
    });
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${answer.title || "query-result"}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function switchChart(index, type) {
    const message = state.messages[index];
    if (!message || !message.answer) return;
    try {
      const chart = await api("/api/rechart", {
        method: "POST",
        body: JSON.stringify({
          rows: message.answer.rows,
          columns: message.answer.columns,
          chart_type: type,
          question: message.answer.question,
          title: message.answer.title,
        }),
      });
      message.answer.chart = chart;
      renderMessages();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function ensureReport() {
    const conn = activeConnection();
    if (state.currentReportId) {
      try {
        const report = await api(`/api/reports/${encodeURIComponent(state.currentReportId)}`);
        if (report.connection_id === conn.id) return report;
      } catch (_) {}
      state.currentReportId = null;
    }
    const report = await api("/api/reports", {
      method: "POST",
      body: JSON.stringify({
        connection_id: conn.id,
        title: `${conn.name} 分析报告`,
      }),
    });
    state.currentReportId = report.id;
    await loadReports();
    return report;
  }

  async function saveAnswerToReport(index) {
    const message = state.messages[index];
    if (!message || !message.answer) return;
    try {
      const report = await ensureReport();
      await api(`/api/reports/${encodeURIComponent(report.id)}/blocks`, {
        method: "POST",
        body: JSON.stringify({ answer: message.answer }),
      });
      await loadReports();
      toast("已保存到报告", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // --------------------------------------------------------------- messages
  $("#messages").addEventListener("click", (event) => {
    const questionChip = event.target.closest("[data-question]");
    if (questionChip) {
      sendQuestion(questionChip.dataset.question);
      return;
    }
    const actionEl = event.target.closest("[data-action]");
    if (actionEl) {
      const action = actionEl.dataset.action;
      if (action === "scan") return scanConnection();
      if (action === "add-connection") return openConnectionModal();
      if (action === "edit-connection") return openConnectionModal(activeConnection());
      if (action === "open-ontology") return openOntologyModal();
      if (action === "retry-last") {
        const lastUser = [...state.messages].reverse().find((m) => m.role === "user");
        if (lastUser) sendQuestion(lastUser.content);
        return;
      }
      const index = Number(actionEl.dataset.msgIndex);
      if (action === "provenance") return openProvenanceModal(index);
      if (action === "save-report") return saveAnswerToReport(index);
      if (action === "copy-sql") {
        const message = state.messages[index];
        if (message && message.answer) copyText(message.answer.sql);
        return;
      }
      if (action === "csv") {
        const message = state.messages[index];
        if (message && message.answer) downloadCsv(message.answer);
        return;
      }
      if (action === "rerun") {
        const message = state.messages[index];
        if (message && message.answer) sendQuestion(message.answer.question);
        return;
      }
    }
    const switchEl = event.target.closest("[data-chart-switch]");
    if (switchEl) {
      switchChart(Number(switchEl.dataset.msgIndex), switchEl.dataset.chartSwitch);
    }
  });

  $("#suggestions").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-question]");
    if (chip) sendQuestion(chip.dataset.question);
  });

  // -------------------------------------------------------------- scanning
  async function scanConnection() {
    if (!activeConnection()) return;
    if (state.loading) return;
    state.loading = true;
    state.loadingStarted = Date.now();
    state.messages.push({ role: "assistant", loading: true });
    renderMessages();
    state.loadingTimer = setInterval(updateLoadingText, 1000);
    try {
      const result = await api(`/api/connections/${encodeURIComponent(state.activeId)}/scan`, {
        method: "POST",
      });
      toast(
        `语义模型已生成：${result.summary.entities} 个实体、${result.summary.metrics} 个指标`,
        "success"
      );
      state.messages = [];
      await loadSummary();
      await loadSuggestions();
    } catch (error) {
      state.messages.pop();
      toast(error.message, "error");
      renderMessages();
    } finally {
      clearInterval(state.loadingTimer);
      state.loadingTimer = null;
      state.loading = false;
      renderMessages();
      renderSuggestions();
    }
  }

  // ----------------------------------------------------------------- modal
  function closeModal() {
    $("#modal-root").innerHTML = "";
  }

  function openModal(html) {
    $("#modal-root").innerHTML = `<div class="modal-overlay">${html}</div>`;
    const overlay = $(".modal-overlay");
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });
  }

  function connectionFieldValue(conn, key) {
    if (key.startsWith("options.")) {
      return (conn.options || {})[key.slice(8)] ?? "";
    }
    return conn[key] ?? "";
  }

  function sourceFieldHtml(field, conn) {
    const rawValue = connectionFieldValue(conn, field.key);
    const secret = Boolean(field.secret) || field.key === "password";
    const value = secret ? "" : (rawValue !== "" ? rawValue : (field.default || ""));
    const placeholder = secret && rawValue ? "留空保持不变" : (field.placeholder || "");
    const req = field.required ? "required" : "";
    let control = "";
    if (field.type === "select") {
      const selected = String(value || field.default || "");
      const options = (field.choices || [])
        .map((choice) => `<option value="${escapeHtml(choice.value)}" ${String(choice.value) === selected ? "selected" : ""}>${escapeHtml(choice.label)}</option>`)
        .join("");
      control = `<select name="${escapeHtml(field.key)}">${options}</select>`;
    } else if (field.type === "textarea") {
      control = `<textarea name="${escapeHtml(field.key)}" rows="4" placeholder="${escapeHtml(placeholder)}" ${req}>${escapeHtml(value)}</textarea>`;
    } else {
      const type = field.type === "password" ? "password" : field.type === "number" ? "number" : "text";
      control = `<input name="${escapeHtml(field.key)}" type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" ${req} data-secret="${secret ? "1" : "0"}" />`;
    }
    return `
      <div class="form-row ${field.full ? "full" : ""}">
        <label>${escapeHtml(field.label)}${field.required ? " *" : ""}</label>
        ${control}
        ${field.help ? `<div class="form-hint">${escapeHtml(field.help)}</div>` : ""}
      </div>`;
  }

  function renderConnectionFields(conn) {
    const source = sourceDef(conn.dialect) || sourceDef("postgres");
    if (!source) return;
    $("#source-description").textContent = source.description || "";
    $("#source-fields").innerHTML = source.fields
      .map((field) => sourceFieldHtml(field, conn))
      .join("");
  }

  function openConnectionModal(existing) {
    const isEdit = Boolean(existing);
    const conn = existing || { name: "", dialect: "postgres", options: {} };
    const groups = {};
    state.sourceTypes.forEach((item) => {
      const category = item.category || "其他";
      (groups[category] = groups[category] || []).push(item);
    });
    const sourceOptions = Object.entries(groups)
      .map(
        ([category, items]) =>
          `<optgroup label="${escapeHtml(category)}">${items
            .map(
              (item) =>
                `<option value="${escapeHtml(item.id)}" ${item.id === conn.dialect ? "selected" : ""}>${escapeHtml(item.label)}</option>`
            )
            .join("")}</optgroup>`
      )
      .join("");
    openModal(`
      <div class="modal wide">
        <button class="modal-close" data-close>×</button>
        <h2>${isEdit ? "编辑数据连接" : "新增数据连接"}</h2>
        <p class="modal-sub">支持关系型数据库（PostgreSQL / MySQL / Oracle / SQL Server）、Trino / Databricks / Athena 数据湖、DuckDB + Parquet、ClickHouse、BigQuery、Spark，以及 Qdrant / Chroma / Weaviate 向量数据库。密码只保存在本机 data/connections.json，不会发送到大模型。</p>
        <form id="connection-form">
          <input type="hidden" name="id" value="${escapeHtml(conn.id || "")}" />
          <div class="form-grid">
            <div class="form-row full">
              <label>数据源类型</label>
              <select name="__source_type" id="source-type">${sourceOptions}</select>
              <div class="form-hint" id="source-description"></div>
            </div>
            <div id="source-fields" style="display:contents"></div>
            <div class="form-row full">
              <div class="form-hint">扫描时会读取表结构、外键、注释和低基数字段的样例值，用于生成本体。请确保账号仅有只读权限。</div>
            </div>
            <div class="form-row full">
              <div class="modal-actions">
                <button type="button" class="ghost-btn" data-test>测试连接</button>
                <button type="button" class="ghost-btn" data-close>取消</button>
                <button type="submit" class="primary-btn">保存</button>
              </div>
            </div>
          </div>
        </form>
      </div>`);
    const form = $("#connection-form");
    renderConnectionFields(conn);
    form.elements.namedItem("__source_type").addEventListener("change", (event) => {
      const nameValue = form.elements.namedItem("name")?.value || conn.name || "";
      const next = { ...conn, dialect: event.target.value, options: {}, id: conn.id };
      renderConnectionFields(next);
      const nameEl = form.elements.namedItem("name");
      if (nameEl) nameEl.value = nameValue;
    });
    form.querySelector("[data-test]").addEventListener("click", async () => {
      try {
        const result = await api("/api/connections/test", {
          method: "POST",
          body: JSON.stringify(formPayload(form, existing)),
        });
        toast(`连接成功：${result.version}`, "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const saved = await api("/api/connections", {
          method: "POST",
          body: JSON.stringify(formPayload(form, existing)),
        });
        closeModal();
        state.activeId = saved.id;
        state.messages = [];
        state.currentReportId = null;
        state.currentReport = null;
        await loadConnections();
        await loadSummary();
        toast("连接已保存，下一步请生成语义模型", "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    $$("[data-close]", $("#modal-root")).forEach((button) => button.addEventListener("click", closeModal));
  }

  function formPayload(form, existing) {
    const dialect = form.elements.namedItem("__source_type").value;
    const source = sourceDef(dialect);
    const payload = {
      id: form.elements.namedItem("id").value || null,
      name: "",
      dialect,
      host: "",
      port: null,
      database: "",
      user: "",
      password: "",
      schema_name: "",
      options: {},
    };
    if (!source) return payload;
    for (const field of source.fields) {
      const el = form.elements.namedItem(field.key);
      if (!el) continue;
      const raw = el.value;
      if (field.key === "password") {
        payload.password = raw;
        continue;
      }
      if (field.key.startsWith("options.")) {
        const key = field.key.slice(8);
        const hadValue = existing && existing.options && existing.options[key];
        if (field.secret && !raw && hadValue) continue;
        payload.options[key] = raw;
        continue;
      }
      if (field.key === "port") {
        payload.port = raw ? Number(raw) : null;
      } else {
        payload[field.key] = raw;
      }
    }
    return payload;
  }

  async function openOntologyModal() {
    if (!activeConnection() || !state.summary || !state.summary.built) return;
    try {
      const data = await api(`/api/connections/${encodeURIComponent(state.activeId)}/ontology`);
      openModal(`
        <div class="modal wide">
          <button class="modal-close" data-close>×</button>
          <h2>编辑业务本体</h2>
          <p class="modal-sub">YAML 中的实体、同义词、指标口径、业务规则会进入每次 NL2SQL 的提示词，并重新编译 Wren MDL。</p>
          <textarea class="yaml-editor" id="ontology-yaml" spellcheck="false">${escapeHtml(data.yaml)}</textarea>
          <div class="form-hint" style="margin-top:8px">
            提示：修改 metrics 的 expression/filters/description、rules、glossary 通常收益最大；保存后会自动重建语义模型。
          </div>
          <div class="modal-actions">
            <button class="ghost-btn" data-close>取消</button>
            <button class="primary-btn" id="save-ontology">保存并重建</button>
          </div>
        </div>`);
      $$("[data-close]", $("#modal-root")).forEach((button) => button.addEventListener("click", closeModal));
      $("#save-ontology").addEventListener("click", async () => {
        const button = $("#save-ontology");
        button.disabled = true;
        button.textContent = "正在重建…";
        try {
          await api(`/api/connections/${encodeURIComponent(state.activeId)}/ontology`, {
            method: "PUT",
            body: JSON.stringify({ yaml: $("#ontology-yaml").value }),
          });
          closeModal();
          await loadSummary();
          toast("本体已保存，语义模型已重建", "success");
        } catch (error) {
          toast(error.message, "error");
          button.disabled = false;
          button.textContent = "保存并重建";
        }
      });
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function confirmDialog(message) {
    return new Promise((resolve) => {
      openModal(`
        <div class="modal" style="max-width:440px">
          <h2>确认操作</h2>
          <p class="modal-sub">${escapeHtml(message)}</p>
          <div class="modal-actions">
            <button class="ghost-btn" data-cancel>取消</button>
            <button class="primary-btn" data-confirm>确认</button>
          </div>
        </div>`);
      $("[data-cancel]").addEventListener("click", () => {
        closeModal();
        resolve(false);
      });
      $("[data-confirm]").addEventListener("click", () => {
        closeModal();
        resolve(true);
      });
    });
  }

  // ---------------------------------------------------------------- reports
  async function loadReports() {
    try {
      state.reports = await api("/api/reports");
    } catch (_) {
      state.reports = [];
    }
    renderReportsList();
  }

  function renderReportsList() {
    const root = $("#reports-list");
    if (!state.reports.length) {
      root.innerHTML = `<div class="empty" style="padding:18px;color:var(--muted);font-size:12px">还没有报告。在分析页把结果保存到报告即可。</div>`;
      return;
    }
    root.innerHTML = state.reports
      .map(
        (report) => `
        <div class="report-item ${report.id === state.currentReportId ? "active" : ""}" data-report-id="${escapeHtml(report.id)}">
          <b>${escapeHtml(report.title)}</b>
          <span>${escapeHtml(report.connection_name || "")} · ${report.block_count} 个模块 · ${escapeHtml(
            String(report.updated_at || "").slice(0, 10)
          )}</span>
        </div>`
      )
      .join("");
  }

  async function openReport(reportId) {
    try {
      state.currentReportId = reportId;
      state.currentReport = await api(`/api/reports/${encodeURIComponent(reportId)}`);
      renderReportsList();
      renderReportDetail();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderReportDetail() {
    const root = $("#report-detail");
    const report = state.currentReport;
    if (!report) {
      root.innerHTML = `<div class="empty">选择或新建一份报告</div>`;
      return;
    }
    disposeCharts();
    const blocks = report.blocks
      .map((block, index) => {
        let visual = "";
        if (block.chart.type === "kpi" && block.chart.option) {
          visual = `
            <div class="kpi-card">
              <div class="kpi-label">${escapeHtml(block.chart.option.label || "")}</div>
              <div class="kpi-value">${escapeHtml(block.chart.option.formatted || compactNumber(block.chart.option.value))}</div>
            </div>`;
        } else if (block.chart.type !== "table" && block.chart.option) {
          visual = `<div class="chart-box" data-chart-report="${index}"></div>`;
        }
        const provenance = block.provenance
          ? `<details class="prov"><summary>数据溯源与可靠性 · ${block.provenance.reliability_score}/100</summary>${provenanceBodyHtml(block.provenance, null, false)}</details>`
          : "";
        const vector = isVectorSource(block.provenance && block.provenance.source_type);
        return `
          <div class="report-block">
            <h3>${escapeHtml(block.title)}</h3>
            <p class="question">${escapeHtml(block.question)}</p>
            ${block.explanation ? `<p class="explain">${escapeHtml(block.explanation)}</p>` : ""}
            ${visual}
            ${block.rows && block.rows.length ? tableHtml(block.columns, block.rows, 30) : ""}
            <details class="sql"><summary>${vector ? "查看查询计划" : "查看 SQL"}</summary><pre class="sql">${escapeHtml(block.sql)}</pre></details>
            ${provenance}
          </div>`;
      })
      .join("");
    root.innerHTML = `
      <div class="report-head">
        <div>
          <h1>${escapeHtml(report.title)}</h1>
          <p>${escapeHtml(report.connection_name)} · ${escapeHtml(report.dialect)} · ${report.blocks.length} 个分析模块</p>
        </div>
        <div class="card-actions" style="margin-top:0">
          <button class="action-btn" data-report-action="export">导出 HTML</button>
          <button class="action-btn" data-report-action="download">下载</button>
          <button class="action-btn" data-report-action="delete">删除</button>
        </div>
      </div>
      ${blocks || `<div class="empty">这份报告还没有内容</div>`}`;
    mountCharts();
    root.querySelectorAll("[data-report-action]").forEach((button) => {
      button.addEventListener("click", () => handleReportAction(button.dataset.reportAction));
    });
  }

  async function handleReportAction(action) {
    if (!state.currentReport) return;
    const id = state.currentReport.id;
    if (action === "export") {
      try {
        const result = await api(`/api/reports/${encodeURIComponent(id)}/export`, { method: "POST" });
        window.open(result.url, "_blank");
      } catch (error) {
        toast(error.message, "error");
      }
    } else if (action === "download") {
      window.open(`/api/reports/${encodeURIComponent(id)}/download`, "_blank");
    } else if (action === "delete") {
      const ok = await confirmDialog(`确定删除报告「${state.currentReport.title}」吗？此操作不可恢复。`);
      if (!ok) return;
      try {
        await api(`/api/reports/${encodeURIComponent(id)}`, { method: "DELETE" });
        state.currentReport = null;
        state.currentReportId = null;
        await loadReports();
        renderReportDetail();
        toast("报告已删除", "success");
      } catch (error) {
        toast(error.message, "error");
      }
    }
  }

  $("#reports-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-report-id]");
    if (item) openReport(item.dataset.reportId);
  });

  $("#create-report").addEventListener("click", async () => {
    if (!activeConnection()) {
      toast("请先选择数据连接", "error");
      return;
    }
    try {
      const report = await api("/api/reports", {
        method: "POST",
        body: JSON.stringify({ connection_id: state.activeId }),
      });
      await loadReports();
      await openReport(report.id);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  // ------------------------------------------------------------------ nav
  function switchView(view) {
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
    $("#analysis-view").classList.toggle("hidden", view !== "analysis");
    $("#reports-view").classList.toggle("hidden", view !== "reports");
    if (view === "reports") {
      loadReports().then(() => {
        if (state.currentReportId) openReport(state.currentReportId);
      });
    } else {
      mountCharts();
    }
  }

  $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));

  $("#new-analysis").addEventListener("click", () => {
    state.messages = [];
    state.currentReportId = null;
    state.currentReport = null;
    renderMessages();
    renderSuggestions();
    $("#question-input").focus();
  });

  // --------------------------------------------------------------- header
  $("#send-btn").addEventListener("click", () => sendQuestion());
  $("#question-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendQuestion();
    }
  });
  $("#question-input").addEventListener("input", (event) => {
    const el = event.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  });

  $("#connection-select").addEventListener("change", async (event) => {
    state.activeId = event.target.value;
    localStorage.setItem("wren.activeId", state.activeId);
    state.messages = [];
    state.currentReportId = null;
    state.currentReport = null;
    await loadSummary();
  });

  $("#add-connection").addEventListener("click", () => openConnectionModal());
  $("#edit-ontology").addEventListener("click", openOntologyModal);
  $("#theme-toggle").addEventListener("click", toggleTheme);

  $("#connection-card").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]");
    if (!action) return;
    if (action.dataset.action === "scan") scanConnection();
    if (action.dataset.action === "edit-connection") openConnectionModal(activeConnection());
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  // ------------------------------------------------------------------- init
  async function init() {
    applyTheme();
    renderMessages();
    await loadSourceTypes();
    await loadHealth();
    await loadConnections();
    await loadSummary();
    await loadReports();
    renderSuggestions();
  }

  init().catch((error) => {
    toast(error.message, "error");
    console.error(error);
  });
})();
