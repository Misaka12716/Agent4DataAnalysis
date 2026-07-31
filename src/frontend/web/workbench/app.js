(function () {
  "use strict";

  const meta = document.querySelector('meta[name="agent-api-base"]');
  const API = (meta && meta.content) || "/agent-api";
  let token = localStorage.getItem("agent_access_token") || "";
  let runId = null;
  let pollTimer = null;
  let chartQueue = [];
  let columnProfile = null;
  let chartTypes = [];
  let selectedCols = new Set();
  let preferredTypes = new Set();

  const $ = (id) => document.getElementById(id);

  function formatApiError(payload, fallback) {
    if (payload == null || payload === "") return fallback || "请求失败";
    if (typeof payload === "string") return payload;
    if (Array.isArray(payload)) {
      return payload
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            return item.msg || item.message || JSON.stringify(item);
          }
          return String(item);
        })
        .join("; ");
    }
    if (typeof payload === "object") {
      if (payload.msg) return String(payload.msg);
      if (payload.message) return String(payload.message);
      if (payload.detail != null) return formatApiError(payload.detail, fallback);
      try {
        return JSON.stringify(payload);
      } catch (e) {
        return fallback || "请求失败";
      }
    }
    return String(payload);
  }

  function authHeaders(json) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    if (token) h["Authorization"] = "Bearer " + token;
    return h;
  }

  async function api(method, path, body) {
    const opts = { method, headers: authHeaders(body != null) };
    if (body != null) opts.body = JSON.stringify(body);
    const r = await fetch(API + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(formatApiError(data.detail != null ? data.detail : data, r.statusText || "请求失败"));
    }
    return data.data != null ? data.data : data;
  }

  function md(text) {
    if (!text) return "";
    return String(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/^## (.+)$/gm, "<h4>$1</h4>")
      .replace(/\n/g, "<br/>");
  }

  async function createSession() {
    const created = await api("POST", "/workbench/session/create", {});
    const sid = created.session_id || (created.data && created.data.session_id) || "";
    if (!sid.startsWith("wb_")) throw new Error("创建工作台工作区失败：未返回有效 ID");
    $("session-id").value = sid;
    localStorage.setItem("workbench_session_id", sid);
    return sid;
  }

  async function ensureSession(forceNew) {
    const input = $("session-id");
    let sid = (input.value || "").trim();
    if (!forceNew && sid.startsWith("wb_")) return sid;
    return createSession();
  }

  function renderEval(ev) {
    const box = $("eval-box");
    if (!box) return;
    box.classList.remove("hidden");
    if (!ev) {
      box.innerHTML = '<p class="hint">分析完成后显示评价（新颖性 / 合理性 / KG 建议）。</p>';
      return;
    }
    const score = Math.round((ev.completeness_score || 0) * 100);
    const kg = ev.knowledge_graph || {};
    const flags = (ev.flags || []).map((f) => "• " + f).join("<br/>");
    box.innerHTML =
      `<div class="metrics-grid">` +
      `<div class="metric-card"><div class="val">${score}%</div><div class="lbl">完整度</div></div>` +
      `<div class="metric-card"><div class="val">${ev.step_count || "—"}</div><div class="lbl">算子步数</div></div>` +
      `<div class="metric-card"><div class="val">${score >= 60 ? "通过" : "复核"}</div><div class="lbl">评价</div></div>` +
      `</div>` +
      `<div class="eval-text">` +
      `<div><span class="lbl">合理性</span><br/>${ev.reasonableness || "—"}</div><br/>` +
      `<div><span class="lbl">新颖性</span><br/>${ev.novelty_note || "—"}</div><br/>` +
      (flags ? `<div><span class="lbl">建议与标记</span><br/>${flags}</div><br/>` : "") +
      `<div><span class="lbl">知识图谱</span><br/>${kg.status || kg.provider || "—"}` +
      (kg.novelty_evidence && kg.novelty_evidence.length
        ? " · 证据 " + kg.novelty_evidence.length + " 条"
        : "") +
      `</div></div>`;
  }

  function renderSteps(steps) {
    const ul = $("step-list");
    ul.innerHTML = "";
    (steps || []).forEach((s, i) => {
      const li = document.createElement("li");
      const st = s.status || "";
      const cls = st === "ok" ? "step-ok" : st === "error" ? "step-err" : "";
      li.className = cls;
      const idx = s.index != null ? s.index : i;
      const kept = s.kept ? " ·kept" : "";
      li.textContent = `#${idx} [${st}${kept}] ${s.solver || s.name || ""}`;
      li.title = "点击填入续跑步骤号 #" + idx;
      li.style.cursor = "pointer";
      li.addEventListener("click", () => {
        $("resume-step").value = String(idx);
        const rm = $("resume-msg");
        if (rm) rm.textContent = "已选步骤 #" + idx + "，点「断点续跑」从该步继续";
      });
      ul.appendChild(li);
    });
  }

  function renderCharts(charts, run_id) {
    const grid = $("charts-grid");
    grid.innerHTML = "";
    const hint = $("charts-count-hint");
    if (!charts || !charts.length) {
      if (hint) hint.textContent = "暂无图表";
      grid.innerHTML = '<p class="hint">暂无图表 — 勾选「同时自动出图」后开始分析，或点「全部可画图种出图」</p>';
      return;
    }
    if (hint) {
      hint.textContent =
        "已生成 " + charts.length + " 张图（不是图种数；可选图种见左侧，共 " +
        (chartTypes.length || 22) + " 种）· 可单张/打包下载";
    }
    charts.forEach(async (ch, i) => {
      const fig = document.createElement("figure");
      fig.className = "chart-item";
      const img = document.createElement("img");
      img.alt = ch.title || "chart";
      const fn = ch.filename || (ch.path && ch.path.split(/[/\\]/).pop()) || "chart_" + i + ".png";
      let objectUrl = "";
      if (ch.base64) {
        img.src = "data:image/png;base64," + ch.base64;
      } else if (run_id) {
        try {
          const r = await fetch(`${API}/workbench/runs/${run_id}/chart/${encodeURIComponent(fn)}`, {
            headers: authHeaders(false),
          });
          if (r.ok) {
            const blob = await r.blob();
            objectUrl = URL.createObjectURL(blob);
            img.src = objectUrl;
          }
        } catch (e) {
          /* skip */
        }
      }
      const cap = document.createElement("figcaption");
      cap.textContent = (ch.title || ch.chart_type || "统计图") + (ch.chart_type ? " · " + ch.chart_type : "");
      const actions = document.createElement("div");
      actions.className = "chart-actions";
      const a = document.createElement("a");
      a.textContent = "下载 PNG";
      a.href = "#";
      a.addEventListener("click", async (ev) => {
        ev.preventDefault();
        try {
          if (objectUrl) {
            const link = document.createElement("a");
            link.href = objectUrl;
            link.download = fn;
            link.click();
            return;
          }
          const r = await fetch(
            `${API}/workbench/runs/${run_id}/chart/${encodeURIComponent(fn)}?download=1`,
            { headers: authHeaders(false) }
          );
          if (!r.ok) throw new Error("下载失败");
          const blob = await r.blob();
          const link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = fn;
          link.click();
        } catch (e) {
          alert(e.message || "下载失败");
        }
      });
      actions.appendChild(a);
      const analysis = document.createElement("div");
      analysis.className = "chart-analysis";
      const src = ch.analysis_source === "llm" ? "大模型解读" : "图表解读";
      analysis.innerHTML =
        "<div class='chart-analysis-label'>" + src + "</div>" +
        "<p>" + (ch.analysis || "（生成解读中或暂无）") + "</p>";
      fig.appendChild(img);
      fig.appendChild(cap);
      fig.appendChild(analysis);
      fig.appendChild(actions);
      if (!img.src && !ch.base64) {
        cap.textContent = (ch.title || "统计图") + "（加载失败）";
      }
      grid.appendChild(fig);
    });
  }

  function fillSelect(sel, names, includeEmpty) {
    const cur = sel.value;
    sel.innerHTML = includeEmpty ? '<option value="">—</option>' : "";
    (names || []).forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n;
      opt.textContent = n;
      sel.appendChild(opt);
    });
    if (cur && names && names.indexOf(cur) >= 0) sel.value = cur;
  }

  function selectionBarHtml() {
    const cols = Array.from(selectedCols);
    const types = Array.from(preferredTypes);
    return (
      `<div class="sel-bar">已选列 ${cols.length ? cols.join(", ") : "无"}` +
      ` · 图种 ${types.length ? types.join(", ") : "由大模型推断"}</div>`
    );
  }

  function syncMultiSelectFromChips() {
    const colsSel = $("chart-cols");
    if (!colsSel) return;
    Array.from(colsSel.options).forEach((opt) => {
      opt.selected = selectedCols.has(opt.value);
    });
  }

  function renderColumnChips(profile) {
    const box = $("columns-box");
    if (!profile || !profile.columns) {
      box.textContent = "暂无列画像";
      return;
    }
    const names = profile.columns.map((c) => c.name);
    selectedCols = new Set(Array.from(selectedCols).filter((n) => names.indexOf(n) >= 0));
    const chips = profile.columns
      .map((c) => {
        const cls =
          (c.is_numeric ? "num" : c.role_hint === "categorical" ? "cat" : "") +
          (selectedCols.has(c.name) ? " selected" : "");
        return (
          `<span class="col-chip ${cls}" data-col="${c.name}" ` +
          `title="点击选中 · ${c.dtype} · nunique=${c.nunique} · miss=${c.missing_rate}">` +
          `${c.name} · ${c.role_hint}</span>`
        );
      })
      .join("");
    box.innerHTML =
      `<div class="hint">${profile.rows} 行 × ${profile.columns.length} 列</div>` +
      selectionBarHtml() +
      `<div class="col-chips" id="col-chips">${chips}</div>`;
    box.querySelectorAll(".col-chip[data-col]").forEach((el) => {
      el.addEventListener("click", () => {
        const name = el.getAttribute("data-col");
        if (selectedCols.has(name)) selectedCols.delete(name);
        else selectedCols.add(name);
        el.classList.toggle("selected", selectedCols.has(name));
        const bar = box.querySelector(".sel-bar");
        if (bar) bar.outerHTML = selectionBarHtml();
        syncMultiSelectFromChips();
      });
    });
    fillSelect($("chart-x"), names, true);
    fillSelect($("chart-y"), names, true);
    fillSelect($("chart-hue"), names, true);
    fillSelect($("chart-cols"), names, false);
    syncMultiSelectFromChips();
  }

  function renderTypeChips() {
    const box = $("type-chips");
    if (!box) return;
    const popular = [
      "violin", "box", "bar", "scatter", "histogram", "correlation_heatmap",
      "missing_heatmap", "pie", "kde", "pca_scatter", "ridge", "km_curve",
    ];
    const byType = {};
    chartTypes.forEach((t) => { byType[t.type] = t; });
    const order = popular.filter((t) => byType[t]).concat(
      chartTypes.map((t) => t.type).filter((t) => popular.indexOf(t) < 0)
    );
    box.innerHTML = order
      .map((tid) => {
        const t = byType[tid] || { type: tid, label: tid };
        const sel = preferredTypes.has(tid) ? " selected" : "";
        return (
          `<span class="col-chip${sel}" data-type="${tid}" title="${t.type}">` +
          `${t.label || t.type}</span>`
        );
      })
      .join("");
    box.querySelectorAll(".col-chip[data-type]").forEach((el) => {
      el.addEventListener("click", () => {
        const tid = el.getAttribute("data-type");
        if (preferredTypes.has(tid)) preferredTypes.delete(tid);
        else preferredTypes.add(tid);
        el.classList.toggle("selected", preferredTypes.has(tid));
        if ($("columns-box") && columnProfile) {
          const bar = $("columns-box").querySelector(".sel-bar");
          if (bar) bar.outerHTML = selectionBarHtml();
        }
        if ($("chart-type")) $("chart-type").value = tid;
      });
    });
  }

  function renderChartQueue() {
    const ul = $("chart-queue");
    ul.innerHTML = "";
    if (!chartQueue.length) {
      ul.innerHTML = '<li class="hint">队列为空 — 点选列/图种后用「大模型出图」</li>';
      return;
    }
    chartQueue.forEach((spec, idx) => {
      const li = document.createElement("li");
      const cols = (spec.cols || []).join(",");
      const desc = [
        spec.type,
        spec.x ? "x=" + spec.x : "",
        spec.y ? "y=" + spec.y : "",
        spec.hue ? "hue=" + spec.hue : "",
        cols ? "cols=" + cols : "",
        spec.title || "",
      ]
        .filter(Boolean)
        .join(" · ");
      const span = document.createElement("span");
      span.textContent = desc;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "删除";
      btn.addEventListener("click", () => {
        chartQueue.splice(idx, 1);
        renderChartQueue();
      });
      li.appendChild(span);
      li.appendChild(btn);
      ul.appendChild(li);
    });
  }

  async function loadChartTypes() {
    try {
      const d = await api("GET", "/workbench/chart-types");
      chartTypes = d.chart_types || [];
      const tot = $("type-total");
      if (tot) tot.textContent = String(chartTypes.length);
      const hint = $("type-count-hint");
      if (hint) hint.textContent = "共 " + chartTypes.length + " 种图可选";
      const sel = $("chart-type");
      if (sel) {
        sel.innerHTML = "";
        chartTypes.forEach((t) => {
          const opt = document.createElement("option");
          opt.value = t.type;
          opt.textContent = (t.label || t.type) + " (" + t.type + ")";
          sel.appendChild(opt);
        });
      }
      renderTypeChips();
    } catch (e) {
      console.warn("chart-types", e);
      const tot = $("type-total");
      if (tot) tot.textContent = "加载失败";
    }
  }

  async function parseChartsFromUI() {
    const sid = await ensureSession();
    const text = ($("chart-nl").value || "").trim();
    const selected = Array.from(selectedCols);
    const preferred = Array.from(preferredTypes);
    if (!text && !selected.length && !preferred.length) {
      throw new Error("请先点选列/图种，或填写出图描述");
    }
    return api("POST", "/workbench/charts/parse", {
      session_id: sid,
      text: text || "根据选中的列与图种生成合适的统计图",
      selected_columns: selected,
      preferred_types: preferred,
    });
  }

  async function renderQueueCharts() {
    const sid = await ensureSession();
    const d = await api("POST", "/workbench/charts/render", {
      session_id: sid,
      charts: chartQueue.slice(),
      run_id: runId || undefined,
    });
    runId = d.run_id || runId;
    renderCharts(d.charts || [], runId);
    const errN = (d.errors || []).length;
    $("chart-msg").textContent =
      "完成 " + (d.count || 0) + " 张" + (errN ? "，失败 " + errN + " 项" : "") +
      " · run_id=" + (d.run_id || "");
    if (errN) console.warn("chart errors", d.errors);
    return d;
  }

  async function loadColumns() {
    if (!token) {
      $("chart-msg").textContent = "请先登录";
      $("columns-box").innerHTML = '<p class="hint">请先点击「验收登录」</p>';
      return;
    }
    try {
      const sid = await ensureSession();
      $("chart-msg").textContent = "加载列画像…";
      $("columns-box").innerHTML = '<p class="hint">正在读取会话数据…</p>';
      columnProfile = await api("GET", "/workbench/columns?session_id=" + encodeURIComponent(sid));
      renderColumnChips(columnProfile);
      const n = (columnProfile.columns || []).length;
      $("chart-msg").textContent = "列画像已更新（" + n + " 列）";
    } catch (e) {
      $("chart-msg").textContent = e.message;
      $("columns-box").innerHTML =
        '<p class="hint">列画像失败：' + e.message +
        '。请确认已上传 CSV，且当前会话 ID 正确后重试「刷新列画像」。</p>';
    }
  }

  function currentFormSpec() {
    const type = $("chart-type").value;
    const x = $("chart-x").value || null;
    const y = $("chart-y").value || null;
    const hue = $("chart-hue").value || null;
    const colsSel = $("chart-cols");
    const cols = Array.from(colsSel.selectedOptions).map((o) => o.value);
    const title = ($("chart-title").value || "").trim();
    const bins = parseInt($("chart-bins").value, 10);
    const spec = { type, x, y, hue, cols: cols.length ? cols : null, title: title || null, params: {} };
    if (type === "histogram" && !isNaN(bins)) spec.params.bins = bins;
    return spec;
  }

  function renderMatrix(containerId, matrix, labels) {
    const box = $(containerId);
    if (!matrix || !matrix.length) {
      box.innerHTML = '<p class="hint">无数据</p>';
      return;
    }
    let html = "<table class=\"data\"><thead><tr><th></th>";
    labels.forEach((l) => {
      html += `<th>${l}</th>`;
    });
    html += "</tr></thead><tbody>";
    matrix.forEach((row, i) => {
      html += `<tr><th>${labels[i]}</th>`;
      row.forEach((v) => {
        const n = parseFloat(v);
        let cls = "";
        if (!isNaN(n)) {
          if (n > 0.3) cls = "heat-pos";
          else if (n < -0.3) cls = "heat-neg";
        }
        html += `<td class="${cls}">${isNaN(n) ? v : n.toFixed(3)}</td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    box.innerHTML = html;
  }

  function renderTable(containerId, rows, columns) {
    const box = $(containerId);
    if (!rows || !rows.length) {
      box.innerHTML = '<p class="hint">无数据</p>';
      return;
    }
    const cols = columns || Object.keys(rows[0]);
    let html = "<table class=\"data\"><thead><tr>";
    cols.forEach((c) => {
      html += `<th>${c}</th>`;
    });
    html += "</tr></thead><tbody>";
    rows.slice(0, 40).forEach((row) => {
      html += "<tr>";
      cols.forEach((c) => {
        const v = row[c];
        html += `<td>${v == null ? "" : v}</td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    box.innerHTML = html;
  }

  async function loadArtifacts(run_id) {
    try {
      const art = await api("GET", `/workbench/runs/${run_id}/artifacts`);
      if (art.describe && art.describe.rows && art.describe.rows.length) {
        renderTable("table-describe", art.describe.rows, art.describe.columns);
      }
      if (art.correlation && art.correlation.matrix) {
        renderMatrix("table-corr", art.correlation.matrix, art.correlation.labels);
      }
    } catch (e) {
      console.warn("artifacts", e);
    }
  }

  async function refreshDetail() {
    if (!runId) return;
    const d = await api("GET", `/workbench/runs/${runId}`);
    $("summary-box").innerHTML = md(d.summary || (d.status === "completed" ? "摘要为空，请查看评价与图表。" : "分析进行中…"));
    renderEval(d.evaluation);
    renderSteps(d.steps);
    renderCharts(d.charts, runId);
    await loadArtifacts(runId);
    $("prog-status").textContent = d.status || "—";
    $("prog-stage").textContent = d.current_stage || "—";
    $("prog-steps").textContent = d.step_count || 0;
  }

  function startPoll() {
    stopPoll();
    pollTimer = setInterval(async () => {
      try {
        const p = await api("GET", `/workbench/progress?run_id=${encodeURIComponent(runId)}`);
        $("prog-status").textContent = p.status || "—";
        $("prog-stage").textContent = p.current_stage || "—";
        $("prog-steps").textContent = p.step_count || 0;
        renderSteps(p.steps);
        if (["completed", "error", "cancelled"].includes(p.status)) {
          stopPoll();
          await refreshDetail();
        }
      } catch (e) {
        console.warn(e);
      }
    }, 1500);
  }

  function stopPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  $("btn-login").addEventListener("click", async () => {
    try {
      $("login-status").textContent = "登录中…";
      const d = await api("POST", "/auth/login-with-sms", { phone: "13800000000", code: "888888" });
      token = d.access_token || "";
      if (!token) throw new Error("登录成功但未返回 access_token");
      localStorage.setItem("agent_access_token", token);
      const sid = await ensureSession();
      $("login-status").textContent = "已登录 · 会话 " + sid.slice(0, 8) + "…";
      $("status-badges").innerHTML = '<span class="badge ok">API 已连接</span>';
    } catch (e) {
      $("login-status").textContent = "登录失败: " + e.message;
    }
  });

  $("btn-upload").addEventListener("click", async () => {
    const f = $("file-input").files[0];
    if (!f) {
      $("upload-msg").textContent = "请选择文件";
      return;
    }
    if (!token) {
      $("upload-msg").textContent = "请先点击「验收登录」";
      return;
    }
    $("upload-msg").textContent = "上传中…";
    try {
      const sid = await ensureSession();
      const fd = new FormData();
      fd.append("file", f);
      fd.append("session_id", sid);
      const r = await fetch(API + "/workbench/session/upload", {
        method: "POST",
        headers: token ? { Authorization: "Bearer " + token } : {},
        body: fd,
      });
      let j = await r.json().catch(() => ({}));
      if (!r.ok && (r.status === 400 || r.status === 404)) {
        const sid2 = await createSession();
        const fd2 = new FormData();
        fd2.append("file", f);
        fd2.append("session_id", sid2);
        const r2 = await fetch(API + "/workbench/session/upload", {
          method: "POST",
          headers: token ? { Authorization: "Bearer " + token } : {},
          body: fd2,
        });
        j = await r2.json().catch(() => ({}));
        if (!r2.ok) {
          throw new Error(formatApiError(j.detail != null ? j.detail : j, "上传失败"));
        }
      } else if (!r.ok) {
        throw new Error(formatApiError(j.detail != null ? j.detail : j, "上传失败"));
      }
      const data = j.data || j;
      const saved = data.relative_path || j.relative_path || f.name;
      const orig = data.original_filename || j.original_filename || f.name;
      const renamed = data.renamed || j.renamed;
      const label = renamed && saved !== orig ? (orig + " → " + saved) : (orig || saved);
      $("upload-msg").textContent = "上传成功: " + label + "（会话已就绪，正在刷新列画像…）";
      try {
        await loadColumns();
        $("upload-msg").textContent = "上传成功: " + label + " · 列画像已就绪";
      } catch (e2) {
        $("upload-msg").textContent = "上传成功，但列画像失败: " + e2.message;
      }
    } catch (e) {
      $("upload-msg").textContent = e.message;
    }
  });

  $("btn-suggest").addEventListener("click", async () => {
    const box = $("suggestions-box");
    box.classList.remove("hidden");
    box.innerHTML = "<p class='hint'>加载建议…</p>";
    if (!token) {
      box.innerHTML = "<p class='hint'>请先点击「验收登录」</p>";
      return;
    }
    try {
      const sid = await ensureSession();
      const d = await api("POST", "/workbench/suggest", {
        session_id: sid,
        task: $("task-input").value.trim(),
      });
      box.innerHTML = `<p class="hint">${d.rows} 行 × ${(d.columns || []).length} 列</p>`;
      (d.suggestions || []).forEach((s) => {
        const div = document.createElement("div");
        div.className = "card";
        div.innerHTML = `<strong>${s.title || "建议"}</strong><span>${s.reason || ""}</span>`;
        box.appendChild(div);
      });
      if (!(d.suggestions || []).length) {
        box.innerHTML += "<p class='hint'>暂无建议，可直接开始分析</p>";
      }
    } catch (e) {
      box.innerHTML = "<p class='hint'>" + e.message + "</p>";
    }
  });

  $("btn-run").addEventListener("click", async () => {
    if (!token) {
      alert("请先点击「验收登录」");
      return;
    }
    try {
      const sid = await ensureSession();
      $("summary-box").innerHTML = "<p class='hint'>正在提交分析任务…</p>";
      const body = {
        session_id: sid,
        task: $("task-input").value.trim(),
        auto_charts: !!($("chk-auto-charts") && $("chk-auto-charts").checked),
      };
      if (chartQueue.length) body.chart_specs = chartQueue.slice();
      const d = await api("POST", "/workbench/run", body);
      runId = d.run_id;
      if (!runId) throw new Error("未返回 run_id");
      $("summary-box").innerHTML =
        "<p class='hint'>分析已启动，run_id=" +
        runId +
        (chartQueue.length ? " · 用户出图 " + chartQueue.length + " 张" : "") +
        (body.auto_charts ? " · 自动出图开" : "") +
        "</p>";
      $("prog-status").textContent = "running";
      startPoll();
    } catch (e) {
      alert(e.message);
      $("summary-box").innerHTML = "<p class='hint'>" + e.message + "</p>";
    }
  });

  $("btn-load-columns").addEventListener("click", () => loadColumns());

  $("btn-add-chart").addEventListener("click", () => {
    const spec = currentFormSpec();
    if (!spec.type) {
      $("chart-msg").textContent = "请选择图种";
      return;
    }
    chartQueue.push(spec);
    renderChartQueue();
    $("chart-msg").textContent = "已加入队列（共 " + chartQueue.length + "）";
  });

  function formatChartParseMsg(d, specs) {
    const parts = [];
    if (d && d.message) parts.push(String(d.message));
    const uns = (d && d.unsupported) || [];
    if (uns.length && !(d && d.message)) {
      parts.push(
        "不支持：" +
          uns.map((u) => (u.name || "") + "（" + (u.reason || "不支持") + "）").join("；")
      );
    }
    if (specs && specs.length) {
      parts.push("已规划 " + specs.length + " 张支持的图（" + ((d && d.source) || "parse") + "）");
    } else if (uns.length) {
      parts.push("未生成替代图（不会把玫瑰图等改成饼图）");
    } else {
      parts.push("未能解析出图规格，请改选列/图种或改描述");
    }
    return parts.join(" ");
  }

  $("btn-parse-charts").addEventListener("click", async () => {
    if (!token) {
      $("chart-msg").textContent = "请先登录";
      return;
    }
    try {
      $("chart-msg").textContent = "大模型解析中…";
      const d = await parseChartsFromUI();
      const specs = d.charts || [];
      const uns = d.unsupported || [];
      if (specs.length) {
        chartQueue = chartQueue.concat(specs);
        renderChartQueue();
      }
      $("chart-msg").textContent = formatChartParseMsg(d, specs);
      if (!specs.length && uns.length) {
        // keep message focused on unsupported
      }
    } catch (e) {
      $("chart-msg").textContent = e.message;
    }
  });

  $("btn-llm-charts").addEventListener("click", async () => {
    if (!token) {
      $("chart-msg").textContent = "请先登录";
      return;
    }
    try {
      $("chart-msg").textContent = "大模型规划并出图中…";
      const d = await parseChartsFromUI();
      const specs = d.charts || [];
      const uns = d.unsupported || [];
      $("chart-msg").textContent = formatChartParseMsg(d, specs);
      if (!specs.length) {
        return;
      }
      chartQueue = specs.slice();
      renderChartQueue();
      $("chart-msg").textContent =
        formatChartParseMsg(d, specs) + (uns.length ? "" : "") + "，正在生成…";
      await renderQueueCharts();
    } catch (e) {
      $("chart-msg").textContent = e.message;
    }
  });

  $("btn-clear-sel").addEventListener("click", () => {
    selectedCols.clear();
    preferredTypes.clear();
    if (columnProfile) renderColumnChips(columnProfile);
    renderTypeChips();
    $("chart-msg").textContent = "已清空列与图种选择";
  });

  $("btn-render-charts").addEventListener("click", async () => {
    if (!token) {
      $("chart-msg").textContent = "请先登录";
      return;
    }
    if (!chartQueue.length) {
      $("chart-msg").textContent = "队列为空 — 可先点「大模型出图」";
      return;
    }
    try {
      $("chart-msg").textContent = "正在按规格出图…";
      await renderQueueCharts();
    } catch (e) {
      $("chart-msg").textContent = e.message;
    }
  });

  $("btn-cancel").addEventListener("click", async () => {
    if (!runId) return;
    try {
      await api("POST", "/workbench/cancel", { run_id: runId });
      stopPoll();
      $("prog-status").textContent = "cancelled";
    } catch (e) {
      alert(e.message);
    }
  });

  $("btn-resume").addEventListener("click", async () => {
    const rm = $("resume-msg");
    if (!token) { alert("请先登录"); return; }
    if (!runId) {
      alert("请先点「开始分析」完成一次运行，再在步骤列表点击要续跑的步骤号");
      if (rm) rm.textContent = "还没有 run_id：请先开始分析";
      return;
    }
    const fromStep = parseInt($("resume-step").value, 10);
    if (isNaN(fromStep) || fromStep < 0) { alert("续跑步骤号无效"); return; }
    try {
      if (rm) rm.textContent = "正在从 #" + fromStep + " 续跑…";
      $("summary-box").innerHTML = `<p class='hint'>从步骤 #${fromStep} 断点续跑…</p>`;
      const d = await api("POST", "/workbench/resume", {
        run_id: runId,
        from_step: fromStep,
        task: $("task-input").value.trim(),
      });
      runId = d.run_id;
      if (rm) rm.textContent = "续跑已启动 · 新 run=" + runId + "（保留 #0–#" + Math.max(0, fromStep - 1) + "）";
      $("summary-box").innerHTML = `<p class='hint'>续跑已启动，新 run_id=${runId}（保留 #0–#${Math.max(0, fromStep - 1)}）</p>`;
      startPoll();
    } catch (e) {
      if (rm) rm.textContent = "续跑失败: " + e.message;
      alert(e.message);
    }
  });

  $("btn-export").addEventListener("click", async () => {
    if (!token || !runId) { alert("请先登录并完成分析"); return; }
    try {
      const sid = await ensureSession();
      const d = await api("POST", "/workbench/export", {
        session_id: sid,
        run_id: runId,
        kind: "bundle",
      });
      alert("已登记导出: " + (d.export_id || "") + "\n" + (d.bundle_dir || d.artifact_path || ""));
    } catch (e) {
      alert(e.message);
    }
  });

  $("btn-exports").addEventListener("click", async () => {
    const box = $("exports-box");
    box.classList.remove("hidden");
    box.innerHTML = "<p class='hint'>加载台账…</p>";
    try {
      const sid = await ensureSession();
      const d = await api("GET", `/workbench/exports?session_id=${encodeURIComponent(sid)}`);
      box.innerHTML = `<p class="hint">导出台账 ${d.count || 0} 条</p>`;
      (d.exports || []).slice(0, 30).forEach((ex) => {
        const div = document.createElement("div");
        div.className = "card";
        div.innerHTML = `<strong>${ex.kind}</strong><span>${ex.created_at || ""} · ${ex.run_id || ""}</span><span class="hint">${ex.artifact_path || ex.note || ""}</span>`;
        box.appendChild(div);
      });
    } catch (e) {
      box.innerHTML = "<p class='hint'>" + e.message + "</p>";
    }
  });

  const cachedSid = localStorage.getItem("workbench_session_id");
  if (cachedSid) $("session-id").value = cachedSid;
  else $("session-id").value = "";
  $("session-id").placeholder = "登录后自动创建";

  if (token) {
    $("login-status").textContent = "已缓存 token（可点验收登录刷新会话）";
    $("status-badges").innerHTML = '<span class="badge ok">API token 就绪</span>';
  }


  if ($("btn-select-all-types")) {
    $("btn-select-all-types").addEventListener("click", () => {
      preferredTypes = new Set(chartTypes.map((t) => t.type));
      renderTypeChips();
      $("chart-msg").textContent = "已全选 " + preferredTypes.size + " 种图";
    });
  }

  if ($("btn-all-charts")) {
    $("btn-all-charts").addEventListener("click", async () => {
      if (!token) {
        $("chart-msg").textContent = "请先登录并上传数据";
        return;
      }
      try {
        $("chart-msg").textContent = "正在按数据可行性编排全部图种…";
        const sid = await ensureSession();
        const preferred = Array.from(preferredTypes);
        const selected = Array.from(selectedCols);
        const d = await api("POST", "/workbench/charts/plan-all", {
          session_id: sid,
          preferred_types: preferred.length ? preferred : undefined,
          selected_columns: selected.length ? selected : undefined,
        });
        const specs = d.charts || [];
        const skipped = d.skipped || [];
        if (!specs.length) {
          $("chart-msg").textContent =
            "当前数据无法自动编排图种" +
            (skipped.length ? "（跳过 " + skipped.length + "）" : "");
          return;
        }
        chartQueue = specs.slice();
        renderChartQueue();
        $("chart-msg").textContent =
          "已编排 " + specs.length + " 张，跳过 " + skipped.length + "，正在生成…";
        await renderQueueCharts();
        if (skipped.length) {
          console.info("skipped charts", skipped);
          $("chart-msg").textContent +=
            " · 跳过: " + skipped.slice(0, 6).map((s) => s.type).join(", ");
        }
      } catch (e) {
        $("chart-msg").textContent = e.message;
      }
    });
  }

  if ($("btn-download-zip")) {
    $("btn-download-zip").addEventListener("click", async () => {
      if (!token || !runId) {
        alert("请先完成分析或出图，生成 run_id 后再打包下载");
        return;
      }
      try {
        const r = await fetch(`${API}/workbench/runs/${encodeURIComponent(runId)}/charts.zip`, {
          headers: authHeaders(false),
        });
        if (!r.ok) {
          const t = await r.text();
          throw new Error(t || "打包下载失败");
        }
        const blob = await r.blob();
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = runId + "_charts.zip";
        link.click();
      } catch (e) {
        alert(e.message || "打包下载失败");
      }
    });
  }

  async function loadSolverHelp() {
    const how = $("solver-how");
    const box = $("solver-list");
    if (!how || !box) return;
    if (!token) {
      how.textContent = "登录后可加载可用分析能力说明。在任务里用中文写步骤与顺序即可。";
      return;
    }
    try {
      const d = await api("GET", "/workbench/solvers");
      how.textContent =
        d.how_to ||
        "在「分析任务」里用自然语言写清要用的分析及顺序（如：先描述统计，再相关，再 t 检验），大模型会按你的顺序编排。";
      const solvers = d.solvers || [];
      if (!solvers.length) {
        box.innerHTML = "<p class='hint'>暂无目录</p>";
        return;
      }
      const ul = document.createElement("ul");
      ul.className = "solver-ul";
      solvers.forEach((s) => {
        const li = document.createElement("li");
        const zh = s.zh_name || s.label || s.desc || s.id || "";
        const desc = s.desc || s.label || "";
        li.innerHTML =
          "<strong class='solver-zh'>" +
          zh +
          "</strong> <span class='solver-desc'>" +
          (desc && desc !== zh ? desc : "") +
          "</span>";
        li.title = "点击将「" + zh + "」写入分析任务";
        li.addEventListener("click", () => {
          const ta = $("task-input");
          if (!ta) return;
          const phrase = zh;
          const cur = (ta.value || "").trim();
          ta.value = cur
            ? cur.replace(/[，,]\s*$/, "") + "，然后做" + phrase
            : "请按顺序：" + phrase;
          ta.focus();
        });
        ul.appendChild(li);
      });
      box.innerHTML = "";
      box.appendChild(ul);
      const tip = document.createElement("p");
      tip.className = "hint";
      tip.textContent =
        "共 " +
        solvers.length +
        " 项。点击可把中文名称写入任务；写清「先…再…最后…」时，大模型会按该顺序排链。";
      box.appendChild(tip);
    } catch (e) {
      how.textContent = "说明加载失败：" + (e.message || e);
    }
  }

  renderChartQueue();
  loadChartTypes().catch(() => {});
  loadSolverHelp().catch(() => {});

  // reload solver help after login
  const _loginBtn = $("btn-login");
  if (_loginBtn) {
    _loginBtn.addEventListener("click", () => {
      setTimeout(() => loadSolverHelp().catch(() => {}), 800);
    });
  }
})();
