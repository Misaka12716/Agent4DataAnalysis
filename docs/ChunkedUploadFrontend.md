# 分片上传前端对接指南

> **读者**：产品前端开发。  
> **后端**：AgentPlatform FastAPI，默认 `http://<host>:52716`。  
> **关联文档**：[`BackendAPI.md`](BackendAPI.md)、[`ResourceAPI.md`](ResourceAPI.md)、[`PsychAPI.md`](PsychAPI.md)、[`2.1.1FrontendIntegrationGuide.md`](2.1.1FrontendIntegrationGuide.md)

本文说明统一分片上传协议 `/upload/chunked/*`：如何切割文件、并发上传、断点续传、进度计算，以及各业务 `target` 的参数与完成响应字段。

---

## 1. 为什么改

| 问题 | 整文件 multipart | 分片协议 |
|------|------------------|----------|
| 大文件单请求超时 | 易失败 | 每片独立超时 |
| 失败重传成本 | 整文件重传 | 仅重传失败分片 |
| 进度 | 粗糙 / 依赖 XHR | 按已上传字节精确计算 |
| 断点续传 | 无 | `GET status` + 本地缓存 `upload_id` |

上传完成后**仍复用**现有读删接口（如 `GET /session/workspace-tree`、`DELETE /session/workspace-file`、resources tree/download 等），`relative_path` / 资源节点语义与旧接口一致。

**不在范围内**：`POST /workbench/session/upload` 继续整文件上传，**没有** `workbench` target，前端工作台无需迁移。

---

## 2. 接口一览

