(function () {
  "use strict";

  const meta = document.querySelector('meta[name="agent-api-base"]');
  const API = meta ? String(meta.content || "") : "";
  let token =
    sessionStorage.getItem("psych_access_token") ||
    localStorage.getItem("agent_access_token") ||
    "";
  let lastItemScores = null;

  const $ = (id) => document.getElementById(id);
  const logEl = $("log");

  function log(message, detail) {
    const safeDetail = detail === undefined ? "" : `\n${JSON.stringify(detail, null, 2)}`;
    const line = `${String(message)}${safeDetail}`;
    logEl.textContent = (logEl.textContent === "日志…" ? "" : `${logEl.textContent}\n\n`) + line;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function show(el, data) {
    el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  }

  function formatApiError(payload, fallback) {
    if (payload == null || payload === "") return fallback || "请求失败";
    if (typeof payload === "string") return payload;
    if (typeof payload === "object") {
      if (payload.detail != null) return formatApiError(payload.detail, fallback);
      if (payload.msg) return String(payload.msg);
      try {
        return JSON.stringify(payload);
      } catch (_e) {
        return fallback || "请求失败";
      }
    }
    return String(payload);
  }

  async function apiResponse(method, path, body, isForm) {
    const headers = {};
    if (!isForm && body != null) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    return fetch(API + path, {
      method,
      headers,
      body: isForm ? body : body != null ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
      cache: "no-store",
    });
  }

  async function api(method, path, body) {
    const response = await apiResponse(method, path, body);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, response.statusText));
    // 兼容 psych {status,data} 与 auth {code,msg,data}
    if (payload.data != null) return payload.data;
    if (payload.code != null && payload.code !== 0) {
      throw new Error(payload.msg || formatApiError(payload, "请求失败"));
    }
    return payload;
  }

  async function applyLoginToken(result) {
    token = result.access_token || result.token || "";
    if (!token) throw new Error("登录响应缺少 token");
    sessionStorage.setItem("psych_access_token", token);
    try {
      localStorage.setItem("agent_access_token", token);
    } catch (_e) {
      /* ignore quota */
    }
    syncLoginStatus();
  }

  async function pollTask(taskId, maxTries) {
    const tries = maxTries || 40;
    for (let i = 0; i < tries; i += 1) {
      const row = await api("GET", `/psych/tasks/${encodeURIComponent(taskId)}`);
      if (["success", "failed", "cancelled"].includes(row.status)) return row;
      await new Promise((r) => setTimeout(r, 800));
    }
    throw new Error("任务轮询超时");
  }

  function syncLoginStatus() {
    if (token) {
      $("login-status").textContent = "已登录";
      $("login-status").className = "status ok";
    } else {
      $("login-status").textContent = "未登录";
      $("login-status").className = "status warn";
    }
  }
  syncLoginStatus();

  /** 点击后立刻禁用，异步结束（成功/失败）后再恢复，防止重复提交。 */
  function bindAsyncClick(id, handler) {
    const btn = $(id);
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await handler(btn);
      } finally {
        btn.disabled = false;
      }
    });
  }

  document.querySelectorAll(".side [data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".side [data-tab]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const tabs = [
        "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9", "m10", "m12",
      ];
      tabs.forEach((tab) => {
        const panel = $(`tab-${tab}`);
        if (panel) panel.classList.toggle("hidden", tab !== button.dataset.tab);
      });
    });
  });

  $("btn-send-code").addEventListener("click", async () => {
    const phone = $("login-phone").value.trim();
    if (!phone) {
      log("请先填写手机号");
      return;
    }
    const btn = $("btn-send-code");
    try {
      btn.disabled = true;
      const data = await api("POST", "/auth/send-sms-code", { phone });
      log("验证码已发送", data);
      let left = 60;
      btn.textContent = `${left}s`;
      const timer = setInterval(() => {
        left -= 1;
        if (left <= 0) {
          clearInterval(timer);
          btn.disabled = false;
          btn.textContent = "发送验证码";
        } else {
          btn.textContent = `${left}s`;
        }
      }, 1000);
    } catch (error) {
      btn.disabled = false;
      btn.textContent = "发送验证码";
      log(`发送验证码失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-login", async () => {
    try {
      const result = await api("POST", "/auth/login-with-sms", {
        phone: $("login-phone").value.trim(),
        code: $("login-code").value.trim(),
      });
      await applyLoginToken(result);
      log("登录成功");
    } catch (error) {
      log(`登录失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-accept-login", async () => {
    try {
      $("login-phone").value = "13800000000";
      $("login-code").value = "888888";
      const result = await api("POST", "/auth/login-with-sms", {
        phone: "13800000000",
        code: "888888",
      });
      await applyLoginToken(result);
      log("验收登录成功（需 ACCEPTANCE_MODE 或已种子验收账号）");
    } catch (error) {
      log(`验收登录失败: ${error.message}。可改用「发送验证码」正式登录，或 bash scripts/init-platform.sh --acceptance`);
    }
  });

  bindAsyncClick("btn-health", async () => {
    try {
      const data = await api("GET", "/psych/health");
      log("health", data);
    } catch (error) {
      log(`health 失败: ${error.message}`);
    }
  });

  function fillDatasetIds(id) {
    if (!id) return;
    ["ds-id", "pipe-ds", "stats-ds", "var-ds", "llm-ds", "exp-ds", "ml-ds", "feat-ds"].forEach((key) => {
      const el = $(key);
      if (el && !el.value) el.value = String(id);
    });
  }

  // ----- M1 -----
  bindAsyncClick("btn-ds-create", async () => {
    try {
      const data = await api("POST", "/psych/datasets", {
        name: $("ds-name").value.trim(),
        source_type: $("ds-type").value,
        description: "2.1.4 demo",
      });
      $("ds-id").value = data.id;
      fillDatasetIds(data.id);
      show($("ds-out"), data);
      log("创建数据集", data);
      await refreshDatasets();
    } catch (error) {
      log(`创建数据集失败: ${error.message}`);
    }
  });

  async function refreshDatasets() {
    const data = await api("GET", "/psych/datasets");
    const box = $("ds-list");
    box.replaceChildren();
    (data.datasets || []).forEach((ds) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `#${ds.id} ${ds.name} (${ds.source_type})`;
      btn.addEventListener("click", () => {
        $("ds-id").value = ds.id;
        fillDatasetIds(ds.id);
      });
      box.appendChild(btn);
    });
    show($("ds-out"), data);
  }

  bindAsyncClick("btn-ds-list", async () => {
    try {
      await refreshDatasets();
      log("数据集列表已刷新");
    } catch (error) {
      log(`列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ds-ingest", async () => {
    try {
      const id = $("ds-id").value;
      if (!id) throw new Error("请先填写 dataset_id");
      const file = $("ds-file").files[0];
      if (!file) throw new Error("请选择文件");
      const form = new FormData();
      form.append("file", file);
      form.append("record_type", "row");
      form.append("patient_key_col", $("ds-pk").value.trim() || "");
      const response = await apiResponse("POST", `/psych/datasets/${id}/ingest`, form, true);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatApiError(payload, response.statusText));
      const data = payload.data != null ? payload.data : payload;
      show($("ds-out"), data);
      log("ingest 已提交", data);
      if (data.task_id) {
        const task = await pollTask(data.task_id);
        show($("ds-out"), task);
        log("ingest 任务完成", task);
      }
    } catch (error) {
      log(`ingest 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ds-preview", async () => {
    try {
      const id = $("ds-id").value;
      const data = await api("GET", `/psych/datasets/${id}/preview?n_rows=15`);
      show($("ds-out"), data);
      log("preview", { rows: (data.rows || data.preview || []).length });
    } catch (error) {
      log(`preview 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ds-query", async () => {
    try {
      const id = $("ds-id").value;
      const data = await api("GET", `/psych/datasets/${id}/query?limit=50`);
      show($("ds-out"), data);
      log("query", data);
    } catch (error) {
      log(`query 失败: ${error.message}`);
    }
  });

  // ----- M2 -----
  bindAsyncClick("btn-pipe-methods", async () => {
    try {
      const data = await api("GET", "/psych/pipelines/methods");
      show($("pipe-out"), data);
      log("pipeline methods", data);
    } catch (error) {
      log(`methods 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-pipe-create", async () => {
    try {
      const data = await api("POST", "/psych/pipelines", {
        name: $("pipe-name").value.trim() || "示意管线",
        steps: [
          { method_id: "describe_full", params: {} },
          { method_id: "pearson_correlation", params: {} },
        ],
      });
      $("pipe-id").value = data.id;
      show($("pipe-out"), data);
      log("创建管线", data);
    } catch (error) {
      log(`创建管线失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-pipe-list", async () => {
    try {
      const data = await api("GET", "/psych/pipelines");
      show($("pipe-out"), data);
    } catch (error) {
      log(`管线列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-pipe-run", async () => {
    try {
      const id = $("pipe-id").value;
      const body = {};
      if ($("pipe-ds").value) body.dataset_id = Number($("pipe-ds").value);
      const data = await api("POST", `/psych/pipelines/${id}/run`, body);
      show($("pipe-out"), data);
      if (data.task_id) {
        const task = await pollTask(data.task_id);
        show($("pipe-out"), task);
        log("管线完成", task);
      }
    } catch (error) {
      log(`管线运行失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-tpl-save", async () => {
    try {
      const data = await api("POST", "/psych/param-templates", {
        module: $("tpl-module").value.trim(),
        method_id: $("tpl-method").value.trim(),
        name: $("tpl-name").value.trim(),
        params: { alpha: 0.05 },
        is_default: true,
      });
      show($("pipe-out"), data);
      log("保存模板", data);
    } catch (error) {
      log(`模板失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-tpl-list", async () => {
    try {
      const mod = $("tpl-module").value.trim();
      const data = await api("GET", `/psych/param-templates${mod ? `?module=${encodeURIComponent(mod)}` : ""}`);
      show($("pipe-out"), data);
    } catch (error) {
      log(`模板列表失败: ${error.message}`);
    }
  });

  // ----- M3 -----
  bindAsyncClick("btn-stats-methods", async () => {
    try {
      const data = await api("GET", "/psych/stats/methods");
      const box = $("stats-methods");
      box.replaceChildren();
      (data.methods || []).forEach((m, index) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = m.method_id;
        input.checked = index < 3;
        label.append(input, document.createTextNode(` ${m.name_zh || m.method_id}`));
        box.appendChild(label);
      });
      show($("stats-out"), { count: (data.methods || []).length, methods: data.methods });
      log(`统计方法 ${(data.methods || []).length} 个`);
    } catch (error) {
      log(`统计目录失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-stats-run", async () => {
    try {
      const methodIds = Array.from($("stats-methods").querySelectorAll("input:checked")).map((el) => el.value);
      if (!methodIds.length) throw new Error("请至少勾选一种统计方法");
      const cols = $("stats-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
      const mappings = {};
      if (methodIds.includes("describe_full") && cols.length) {
        mappings.describe_full = { numeric_columns: cols };
      }
      const body = {
        method_ids: methodIds,
        mappings,
        params_by_method: {},
      };
      if ($("stats-ds").value) body.dataset_id = Number($("stats-ds").value);
      const data = await api("POST", "/psych/stats/run", body);
      $("stats-task").value = data.task_id || "";
      show($("stats-out"), data);
      log("统计任务已提交", data);
    } catch (error) {
      log(`统计运行失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-stats-poll", async () => {
    try {
      const taskId = $("stats-task").value.trim();
      if (!taskId) throw new Error("无 task_id");
      const task = await pollTask(taskId);
      show($("stats-out"), task);
      log("统计任务状态", task);
    } catch (error) {
      log(`轮询失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-stats-results", async () => {
    try {
      const taskId = $("stats-task").value.trim();
      const data = await api("GET", `/psych/stats/results/${encodeURIComponent(taskId)}`);
      show($("stats-out"), data);
      log("统计 results", data);
    } catch (error) {
      log(`results 失败: ${error.message}`);
    }
  });

  // ----- M4 -----
  bindAsyncClick("btn-var-create", async () => {
    try {
      const body = {
        var_name: $("var-name").value.trim(),
        display_name: $("var-display").value.trim(),
        category: $("var-cat").value.trim(),
        dtype: "numeric",
      };
      if ($("var-ds").value) body.dataset_id = Number($("var-ds").value);
      const data = await api("POST", "/psych/variables", body);
      $("var-id").value = data.id;
      show($("var-out"), data);
      log("创建变量", data);
    } catch (error) {
      log(`变量创建失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-var-list", async () => {
    try {
      const qs = $("var-ds").value ? `?dataset_id=${$("var-ds").value}` : "";
      const data = await api("GET", `/psych/variables${qs}`);
      show($("var-out"), data);
    } catch (error) {
      log(`变量列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-var-batch", async () => {
    try {
      const data = await api("POST", "/psych/variables/batch", {
        items: [
          { var_name: "age", display_name: "年龄", dtype: "numeric", category: "人口学" },
          { var_name: "relapse", display_name: "复发", dtype: "categorical", category: "结局" },
        ],
      });
      show($("var-out"), data);
      log("批量变量", data);
    } catch (error) {
      log(`批量失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-var-dict", async () => {
    try {
      const qs = $("var-ds").value ? `?dataset_id=${$("var-ds").value}&format=json` : "?format=json";
      const data = await api("GET", `/psych/variables/dictionary/export${qs}`);
      show($("var-out"), data);
      log("数据字典导出", data);
    } catch (error) {
      log(`字典导出失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-cat-create", async () => {
    try {
      const data = await api("POST", "/psych/var-categories", {
        name: $("cat-name").value.trim(),
        sort_order: 0,
      });
      show($("var-out"), data);
    } catch (error) {
      log(`分类失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-cat-list", async () => {
    try {
      const data = await api("GET", "/psych/var-categories");
      show($("var-out"), data);
    } catch (error) {
      log(`分类列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-var-map", async () => {
    try {
      const data = await api("POST", "/psych/variables/mapping", {
        var_id: Number($("var-id").value),
        mapping: { source_column: $("var-name").value.trim(), transform: "identity" },
      });
      show($("var-out"), data);
    } catch (error) {
      log(`映射失败: ${error.message}`);
    }
  });

  // ----- M5 -----
  bindAsyncClick("btn-llm-extract", async () => {
    try {
      const body = { text: $("llm-text").value, extract_type: "clinical_entities" };
      if ($("llm-ds").value) body.dataset_id = Number($("llm-ds").value);
      const data = await api("POST", "/psych/llm/extract", body);
      show($("llm-out"), data);
      log("LLM extract", data);
    } catch (error) {
      log(`extract 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-llm-relate", async () => {
    try {
      const data = await api("POST", "/psych/llm/relate", {
        entities: { diagnosis: "抑郁障碍", medication: "舍曲林", scale: "HAMD" },
        question: "诊断、用药与量表严重度如何关联？",
      });
      show($("llm-out"), data);
      log("LLM relate", data);
    } catch (error) {
      log(`relate 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-llm-query", async () => {
    try {
      const body = { query: $("llm-text").value.trim() || "HAMD 总分大于 17 的患者" };
      if ($("llm-ds").value) body.dataset_id = Number($("llm-ds").value);
      const data = await api("POST", "/psych/llm/query", body);
      show($("llm-out"), data);
      log("LLM query", data);
    } catch (error) {
      log(`query 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-llm-qa", async () => {
    try {
      const body = {
        question: $("llm-text").value.trim() || "如何解读 HAMD 22 分？",
        context: "精神专科临床数据分析示意",
      };
      if ($("llm-ds").value) body.dataset_id = Number($("llm-ds").value);
      const data = await api("POST", "/psych/llm/qa", body);
      show($("llm-out"), data);
      log("LLM qa", data);
    } catch (error) {
      log(`qa 失败: ${error.message}`);
    }
  });

  // ----- M6 -----
  bindAsyncClick("btn-param-get", async () => {
    try {
      const scope = $("param-scope").value;
      const data = await api("GET", `/psych/analysis-params?scope=${encodeURIComponent(scope)}`);
      show($("param-out"), data);
    } catch (error) {
      log(`读参数失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-param-put", async () => {
    try {
      let value = $("param-val").value;
      if (!Number.isNaN(Number(value)) && value.trim() !== "") value = Number(value);
      const data = await api("PUT", "/psych/analysis-params", {
        scope: $("param-scope").value,
        items: { [$("param-key").value.trim()]: value },
      });
      show($("param-out"), data);
      log("参数已保存", data);
    } catch (error) {
      log(`写参数失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-exp-create", async () => {
    try {
      const body = {
        kind: $("exp-kind").value.trim(),
        format: $("exp-fmt").value,
        note: "2.1.4 demo export",
      };
      if ($("exp-ds").value) body.dataset_id = Number($("exp-ds").value);
      if ($("exp-task").value.trim()) body.task_id = $("exp-task").value.trim();
      if (!body.dataset_id && !body.task_id) {
        body.data = [
          { patient_id: "P001", HAMD_total: 22 },
          { patient_id: "P002", HAMD_total: 14 },
        ];
      }
      const data = await api("POST", "/psych/exports", body);
      $("exp-id").value = data.export_id || data.id || "";
      show($("param-out"), data);
      log("导出已创建", data);
    } catch (error) {
      log(`导出失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-exp-dl", async () => {
    try {
      const id = $("exp-id").value.trim();
      if (!id) throw new Error("无 export_id");
      const response = await apiResponse("GET", `/psych/exports/${encodeURIComponent(id)}/download`);
      if (!response.ok) throw new Error(`下载失败 ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `psych-export-${id}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      log("导出文件已下载");
    } catch (error) {
      log(`下载失败: ${error.message}`);
    }
  });

  // ----- M7 -----
  bindAsyncClick("btn-ml-algos", async () => {
    try {
      const data = await api("GET", "/psych/ml/algorithms");
      const box = $("ml-algos");
      box.replaceChildren();
      (data.algorithms || []).forEach((a) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = a.name_zh || a.algo_id;
        btn.addEventListener("click", () => {
          $("ml-algo").value = a.algo_id;
        });
        box.appendChild(btn);
      });
      show($("ml-out"), { count: (data.algorithms || []).length, algorithms: data.algorithms });
      log(`ML 算法 ${(data.algorithms || []).length} 个`);
    } catch (error) {
      log(`算法目录失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ml-models", async () => {
    try {
      const data = await api("GET", "/psych/ml/models");
      show($("ml-out"), data);
    } catch (error) {
      log(`模型列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ml-train", async () => {
    try {
      const features = $("ml-feat").value.split(",").map((s) => s.trim()).filter(Boolean);
      const body = {
        algo_id: $("ml-algo").value.trim(),
        mapping: {
          id_col: $("ml-idcol").value.trim(),
          feature_columns: features,
          target_col: $("ml-target").value.trim(),
        },
        model_name: `demo_${$("ml-algo").value.trim()}`,
        sync_resource: true,
      };
      if ($("ml-ds").value) body.dataset_id = Number($("ml-ds").value);
      const data = await api("POST", "/psych/ml/train", body);
      $("ml-task").value = data.task_id || "";
      show($("ml-out"), data);
      if (data.task_id) {
        const task = await pollTask(data.task_id, 60);
        show($("ml-out"), task);
        const mid = task.result_json && task.result_json.psych_model_id;
        if (mid) $("ml-model").value = mid;
        log("训练完成", task);
      }
    } catch (error) {
      log(`训练失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-ml-predict", async () => {
    try {
      const body = { model_id: Number($("ml-model").value) };
      if ($("ml-ds").value) body.dataset_id = Number($("ml-ds").value);
      else {
        body.rows = [{ patient_id: "P001", age: 35, HAMD_total: 22, relapse: 0 }];
      }
      const data = await api("POST", "/psych/ml/predict", body);
      show($("ml-out"), data);
      log("预测", data);
    } catch (error) {
      log(`预测失败: ${error.message}`);
    }
  });

  // ----- M8 -----
  bindAsyncClick("btn-feat-run", async () => {
    try {
      const body = {
        feature_type: $("feat-type").value,
        feature_set_name: $("feat-name").value.trim(),
      };
      if ($("feat-ds").value) body.dataset_id = Number($("feat-ds").value);
      const data = await api("POST", "/psych/features/extract", body);
      if (data.id) $("feat-id").value = data.id;
      show($("feat-out"), data);
      log("特征提取", data);
    } catch (error) {
      log(`特征提取失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-feat-list", async () => {
    try {
      const qs = $("feat-ds").value ? `?dataset_id=${$("feat-ds").value}` : "";
      const data = await api("GET", `/psych/features${qs}`);
      show($("feat-out"), data);
    } catch (error) {
      log(`特征列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-feat-get", async () => {
    try {
      const data = await api("GET", `/psych/features/${$("feat-id").value}`);
      show($("feat-out"), data);
    } catch (error) {
      log(`特征详情失败: ${error.message}`);
    }
  });

  // ----- M9 -----
  bindAsyncClick("btn-dl-models", async () => {
    try {
      const data = await api("GET", "/psych/dl/models");
      show($("dl-out"), data);
      log("DL 模型", data);
    } catch (error) {
      log(`DL 目录失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-dl-train", async () => {
    try {
      const texts = [
        "焦虑 失眠 紧张",
        "情绪 低落 兴趣减退",
        "正常 生活 工作顺利",
        "幻觉 妄想 思维紊乱",
        "轻度 焦虑 可应对",
        "抑郁 自杀观念",
        "睡眠良好 情绪平稳",
        "幻听 被害妄想",
      ];
      const labels = [0, 1, 0, 1, 0, 1, 0, 1];
      const data = await api("POST", "/psych/dl/train", {
        model_id: $("dl-model").value,
        texts,
        labels,
        epochs: Number($("dl-epochs").value) || 2,
      });
      $("dl-task").value = data.task_id || "";
      show($("dl-out"), data);
      log("DL 训练已提交", data);
    } catch (error) {
      log(`DL 训练失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-dl-poll", async () => {
    try {
      const taskId = $("dl-task").value.trim();
      if (!taskId) throw new Error("无 task_id");
      const task = await pollTask(taskId, 60);
      show($("dl-out"), task);
      const meta = task.result_json && task.result_json.meta_path;
      if (meta) $("dl-meta").value = meta;
      log("DL 任务", task);
    } catch (error) {
      log(`DL 轮询失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-dl-infer", async () => {
    try {
      const data = await api("POST", "/psych/dl/infer", {
        meta_path: $("dl-meta").value.trim(),
        texts: ["焦虑 失眠", "情绪 低落"],
      });
      show($("dl-out"), data);
      log("DL 推理", data);
    } catch (error) {
      log(`推理失败: ${error.message}`);
    }
  });

  // ----- M10 -----
  async function refreshCaps() {
    const kind = $("cap-kind").value;
    const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
    const data = await api("GET", `/psych/capabilities${qs}`);
    const box = $("cap-list");
    box.replaceChildren();
    (data.capabilities || []).forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      const id = c.capability_id || c.id;
      btn.textContent = `${id} · ${c.enabled ? "开" : "关"} · ${c.version || ""}`;
      btn.addEventListener("click", () => {
        $("cap-id").value = id;
        $("up-id").value = id;
        const compose = $("cap-compose");
        const parts = compose.value ? compose.value.split(",").map((s) => s.trim()).filter(Boolean) : [];
        if (!parts.includes(id)) parts.push(id);
        compose.value = parts.join(",");
      });
      box.appendChild(btn);
    });
    show($("cap-out"), data);
    return data;
  }

  bindAsyncClick("btn-cap-list", async () => {
    try {
      await refreshCaps();
      log("能力列表已刷新");
    } catch (error) {
      log(`能力列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-cap-enable", async () => {
    try {
      const id = $("cap-id").value.trim();
      const data = await api("PUT", `/psych/capabilities/${encodeURIComponent(id)}`, { enabled: true });
      show($("cap-out"), data);
      await refreshCaps();
    } catch (error) {
      log(`启用失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-cap-disable", async () => {
    try {
      const id = $("cap-id").value.trim();
      const data = await api("PUT", `/psych/capabilities/${encodeURIComponent(id)}`, { enabled: false });
      show($("cap-out"), data);
      await refreshCaps();
    } catch (error) {
      log(`停用失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-cap-compose", async () => {
    try {
      const ids = $("cap-compose").value.split(",").map((s) => s.trim()).filter(Boolean);
      const data = await api("POST", "/psych/capabilities/compose", {
        capability_ids: ids,
        name: "demo_compose",
      });
      show($("cap-out"), data);
      if (data.id) $("pipe-id").value = data.id;
      log("能力编排", data);
    } catch (error) {
      log(`编排失败: ${error.message}`);
    }
  });

  // ----- M12 -----
  function parseRawInput() {
    const raw = $("sc-raw").value.trim();
    try {
      return JSON.parse(raw);
    } catch (_e) {
      return raw;
    }
  }

  bindAsyncClick("btn-sc-forms", async () => {
    try {
      const data = await api("GET", "/psych/scales/forms");
      show($("sc-out"), data);
      log(`量表 ${(data.forms || []).length} 个`);
    } catch (error) {
      log(`量表定义失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-parse", async () => {
    try {
      const data = await api("POST", "/psych/scales/parse", {
        scale_code: $("sc-code").value,
        raw: parseRawInput(),
        patient_key: $("sc-pk").value.trim(),
      });
      lastItemScores = data.item_scores || null;
      show($("sc-out"), data);
      log("量表 parse", data);
    } catch (error) {
      log(`parse 失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-score", async () => {
    try {
      let itemScores = lastItemScores;
      if (!itemScores) {
        const parsed = await api("POST", "/psych/scales/parse", {
          scale_code: $("sc-code").value,
          raw: parseRawInput(),
          patient_key: $("sc-pk").value.trim(),
        });
        itemScores = parsed.item_scores;
        lastItemScores = itemScores;
      }
      const data = await api("POST", "/psych/scales/score", {
        scale_code: $("sc-code").value,
        item_scores: itemScores,
        patient_key: $("sc-pk").value.trim(),
      });
      show($("sc-out"), data);
      log("量表评分", data);
    } catch (error) {
      log(`评分失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-scores", async () => {
    try {
      const code = $("sc-code").value;
      const data = await api("GET", `/psych/scales/scores?scale_code=${encodeURIComponent(code)}&limit=50`);
      show($("sc-out"), data);
    } catch (error) {
      log(`得分列表失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-trend", async () => {
    try {
      const pk = $("sc-pk").value.trim();
      const code = $("sc-code").value;
      const data = await api(
        "GET",
        `/psych/scales/trend?patient_key=${encodeURIComponent(pk)}&scale_code=${encodeURIComponent(code)}`
      );
      show($("sc-out"), data);
      const points = data.points || data.series || data.scores || [];
      const box = $("sc-trend-bars");
      box.replaceChildren();
      const max = Math.max(1, ...points.map((p) => Number(p.total ?? p.score ?? p.y ?? 0)));
      points.forEach((p, i) => {
        const val = Number(p.total ?? p.score ?? p.y ?? 0);
        const row = document.createElement("div");
        row.className = "bar-row";
        const label = document.createElement("div");
        label.textContent = String(p.visited_at || p.date || p.t || `#${i + 1}`);
        const track = document.createElement("div");
        track.className = "bar-track";
        const fill = document.createElement("div");
        fill.className = "bar-fill";
        fill.style.width = `${(val / max) * 100}%`;
        track.appendChild(fill);
        const number = document.createElement("div");
        number.textContent = String(val);
        row.append(label, track, number);
        box.appendChild(row);
      });
      log("趋势", data);
    } catch (error) {
      log(`趋势失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-compare", async () => {
    try {
      const data = await api("POST", "/psych/scales/compare", {
        scale_code: $("sc-code").value,
        group_a: [$("sc-pk").value.trim() || "P001"],
        group_b: ["P002", "P003"],
      });
      show($("sc-out"), data);
      log("分组对比", data);
    } catch (error) {
      log(`对比失败: ${error.message}`);
    }
  });

  bindAsyncClick("btn-sc-export", async () => {
    try {
      const code = $("sc-code").value;
      const data = await api("GET", `/psych/scales/export?scale_code=${encodeURIComponent(code)}`);
      show($("sc-out"), data);
      log("量表导出", data);
    } catch (error) {
      log(`量表导出失败: ${error.message}`);
    }
  });

  if (token) {
    log("已检测到本地 token，可直接调用 /psych/*");
  }
})();
