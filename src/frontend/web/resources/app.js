(function () {
  "use strict";

  const meta = document.querySelector('meta[name="agent-api-base"]');
  const API = (meta && meta.content) || "";
  let token = localStorage.getItem("agent_access_token") || "";
  let currentParentId = null;
  let selectedDatasetId = null;
  let selectedModelId = null;
  let moveNodeId = null;

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

  async function apiForm(method, path, formData) {
    const opts = {
      method,
      headers: token ? { Authorization: "Bearer " + token } : {},
      body: formData,
    };
    const r = await fetch(API + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(formatApiError(data.detail != null ? data.detail : data, r.statusText || "请求失败"));
    }
    return data.data != null ? data.data : data;
  }

  function requireAuth() {
    if (!token) throw new Error("请先验收登录");
  }

  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function setStatus(ok, text) {
    $("status-badges").innerHTML = ok
      ? '<span class="badge ok">' + (text || "已登录") + "</span>"
      : '<span class="badge">' + (text || "未登录") + "</span>";
  }

  // ---------- tabs ----------
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tab = btn.getAttribute("data-tab");
      document.querySelectorAll(".panel-tab").forEach((p) => p.classList.add("hidden"));
      $("tab-" + tab).classList.remove("hidden");
      if (tab === "datasets") loadDatasets().catch((e) => ($("ds-msg").textContent = e.message));
      if (tab === "models") loadModels().catch((e) => ($("model-msg").textContent = e.message));
      if (tab === "files") loadFiles().catch((e) => ($("file-msg").textContent = e.message));
    });
  });

  // ---------- login ----------
  $("btn-login").addEventListener("click", async () => {
    try {
      $("login-status").textContent = "登录中…";
      const d = await api("POST", "/auth/login-with-sms", { phone: "13800000000", code: "888888" });
      token = d.access_token || "";
      if (!token) throw new Error("登录成功但未返回 access_token");
      localStorage.setItem("agent_access_token", token);
      $("login-status").textContent = "已登录";
      setStatus(true, "API 已连接");
      await loadFiles();
    } catch (e) {
      $("login-status").textContent = "登录失败: " + e.message;
      setStatus(false, "登录失败");
    }
  });

  if (token) {
    $("login-status").textContent = "已恢复本地 Token";
    setStatus(true, "已登录");
    loadFiles().catch(() => {});
  }

  // ---------- files ----------
  async function loadFiles() {
    requireAuth();
    const q = currentParentId == null ? "" : "?parent_id=" + currentParentId;
    const data = await api("GET", "/resources/files/tree" + q);
    const items = data.items || [];
    $("cwd-label").textContent = currentParentId == null ? "/" : ("#" + currentParentId);

    const tree = $("file-tree");
    tree.innerHTML = "";
    if (currentParentId != null) {
      const up = document.createElement("li");
      up.textContent = "‥ 上级 / 根目录";
      up.addEventListener("click", () => {
        currentParentId = null;
        loadFiles();
      });
      tree.appendChild(up);
    }
    items
      .filter((it) => it.node_type === "folder")
      .forEach((it) => {
        const li = document.createElement("li");
        li.className = "folder";
        li.textContent = it.name;
        li.addEventListener("click", () => {
          currentParentId = it.id;
          loadFiles();
        });
        tree.appendChild(li);
      });

    const tbody = $("file-table").querySelector("tbody");
    tbody.innerHTML = "";
    items.forEach((it) => {
      const tr = document.createElement("tr");
      const cat = it.category || "—";
      tr.innerHTML =
        "<td>" +
        escapeHtml(it.name) +
        "</td><td>" +
        it.node_type +
        '</td><td><span class="chip ' +
        cat +
        '">' +
        cat +
        "</span></td><td>" +
        (it.node_type === "file" ? fmtSize(it.size_bytes) : "—") +
        '</td><td class="ops"></td>';
      const ops = tr.querySelector(".ops");
      if (it.node_type === "folder") {
        ops.appendChild(btn("打开", () => {
          currentParentId = it.id;
          loadFiles();
        }));
      } else {
        ops.appendChild(btn("预览", () => previewFile(it.id)));
        ops.appendChild(btn("下载", () => downloadUrl("/resources/files/" + it.id + "/download", it.name)));
        if (it.category === "table" || (it.tags && it.tags.suggest_dataset)) {
          ops.appendChild(btn("晋升数据集", () => promoteDataset(it.id, it.name)));
        }
      }
      ops.appendChild(btn("移动", () => openMove(it.id)));
      ops.appendChild(btn("删除", () => deleteNode(it.id), "danger"));
      tbody.appendChild(tr);
    });
    $("file-msg").textContent = "共 " + items.length + " 项";
  }

  function btn(label, onClick, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.className = cls || "secondary";
    b.style.marginRight = "0.25rem";
    b.style.padding = "0.25rem 0.5rem";
    b.style.fontSize = "0.78rem";
    b.addEventListener("click", onClick);
    return b;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function previewFile(id) {
    requireAuth();
    const box = $("preview-box");
    box.textContent = "加载预览…";
    try {
      const data = await api("GET", "/resources/files/" + id + "/preview");
      if (!data) {
        box.textContent = "无预览数据";
        return;
      }
      if (data.kind === "table") {
        box.innerHTML = renderTable(data.columns, data.preview_rows);
      } else if (data.kind === "image" || data.kind === "nifti") {
        let html = '<img alt="preview" src="data:' + (data.mime || "image/png") + ";base64," + data.data_base64 + '" />';
        if (data.kind === "nifti") {
          html +=
            '<p class="hint">shape=' +
            JSON.stringify(data.shape) +
            " · slice=" +
            data.slice_index +
            "</p>";
        }
        box.innerHTML = html;
      } else if (data.kind === "pdf") {
        const r = await fetch(API + "/resources/files/" + id + "/preview?as_file=true", {
          headers: authHeaders(false),
        });
        if (!r.ok) throw new Error("PDF 加载失败");
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        box.innerHTML =
          '<iframe title="pdf" src="' + url + '"></iframe>' +
          '<p class="hint">PDF 预览（blob）</p>';
      } else if (data.kind === "text") {
        box.innerHTML = "<pre class='code-box'>" + escapeHtml(data.content) + "</pre>";
      } else {
        box.textContent = data.message || JSON.stringify(data);
      }
    } catch (e) {
      box.textContent = "预览失败: " + e.message;
    }
  }

  function renderTable(columns, rows) {
    if (!columns || !columns.length) return "<p class='hint'>无列</p>";
    let html = '<table class="data-table"><thead><tr>';
    columns.forEach((c) => {
      html += "<th>" + escapeHtml(c) + "</th>";
    });
    html += "</tr></thead><tbody>";
    (rows || []).forEach((row) => {
      html += "<tr>";
      columns.forEach((c) => {
        const v = row[c];
        html += "<td>" + escapeHtml(v == null ? "" : String(v)) + "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    return html;
  }

  function downloadUrl(path, filename) {
    requireAuth();
    fetch(API + path, { headers: authHeaders(false) })
      .then((r) => {
        if (!r.ok) throw new Error("下载失败 " + r.status);
        return r.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename || "download";
        a.click();
        URL.revokeObjectURL(url);
      })
      .catch((e) => alert(e.message));
  }

  async function promoteDataset(id, name) {
    try {
      requireAuth();
      const dsName = prompt("数据集名称", (name || "").replace(/\.[^.]+$/, "")) || "";
      if (!dsName) return;
      await api("POST", "/resources/files/" + id + "/promote-dataset", {
        name: dsName,
        description: "",
      });
      $("file-msg").textContent = "已晋升为数据集: " + dsName;
      alert("已创建数据集，可到「数据集」页查看");
    } catch (e) {
      alert(e.message);
    }
  }

  async function deleteNode(id) {
    if (!confirm("确认删除该节点？（文件夹将递归软删除）")) return;
    try {
      requireAuth();
      const res = await api("DELETE", "/resources/files/" + id);
      if (res.warning) alert(res.warning);
      await loadFiles();
    } catch (e) {
      alert(e.message);
    }
  }

  function openMove(id) {
    moveNodeId = id;
    $("move-target-id").value = "";
    $("move-dialog").classList.remove("hidden");
  }

  $("btn-move-cancel").addEventListener("click", () => {
    $("move-dialog").classList.add("hidden");
    moveNodeId = null;
  });

  $("btn-move-confirm").addEventListener("click", async () => {
    try {
      requireAuth();
      const raw = $("move-target-id").value.trim();
      const target = raw === "" ? null : Number(raw);
      await api("POST", "/resources/files/" + moveNodeId + "/move", {
        target_parent_id: target,
      });
      $("move-dialog").classList.add("hidden");
      moveNodeId = null;
      await loadFiles();
    } catch (e) {
      alert(e.message);
    }
  });

  $("btn-mkdir").addEventListener("click", async () => {
    try {
      requireAuth();
      const name = prompt("文件夹名称");
      if (!name) return;
      await api("POST", "/resources/files/mkdir", {
        name: name,
        parent_id: currentParentId,
      });
      await loadFiles();
    } catch (e) {
      alert(e.message);
    }
  });

  $("btn-refresh-tree").addEventListener("click", () => loadFiles().catch((e) => alert(e.message)));
  $("btn-goto-root").addEventListener("click", () => {
    currentParentId = null;
    loadFiles().catch((e) => alert(e.message));
  });

  $("btn-upload").addEventListener("click", async () => {
    try {
      requireAuth();
      const f = $("file-input").files[0];
      if (!f) {
        $("file-msg").textContent = "请选择文件";
        return;
      }
      const fd = new FormData();
      fd.append("file", f);
      if (currentParentId != null) fd.append("parent_id", String(currentParentId));
      const node = await apiForm("POST", "/resources/files/upload", fd);
      $("file-msg").textContent =
        "上传成功 · 分类=" + (node.category || "?") + (node.tags && node.tags.suggest_dataset ? " · 可晋升为数据集" : "");
      $("file-input").value = "";
      await loadFiles();
    } catch (e) {
      $("file-msg").textContent = e.message;
    }
  });

  // ---------- datasets ----------
  async function loadDatasets() {
    requireAuth();
    const kw = ($("ds-keyword").value || "").trim();
    const st = $("ds-status").value;
    let path = "/resources/datasets?limit=100&offset=0";
    if (kw) path += "&keyword=" + encodeURIComponent(kw);
    if (st) path += "&status=" + encodeURIComponent(st);
    const data = await api("GET", path);
    const tbody = $("ds-table").querySelector("tbody");
    tbody.innerHTML = "";
    (data.items || []).forEach((it) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        escapeHtml(it.name) +
        "</td><td>v" +
        it.current_version +
        "</td><td>" +
        it.status +
        "</td><td>" +
        escapeHtml(it.updated_at || "") +
        '</td><td class="ops"></td>';
      const ops = tr.querySelector(".ops");
      ops.appendChild(
        btn("查看", () => {
          selectedDatasetId = it.id;
          loadDatasetDetail();
        })
      );
      tbody.appendChild(tr);
    });
    $("ds-msg").textContent = "共 " + (data.total || 0) + " 个数据集";
  }

  async function loadDatasetDetail() {
    if (!selectedDatasetId) return;
    requireAuth();
    const detail = await api("GET", "/resources/datasets/" + selectedDatasetId);
    const ds = detail.dataset || {};
    const ver = detail.current_version_meta || {};
    $("ds-detail").innerHTML =
      "<p><strong>" +
      escapeHtml(ds.name) +
      "</strong> · v" +
      ds.current_version +
      " · " +
      ds.status +
      "</p>" +
      "<p class='hint'>" +
      escapeHtml(ds.description || "") +
      "</p>" +
      "<p class='hint'>行=" +
      (ver.row_count || 0) +
      " 列=" +
      (ver.column_count || 0) +
      "</p>" +
      "<pre class='code-box'>" +
      escapeHtml(JSON.stringify(ver.schema_json || [], null, 2)) +
      "</pre>";

    const preview = await api("GET", "/resources/datasets/" + selectedDatasetId + "/preview");
    const pv = preview.preview || {};
    $("ds-preview").innerHTML = renderTable(pv.columns || [], pv.rows || []);

    const versions = await api("GET", "/resources/datasets/" + selectedDatasetId + "/versions");
    const ul = $("ds-versions");
    ul.innerHTML = "";
    (versions.versions || []).forEach((v) => {
      const li = document.createElement("li");
      li.innerHTML =
        "<span>v" +
        v.version +
        " · " +
        escapeHtml(v.note || "") +
        " · " +
        (v.row_count || 0) +
        "×" +
        (v.column_count || 0) +
        "</span>";
      const b = btn("回滚到此版本", async () => {
        if (!confirm("确认回滚到 v" + v.version + "？")) return;
        await api("POST", "/resources/datasets/" + selectedDatasetId + "/rollback", {
          version: v.version,
        });
        await loadDatasetDetail();
        await loadDatasets();
      });
      li.appendChild(b);
      ul.appendChild(li);
    });

    $("ds-actions").classList.remove("hidden");
  }

  $("btn-ds-refresh").addEventListener("click", () =>
    loadDatasets().catch((e) => ($("ds-msg").textContent = e.message))
  );
  $("btn-ds-upload").addEventListener("click", async () => {
    try {
      requireAuth();
      const f = $("ds-upload-input").files[0];
      if (!f) {
        $("ds-msg").textContent = "请选择表格文件";
        return;
      }
      const fd = new FormData();
      fd.append("file", f);
      fd.append("name", f.name.replace(/\.[^.]+$/, ""));
      await apiForm("POST", "/resources/datasets/upload", fd);
      $("ds-upload-input").value = "";
      await loadDatasets();
    } catch (e) {
      $("ds-msg").textContent = e.message;
    }
  });
  $("btn-ds-version").addEventListener("click", async () => {
    try {
      requireAuth();
      if (!selectedDatasetId) return alert("请先选择数据集");
      const f = $("ds-version-input").files[0];
      if (!f) return alert("请选择新版本文件");
      const fd = new FormData();
      fd.append("file", f);
      fd.append("note", "UI 上传新版本");
      await apiForm("POST", "/resources/datasets/" + selectedDatasetId + "/versions", fd);
      $("ds-version-input").value = "";
      await loadDatasetDetail();
      await loadDatasets();
    } catch (e) {
      alert(e.message);
    }
  });
  $("btn-ds-refresh-meta").addEventListener("click", async () => {
    try {
      requireAuth();
      if (!selectedDatasetId) return;
      await api("POST", "/resources/datasets/" + selectedDatasetId + "/refresh-meta", {});
      await loadDatasetDetail();
      await loadDatasets();
    } catch (e) {
      alert(e.message);
    }
  });
  $("btn-ds-download").addEventListener("click", () => {
    if (!selectedDatasetId) return;
    downloadUrl("/resources/datasets/" + selectedDatasetId + "/download", "dataset");
  });
  $("btn-ds-archive").addEventListener("click", async () => {
    try {
      requireAuth();
      if (!selectedDatasetId) return;
      if (!confirm("确认归档该数据集？")) return;
      await api("DELETE", "/resources/datasets/" + selectedDatasetId);
      selectedDatasetId = null;
      $("ds-detail").textContent = "已归档";
      $("ds-actions").classList.add("hidden");
      await loadDatasets();
    } catch (e) {
      alert(e.message);
    }
  });

  // ---------- models ----------
  async function loadModels() {
    requireAuth();
    const kw = ($("model-keyword").value || "").trim();
    let path = "/resources/models?limit=100&offset=0";
    if (kw) path += "&keyword=" + encodeURIComponent(kw);
    const data = await api("GET", path);
    const tbody = $("model-table").querySelector("tbody");
    tbody.innerHTML = "";
    (data.items || []).forEach((it) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        escapeHtml(it.model_name) +
        "</td><td>" +
        escapeHtml(it.model_type || "—") +
        "</td><td>" +
        escapeHtml(it.source || "") +
        '</td><td class="ops"></td>';
      const ops = tr.querySelector(".ops");
      ops.appendChild(
        btn("查看", () => {
          selectedModelId = it.id;
          showModelDetail(it);
        })
      );
      tbody.appendChild(tr);
    });
    $("model-msg").textContent = "共 " + (data.total || 0) + " 个模型";
  }

  function showModelDetail(it) {
    $("model-detail").innerHTML =
      "<p><strong>" +
      escapeHtml(it.model_name) +
      "</strong> (#" +
      it.id +
      ")</p>" +
      "<pre class='code-box'>" +
      escapeHtml(
        JSON.stringify(
          {
            framework: it.framework,
            model_type: it.model_type,
            task_type: it.task_type,
            features: it.features,
            metrics: it.metrics,
            params: it.params,
            source: it.source,
          },
          null,
          2
        )
      ) +
      "</pre>";
  }

  $("btn-model-refresh").addEventListener("click", () =>
    loadModels().catch((e) => ($("model-msg").textContent = e.message))
  );

  $("btn-model-upload").addEventListener("click", async () => {
    try {
      requireAuth();
      const f = $("model-file").files[0];
      const name = ($("model-name").value || "").trim();
      if (!f || !name) {
        $("model-msg").textContent = "请填写模型名称并选择文件";
        return;
      }
      const fd = new FormData();
      fd.append("file", f);
      fd.append("model_name", name);
      const mt = ($("model-type").value || "").trim();
      const tt = ($("model-task").value || "").trim();
      const feat = ($("model-features").value || "").trim();
      if (mt) fd.append("model_type", mt);
      if (tt) fd.append("task_type", tt);
      if (feat) fd.append("features", feat);
      await apiForm("POST", "/resources/models/upload", fd);
      $("model-file").value = "";
      $("model-msg").textContent = "上传成功";
      await loadModels();
    } catch (e) {
      $("model-msg").textContent = e.message;
    }
  });

  $("btn-predict").addEventListener("click", async () => {
    try {
      requireAuth();
      if (!selectedModelId) return alert("请先选择模型");
      const raw = ($("predict-rows").value || "").trim();
      let rows;
      try {
        const parsed = JSON.parse(raw);
        rows = Array.isArray(parsed) ? parsed : [parsed];
      } catch (e) {
        throw new Error("预测输入必须是合法 JSON 数组/对象");
      }
      const res = await api("POST", "/resources/models/" + selectedModelId + "/predict", { rows: rows });
      $("predict-result").textContent = JSON.stringify(res, null, 2);
    } catch (e) {
      $("predict-result").textContent = "预测失败: " + e.message;
    }
  });

  $("btn-model-download").addEventListener("click", () => {
    if (!selectedModelId) return;
    downloadUrl("/resources/models/" + selectedModelId + "/download", "model.pkl");
  });

  $("btn-model-delete").addEventListener("click", async () => {
    try {
      requireAuth();
      if (!selectedModelId) return;
      if (!confirm("确认删除该模型？")) return;
      await api("DELETE", "/resources/models/" + selectedModelId);
      selectedModelId = null;
      $("model-detail").textContent = "已删除";
      await loadModels();
    } catch (e) {
      alert(e.message);
    }
  });
})();