鉴权：所有接口需 `Authorization: Bearer <access_token>`。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/upload/chunked/init` | 创建上传会话，返回 `upload_id` / `chunk_size` / `total_chunks` |
| `PUT` | `/upload/chunked/{upload_id}/parts/{index}` | 上传单个分片（`multipart` 字段名 `chunk`） |
| `GET` | `/upload/chunked/{upload_id}` | 查询已上传分片，用于断点续传 |
| `POST` | `/upload/chunked/{upload_id}/complete` | 合并分片并写入业务落点 |
| `DELETE` | `/upload/chunked/{upload_id}` | 中止并清理暂存 |

服务端默认：`chunk_size = 5MB`（允许 1~10MB）；未完成上传 TTL **24 小时**。

---

## 3. 推荐前端常量

```js
const CHUNK_SIZE = 5 * 1024 * 1024; // 5MB，与后端默认一致
const CONCURRENCY = 3;              // 建议 3~5
const PART_TIMEOUT_MS = 60_000;     // 单分片超时
const PART_MAX_RETRIES = 3;         // 单分片失败重试次数
```

进度：

```text
progress = uploaded_bytes / file.size
```

`uploaded_bytes` 可用本地累计，或定期用 `GET /upload/chunked/{upload_id}` 的 `uploaded_bytes` / `progress` 校准。

---

## 4. 标准流程（伪代码）

```js
async function uploadFileChunked(file, target, targetParams, token) {
  const key = `chunked:${target}:${file.name}:${file.size}:${file.lastModified}`;
  let uploadId = sessionStorage.getItem(key);

  // 1) 恢复或 init
  let status;
  if (uploadId) {
    const st = await api("GET", `/upload/chunked/${uploadId}`, { token });
    if (st.ok) status = st.data;
    else uploadId = null;
  }
  if (!uploadId) {
    const init = await api("POST", "/upload/chunked/init", {
      token,
      json: {
        filename: file.name,
        size: file.size,
        chunk_size: CHUNK_SIZE,
        target,
        target_params: targetParams,
        // file_sha256: optional
      },
    });
    uploadId = init.data.upload_id;
    sessionStorage.setItem(key, uploadId);
    status = {
      chunk_size: init.data.chunk_size,
      total_chunks: init.data.total_chunks,
      uploaded_parts: [],
      missing_parts: [...Array(init.data.total_chunks).keys()],
    };
  }

  const chunkSize = status.chunk_size;
  const total = status.total_chunks;
  const done = new Set(status.uploaded_parts || []);
  const queue = [...Array(total).keys()].filter((i) => !done.has(i));

  // 2) 并发上传分片
  async function uploadOne(index) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const blob = file.slice(start, end);
    for (let attempt = 1; attempt <= PART_MAX_RETRIES; attempt++) {
      try {
        const fd = new FormData();
        fd.append("chunk", blob, `part-${index}`);
        await api("PUT", `/upload/chunked/${uploadId}/parts/${index}`, {
          token,
          formData: fd,
          timeout: PART_TIMEOUT_MS,
        });
        return;
      } catch (e) {
        if (attempt === PART_MAX_RETRIES) throw e;
      }
    }
  }

  await runPool(queue, CONCURRENCY, uploadOne);

  // 3) 合并落盘
  const result = await api("POST", `/upload/chunked/${uploadId}/complete`, { token });
  sessionStorage.removeItem(key);
  return result; // 业务字段与旧整文件上传对齐，并含 upload_id
}
```

要点：

- 分片下标从 **0** 开始，最后一片可小于 `chunk_size`。
- 重复 `PUT` 同一 `index`（相同字节）视为成功（幂等）。
- 用户取消：`DELETE /upload/chunked/{upload_id}` 并清除本地 `upload_id`。

---

## 5. `target` 与 `target_params`

| target | 对应旧接口 | `target_params` | complete 成功形态 |
|--------|------------|-----------------|-------------------|
| `session` | `POST /session/upload-excel` | `{ "session_id": "<uuid>" }` | 顶层：`status/relative_path/original_filename/renamed/file_category/workspace_abs_path/session_id` + `upload_id` |
| `project_raw` | `POST /project/{id}/upload` | `{ "project_id": 1 }` | 顶层：`relative_path`（`raw/...`）等 + `upload_id` |
| `resources_file` | `POST /resources/files/upload` | `{ "parent_id": null \| number }` | `{ status, data: <file node>, upload_id }`，HTTP 201 |
| `resources_dataset` | `POST /resources/datasets/upload` | `{ "name"?, "description"? }` | `{ status, data, upload_id }`，201 |
| `resources_dataset_version` | `POST /resources/datasets/{id}/versions` | `{ "dataset_id", "note"? }` | `{ status, data, upload_id }`，201 |
| `resources_model` | `POST /resources/models/upload` | `{ "model_name", "model_type"?, "task_type"?, "features"?, "metrics"?, "params"? }` | `{ status, data, upload_id }`，201；仅 `.pkl/.joblib` |
| `psych_ingest` | `POST /psych/datasets/{id}/ingest` | `{ "dataset_id", "record_type"?, "patient_key_col"? }` | `{ status, data, upload_id }`，201 |

权限、扩展名白名单、大小上限在 **`init` 阶段**校验（与旧接口一致），避免传完再失败。

大小上限摘要：

- `session` / `project_raw` / resources*：默认 **2048MB**（resources 可由 `RESOURCES_MAX_UPLOAD_MB` 配置）
- `psych_ingest`：**200MB**

---

## 6. 请求 / 响应示例

### 6.1 init（会话）

```bash
curl -X POST "http://localhost:52716/upload/chunked/init" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "demo.csv",
    "size": 12345678,
    "chunk_size": 5242880,
    "target": "session",
    "target_params": { "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73" }
  }'
```

成功 `201`：

```json
{
  "status": "success",
  "data": {
    "upload_id": "a1b2c3...",
    "filename": "demo.csv",
    "size": 12345678,
    "chunk_size": 5242880,
    "total_chunks": 3,
    "target": "session",
    "target_params": { "session_id": "9e9f3f2f-5978-4b31-a57f-95b0e6478b73" },
    "expires_in_seconds": 86400
  }
}
```

### 6.2 上传分片

```bash
curl -X PUT "http://localhost:52716/upload/chunked/<upload_id>/parts/0" \
  -H "Authorization: Bearer <access_token>" \
  -F "chunk=@part0.bin"
```

可选 Form 字段：`part_sha256`（该分片内容的 hex SHA256）。

### 6.3 状态（断点续传）

```bash
curl "http://localhost:52716/upload/chunked/<upload_id>" \
  -H "Authorization: Bearer <access_token>"
