(function () {
  "use strict";

  const meta = document.querySelector('meta[name="agent-api-base"]');
  const API = meta ? String(meta.content || "") : "";
  let token = sessionStorage.getItem("dq213_access_token") || localStorage.getItem("agent_access_token") || "";
  let reportId = "";

  const $ = (id) => document.getElementById(id);
  const logEl = $("log");

  function log(message, detail) {
    const safeDetail = detail === undefined ? "" : `\n${JSON.stringify(detail, null, 2)}`;
    const line = `${String(message)}${safeDetail}`;
    logEl.textContent = (logEl.textContent === "日志…" ? "" : `${logEl.textContent}\n\n`) + line;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function formatApiError(payload, fallback) {
    if (payload == null || payload === "") return fallback || "请求失败";
    if (typeof payload === "string") return payload;
    if (typeof payload === "object") {
      if (payload.detail != null) return formatApiError(payload.detail, fallback);
      if (payload.msg) return String(payload.msg);
      try { return JSON.stringify(payload); } catch (_error) { return fallback || "请求失败"; }
    }
    return String(payload);
  }

  async function apiResponse(method, path, body) {
    const headers = {};
    if (body != null) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    return fetch(API + path, {
      method,
      headers,
      body: body != null ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
      cache: "no-store",
    });
  }

  async function api(method, path, body) {
    const response = await apiResponse(method, path, body);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, response.statusText));
    return payload.data != null ? payload.data : payload;
  }

  document.querySelectorAll(".tabs [data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tabs [data-tab]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      ["qc", "phi", "timeline"].forEach((tab) => {
        const panel = $(`tab-${tab}`);
        panel.classList.toggle("hidden", tab !== button.dataset.tab);
      });
    });
  });

  $("btn-login").addEventListener("click", async () => {
    try {
      const result = await api("POST", "/auth/login-with-sms", {
        phone: $("login-phone").value.trim(),
        code: $("login-code").value.trim(),
      });
      token = result.access_token || result.token || "";
      if (!token) throw new Error("登录响应缺少 token");
      sessionStorage.setItem("dq213_access_token", token);
      $("login-status").textContent = "已登录";
      $("login-status").className = "status ok";
      log("登录成功");
      await loadPatients();
    } catch (error) {
      log(`登录失败: ${error.message}`);
    }
  });

  function pct(value) {
    if (value == null || Number.isNaN(Number(value))) return "—";
    return `${(Number(value) * 100).toFixed(1)}%`;
  }

  function renderBars(container, items, valueKey, labelKey) {
    container.replaceChildren();
    (items || []).forEach((item) => {
      const value = Number(item[valueKey] || 0);
      const row = document.createElement("div");
      row.className = "bar-row";
      const label = document.createElement("div");
      label.textContent = String(item[labelKey] || "");
      const track = document.createElement("div");
      track.className = "bar-track";
      const fill = document.createElement("div");
      fill.className = "bar-fill";
      fill.style.width = `${Math.max(0, Math.min(100, value))}%`;
      track.appendChild(fill);
      const number = document.createElement("div");
      number.textContent = Number.isFinite(value) ? value.toFixed(1) : "—";
      row.append(label, track, number);
      container.appendChild(row);
    });
  }

  function strictQcPayload() {
    const rows = [];
    for (let index = 0; index < 12; index += 1) {
      rows.push({
        patient_id: index === 11 ? "DQ-BAD ID" : `DQ-${String(index + 1).padStart(3, "0")}`,
        age: index === 10 ? 180 : 22 + index,
        gender: index === 9 ? "未知值" : (index % 2 ? "女" : "男"),
        diagnosis: index === 8 ? "x" : "抑郁障碍",
        admission_date: "2026-01-03",
        discharge_date: index === 7 ? "2025-12-31" : "2026-01-18",
        HAMD_total: index === 10 ? 99 : 8 + index,
        HAMA_total: index === 6 ? null : 6 + index,
        PHQ9_total: index === 5 ? "not-number" : 5 + index,
        disease_duration_years: 1 + index / 2,
        relapse: index === 4 ? 3 : 0,
      });
    }
    rows[3].patient_id = rows[2].patient_id;
    return {
      rows,
      unstructured_rows: [
        { content: "患者情绪较前改善，睡眠恢复，继续当前治疗方案。" },
        { content: "短" },
        { content: "包含乱码\u0001的病历文本" },
        { content: "患者情绪较前改善，睡眠恢复，继续当前治疗方案。" },
      ],
      multimodal_items: [
        { asset_id: "IMG-1", modality: "image", mime_type: "image/png", uri: "/safe/image.png", size_bytes: 2048, checksum: "a".repeat(64) },
        { asset_id: "DOC-1", modality: "pdf", mime_type: "application/pdf", uri: "/safe/report.pdf", size_bytes: 4096, checksum: "b".repeat(64) },
        { asset_id: "BAD-1", modality: "image", mime_type: "text/plain", uri: "", size_bytes: -1 },
      ],
      export: true,
    };
  }

  $("btn-qc").addEventListener("click", async () => {
    try {
      const body = $("qc-source").value === "inline" ? strictQcPayload() : { export: true };
      const result = await api("POST", "/dq213/qc/assess", body);
      if (!result.ok) throw new Error(result.error || "质控失败");
      $("m-health").textContent = `${result.health_score} (${result.health_label})`;
      $("m-miss").textContent = pct(result.core_metrics && result.core_metrics.missing_rate);
      $("m-anom").textContent = pct(result.core_metrics && result.core_metrics.field_anomaly_rate);
      $("m-out").textContent = pct(result.core_metrics && result.core_metrics.outlier_rate);
      $("m-text").textContent = pct(result.core_metrics && result.core_metrics.unstructured_issue_rate);
      $("m-media").textContent = pct(result.core_metrics && result.core_metrics.multimodal_issue_rate);
      const dimensions = result.dimensions || {};
      const dimensionItems = Object.keys(dimensions)
        .filter((key) => dimensions[key].score != null)
        .map((key) => ({ name: key, score: Number(dimensions[key].score) }));
      renderBars($("dim-bars"), dimensionItems, "score", "name");
      const fieldItems = (result.field_anomaly_rates || []).slice(0, 12).map((item) => ({
        name: item.field,
        score: Number(item.anomaly_rate || 0) * 100,
      }));
      renderBars($("field-bars"), fieldItems, "score", "name");
      reportId = result.report_id || "";
      $("btn-report").classList.toggle("hidden", !reportId);
      log("质控完成", { health_score: result.health_score, core_metrics: result.core_metrics, report_id: reportId });
    } catch (error) {
      log(`质控失败: ${error.message}`);
    }
  });

  $("btn-report").addEventListener("click", async () => {
    try {
      if (!reportId) throw new Error("没有可下载的报告");
      const response = await apiResponse("GET", `/dq213/qc/reports/${encodeURIComponent(reportId)}`);
      if (!response.ok) throw new Error(`报告下载失败 ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `dq213-qc-${reportId}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      log("质控报告已下载");
    } catch (error) {
      log(`下载报告失败: ${error.message}`);
    }
  });

  const textDemo = "患者张伟，身份证110101199001011234，手机13812345678，住址北京市海淀区某某路1号。诊断精神分裂症，奥氮平10mg。病案号：ZY20260001。";
  const rowsDemo = [
    {
      patient_id: "PATIENT-001",
      patient_name: "张伟",
      phone: "13812345678",
      id_card: "110101199001011234",
      email: "demo@hospital.org",
      address: "北京市海淀区某某路1号",
      note: "患者张伟联系电话13812345678，诊断抑郁症。",
    },
  ];

  $("btn-phi-demo").addEventListener("click", () => {
    $("phi-kind").value = "text";
    $("phi-input").value = textDemo;
  });
  $("btn-phi-rows-demo").addEventListener("click", () => {
    $("phi-kind").value = "rows";
    $("phi-input").value = JSON.stringify(rowsDemo, null, 2);
  });

  $("btn-phi-run").addEventListener("click", async () => {
    try {
      const kind = $("phi-kind").value;
      const body = kind === "rows"
        ? { rows: JSON.parse($("phi-input").value) }
        : { text: $("phi-input").value, mode: $("phi-mode").value };
      const result = await api("POST", "/dq213/phi/anonymize", body);
      $("phi-output").textContent = kind === "rows" ? JSON.stringify(result.rows || [], null, 2) : (result.anonymized || "");
      const detection = result.detection || {};
      $("phi-meta").textContent = kind === "rows"
        ? `处理 ${result.n_rows || 0} 行，执行 ${result.n_field_ops || 0} 次字段操作`
        : `识别 ${detection.n_entities || 0} 处，类型 ${JSON.stringify(detection.by_type || {})}`;
      log("脱敏完成", kind === "rows" ? { n_rows: result.n_rows, n_field_ops: result.n_field_ops } : detection.by_type);
    } catch (error) {
      log(`脱敏失败: ${error.message}`);
    }
  });

  const TYPE_LABELS = {
    diagnosis: "诊断",
    admission: "入院",
    discharge: "出院",
    medication: "用药",
    examination: "检查",
    lab: "检验",
    assessment: "量表",
    clinical_note: "病历",
    followup: "随访",
  };

  async function loadPatients() {
    if (!token) return;
    try {
      const result = await api("GET", "/dq213/timeline/patients?limit=80");
      const select = $("tl-patient");
      select.replaceChildren();
      (result.items || []).forEach((patient) => {
        const option = document.createElement("option");
        option.value = String(patient.patient_id || "");
        option.textContent = `${patient.patient_id || ""} · ${patient.diagnosis || ""}`;
        select.appendChild(option);
      });
      const demo = Array.from(select.options).find((option) => option.value === "DQ213-DEMO-001");
      if (demo) select.value = demo.value;
    } catch (error) {
      log(`加载患者列表失败: ${error.message}`);
    }
  }

  function selectedTypes() {
    return Array.from(document.querySelectorAll("#tl-filters input[type=checkbox]:checked")).map((input) => input.value);
  }

  function renderFilters() {
    const container = $("tl-filters");
    container.replaceChildren();
    Object.keys(TYPE_LABELS).forEach((type) => {
      const label = document.createElement("label");
      label.className = "check-label";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = type;
      input.checked = true;
      label.append(input, document.createTextNode(` ${TYPE_LABELS[type]}`));
      container.appendChild(label);
    });
  }

  function safeAssetUri(value) {
    const text = String(value || "");
    if (text.startsWith("/") && !text.startsWith("//")) return text;
    try {
      const parsed = new URL(text);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? text : "";
    } catch (_error) {
      return "";
    }
  }

  function renderTimelineEvent(event) {
    const item = document.createElement("article");
    item.className = "t-item";
    const metaLine = document.createElement("div");
    metaLine.className = "meta";
    metaLine.append(document.createTextNode(`${event.event_date || "日期未知"} · `));
    const chip = document.createElement("span");
    chip.classList.add("chip");
    if (Object.prototype.hasOwnProperty.call(TYPE_LABELS, event.event_type)) chip.classList.add(event.event_type);
    chip.textContent = TYPE_LABELS[event.event_type] || String(event.event_type || "未知");
    metaLine.append(chip, document.createTextNode(` · ${event.modality || "structured"}`));
    const title = document.createElement("strong");
    title.textContent = String(event.title || "");
    const detail = document.createElement("pre");
    detail.className = "event-detail";
    detail.textContent = JSON.stringify(event.detail || {}, null, 2);
    item.append(metaLine, title, detail);
    const assets = event.assets || [];
    if (assets.length) {
      const assetGrid = document.createElement("div");
      assetGrid.className = "asset-grid";
      assets.forEach((asset) => {
        const card = document.createElement("div");
        card.className = "asset-card";
        const uri = safeAssetUri(asset.uri);
        const thumbnail = safeAssetUri(asset.thumbnail_uri || asset.uri);
        if (asset.modality === "image" && thumbnail) {
          const image = document.createElement("img");
          image.src = thumbnail;
          image.alt = String(asset.title || "医学影像");
          image.loading = "lazy";
          card.appendChild(image);
        }
        const label = document.createElement(uri ? "a" : "span");
        label.textContent = `${asset.title || "附件"} · ${asset.mime_type || ""}`;
        if (uri) {
          label.href = uri;
          label.target = "_blank";
          label.rel = "noopener noreferrer";
        }
        card.appendChild(label);
        assetGrid.appendChild(card);
      });
      item.appendChild(assetGrid);
    }
    return item;
  }

  $("btn-tl-refresh").addEventListener("click", loadPatients);
  $("btn-tl-load").addEventListener("click", async () => {
    try {
      const patientId = $("tl-patient").value;
      if (!patientId) throw new Error("请选择患者");
      const modality = $("tl-modality").value;
      const result = await api("POST", "/dq213/timeline/query", {
        patient_id: patientId,
        event_types: selectedTypes(),
        start_date: $("tl-start").value || null,
        end_date: $("tl-end").value || null,
        modalities: modality ? [modality] : [],
        keyword: $("tl-keyword").value.trim() || null,
        limit: 500,
      });
      if (!result.ok) throw new Error(result.error || "轨迹查询失败");
      $("tl-n").textContent = String(result.n_events || 0);
      $("tl-types").textContent = String(Object.keys(result.by_type || {}).length);
      $("tl-mod").textContent = String(Object.keys(result.by_modality || {}).length);
      $("tl-assets").textContent = String(result.n_assets || 0);
      $("tl-pid").textContent = result.patient_id || patientId;
      const list = $("tl-list");
      list.replaceChildren();
      (result.events || []).forEach((event) => list.appendChild(renderTimelineEvent(event)));
      if (!(result.events || []).length) {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "当前筛选条件下没有事件。";
        list.appendChild(empty);
      }
      log("轨迹加载完成", { n_events: result.n_events, n_assets: result.n_assets, by_type: result.by_type, warnings: result.warnings });
    } catch (error) {
      log(`轨迹失败: ${error.message}`);
    }
  });

  renderFilters();
  if (token) {
    $("login-status").textContent = "已发现现有会话 token";
    $("login-status").className = "status ok";
    loadPatients();
  }
})();