```

`data` 含：`uploaded_parts`、`missing_parts`、`uploaded_bytes`、`progress`。

### 6.4 complete（会话）

```bash
curl -X POST "http://localhost:52716/upload/chunked/<upload_id>/complete" \
  -H "Authorization: Bearer <access_token>"
```

成功后可直接：

```bash
curl "http://localhost:52716/session/workspace-tree?session_id=<sid>" \
  -H "Authorization: Bearer <access_token>"
```

### 6.5 resources_file（fetch）

```js
const initRes = await fetch(`${API}/upload/chunked/init`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    filename: file.name,
    size: file.size,
    target: "resources_file",
    target_params: { parent_id: parentId ?? null },
  }),
});
const { data } = await initRes.json();
// …按 parts 上传后：
const done = await fetch(`${API}/upload/chunked/${data.upload_id}/complete`, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },
});
// done.json() => { status, data: fileNode, upload_id }
```

---

## 7. 错误码与幂等

| HTTP | 典型场景 |
|------|----------|
| 400 | 缺参数、分片大小不匹配、分片未齐、非法 `target` |
| 401 | 未登录 / token 无效 |
| 403 | 无项目权限 / 项目已归档 |
| 404 | `upload_id` 不存在或不属于当前用户；会话/数据集不存在 |
| 409 | 已 complete 的上传再次写分片 |
| 410 | 上传会话超过 24h TTL |
| 413 | 超过目标域大小上限 |
| 415 | 扩展名不在白名单 |

- 同一 `index` 重复上传**相同长度**内容：成功（覆盖写入）。
- `complete` 失败（业务落盘错误）时 staging 可能仍在；可修参后对**新** `upload_id` 重传，或 `DELETE` 后重新 init。
- 过期后必须重新 `init`。

---

## 8. 迁移清单

| 旧接口 | 新协议 | 备注 |
|--------|--------|------|
| `POST /session/upload-excel` | `target=session` | **新产品前端必须迁移**；旧接口暂保留，`deprecated: true` |
| `POST /project/{id}/upload` | `target=project_raw` | 仍不进入分析链路；分析请 `session` 或 copy-from-raw |
| `POST /resources/files/upload` | `target=resources_file` | |
| `POST /resources/datasets/upload` | `target=resources_dataset` | |
| `POST /resources/datasets/{id}/versions` | `target=resources_dataset_version` | |
| `POST /resources/models/upload` | `target=resources_model` | |
| `POST /psych/datasets/{id}/ingest` | `target=psych_ingest` | |
| `POST /workbench/session/upload` | **不迁移** | 继续整文件上传 |

旧整文件接口响应增加：

```json
{
  "deprecated": true,
  "notice": "整文件 multipart 上传已 deprecated，请改用 POST /upload/chunked/init 分片协议。详见 docs/ChunkedUploadFrontend.md。"
}
```

小文件过渡期仍可调用旧接口；新产品前端请统一走分片 SDK，避免后续移除旧入口时二次改造。

---

## 9. 上传完成后的配套 API（无需改动）

- 会话：`GET /session/workspace-tree`、`DELETE /session/workspace-file`
- 项目：`GET /project/{id}/tree`、`GET /project/{id}/assets`、`POST /session/copy-from-project-raw`
- 资源：`GET /resources/files/tree`、download/preview/delete 等（见 [`ResourceAPI.md`](ResourceAPI.md)）

---

## 10. 联调样例文件

仓库内可用于分片上传联调的较大样例（路径相对仓库根）：

| 文件 | 约大小 | 说明 |
|------|--------|------|
| [`tests/fixtures/table/large-dataset.csv`](../tests/fixtures/table/large-dataset.csv) | ~10MB | 大 CSV；建议 `chunk_size` 1–5MB，验证多片与断点续传 |
| [`tests/fixtures/imaging/患者CT.dcm`](../tests/fixtures/imaging/患者CT.dcm) | ~12MB | DICOM（中文文件名）；`target=session` / `resources_file` 等 |

更小的混合类型表：[`tests/fixtures/table/mixed-types.csv`](../tests/fixtures/table/mixed-types.csv)（~32KB）。样例说明见 [`Tests.md`](Tests.md)。
