# 2.1.3 数据质量控制可视化前端对接说明

本文档面向 AgentPlatform 前端维护者，说明如何在现有系统中接入以下三项能力：

1. 自动化数据质量评估与质控报告；
2. 医疗敏感信息识别、脱敏与匿名化；
3. 患者诊疗轨迹时序化全景与多模态联动。

模块已经直接挂载到 AgentPlatform 主后端，**不需要新增端口或单独启动进程**。

---

## 1. 集成结论

| 项目 | 结论 |
| --- | --- |
| API Base | `http://<服务器地址>:52716` |
| 联调页面 | `http://<服务器地址>:52716/dq213-app` |
| 静态资源 | `/static/dq213/*` |
| 鉴权 | 复用 AgentPlatform JWT，`Authorization: Bearer <access_token>` |
| 用户隔离 | 后端从 JWT 获取 `user_id`，前端不能指定 `owner_user_id` |
| 是否新增端口 | 否，全部复用 `52716` |
| 是否新增常驻进程 | 否，跟随 AgentPlatform 后端进程常驻 |
| 演示写库接口 | 生产融合未注册 `/dq213/demo/seed` |

推荐前端将三项功能做成一个“数据质量控制”一级页面，页面内使用三个页签：

- `质量评估`
- `脱敏匿名化`
- `诊疗轨迹`

现有 `/dq213-app` 是可直接使用的同源联调页，也可以作为正式前端开发时的请求和交互参考。

---

## 2. 鉴权与公共约定

### 2.1 获取 Token

正式环境沿用主系统短信登录流程：

```http
POST /auth/send-sms-code
Content-Type: application/json

{
  "phone": "13800000000"
}
```

```http
POST /auth/login-with-sms
Content-Type: application/json

{
  "phone": "13800000000",
  "code": "用户收到的短信验证码"
}
```

登录成功响应：

```json
{
  "code": 0,
  "msg": "login success",
  "data": {
    "access_token": "<JWT>",
    "token_type": "bearer",
    "expires_in": 604800,
    "user_id": 1,
    "username": "用户名称",
    "phone": "13800000000"
  }
}
```

产品前端建议继续使用主系统已有的 `agent_access_token`。同源联调页也会自动读取：

```js
const token = localStorage.getItem("agent_access_token");
```

### 2.2 请求头

除登录接口外，本模块所有 `/dq213/*` API 均要求：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

报告下载接口不需要 `Content-Type`，但仍需要 `Authorization`。

### 2.3 成功响应

本模块 JSON API 统一返回：

```json
{
  "status": "success",
  "data": {}
}
```

前端通常直接读取 `payload.data`。

### 2.4 错误响应

常见错误形状：

```json
{
  "detail": "patient_id 必填"
}
```

鉴权失败可能返回：

```json
{
  "detail": {
    "code": 6,
    "msg": "unauthorized"
  }
}
```

| HTTP 状态码 | 含义 | 前端处理建议 |
| --- | --- | --- |
| `400` | 请求字段、筛选条件或数据格式不合法 | 显示 `detail`，保留用户输入 |
| `401` | 未登录、Token 失效 | 清除旧 Token 并跳转登录 |
| `403` | 无权限或服务端路径访问被禁止 | 不重试，提示权限问题 |
| `404` | 报告不存在或已清理 | 提示重新生成报告 |
| `422` | FastAPI 参数校验失败 | 检查 query/body 类型 |
| `500` | 模块内部处理异常 | 展示通用错误并记录请求 ID |
| `503` | 数据库、密钥或依赖未准备好 | 提示稍后重试并联系后端 |

推荐的请求封装：

```js
const API_BASE = ""; // 同源部署留空；跨域时填 http://host:52716

async function dq213Request(method, path, body) {
  const token = localStorage.getItem("agent_access_token") || "";
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body == null ? {} : { "Content-Type": "application/json" }),
      Authorization: `Bearer ${token}`,
    },
    body: body == null ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof payload.detail === "string"
        ? payload.detail
        : payload.detail?.msg || `HTTP ${response.status}`
    );
  }
  return payload.data;
}
```

---

## 3. API 总览

| 功能 | 方法 | 路径 |
| --- | --- | --- |
| 模块健康检查 | `GET` | `/dq213/health` |
| 质控维度定义 | `GET` | `/dq213/qc/dimensions` |
| 执行质量评估 | `POST` | `/dq213/qc/assess` |
| 下载质控报告 | `GET` | `/dq213/qc/reports/{report_id}` |
| 识别文本隐私信息 | `POST` | `/dq213/phi/detect` |
| 文本或结构化数据脱敏 | `POST` | `/dq213/phi/anonymize` |
| 获取脱敏静态样例 | `POST` | `/dq213/phi/demo` |
| 获取当前用户患者列表 | `GET` | `/dq213/timeline/patients` |
| 查询单患者时间线 | `GET` | `/dq213/timeline/{patient_id}` |
| 组合条件查询时间线 | `POST` | `/dq213/timeline/query` |

> 生产环境**没有** `/dq213/demo/seed`。前端不要调用或展示“写入演示数据”按钮。

---

## 4. 自动化数据质量评估

### 4.1 获取评估维度

```http
GET /dq213/qc/dimensions
Authorization: Bearer <access_token>
```

返回维度包括：完整性、一致性、准确性、异常值、非结构化文本、多模态质量、临床多类型覆盖。

### 4.2 评估前端传入数据

```http
POST /dq213/qc/assess
Authorization: Bearer <access_token>
Content-Type: application/json
```

综合请求示例：

```json
{
  "rows": [
    {
      "patient_id": "P-001",
      "age": 32,
      "gender": "女",
      "diagnosis": "抑郁障碍",
      "admission_date": "2026-01-03",
      "discharge_date": "2026-01-18",
      "HAMD_total": 18,
      "HAMA_total": 12,
      "PHQ9_total": 15,
      "disease_duration_years": 2.5,
      "relapse": 0
    }
  ],
  "unstructured_rows": [
    {
      "content": "患者睡眠较前改善，继续当前治疗方案。"
    }
  ],
  "multimodal_items": [
    {
      "asset_id": "IMG-001",
      "modality": "image",
      "mime_type": "image/png",
      "uri": "/medical-assets/IMG-001.png",
      "size_bytes": 204800,
      "checksum": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "export": true
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `rows` | 与数据库模式二选一 | 结构化对象数组，最多 `20000` 行、`512` 个字段 |
| `unstructured_rows` | 否 | 文本对象数组或字符串数组，最多 `10000` 条 |
| `multimodal_items` | 否 | 多模态元数据数组，最多 `10000` 条 |
| `export` | 否 | `true` 时生成可下载 JSON 报告并返回 `report_id` |

支持的多模态类型：`image`、`audio`、`video`、`document`、`pdf`、`dicom`、`waveform`。

### 4.3 评估当前用户数据库数据

不传 `rows` 时，后端自动评估当前登录用户拥有的患者数据：

```json
{
  "limit": 5000,
  "note_limit": 200,
  "export": true
}
```

后端只查询 JWT 当前用户的 `owner_user_id` 数据。前端不要传 `owner_user_id`。

### 4.4 评估响应

```json
{
  "status": "success",
  "data": {
    "ok": true,
    "report_version": "2.1.3-safe-1",
    "source": "inline",
    "n_rows": 12,
    "n_cols": 13,
    "health_score": 76.42,
    "health_label": "Good",
    "core_metrics": {
      "missing_rate": 0.0128,
      "field_anomaly_rate": 0.0833,
      "outlier_rate": 0.0139,
      "unstructured_issue_rate": 0.25,
      "multimodal_issue_rate": 0.3333,
      "multimodal_coverage": 0.2857,
      "multitype_coverage": 0.5714
    },
    "dimensions": {
      "completeness": {},
      "consistency": {},
      "accuracy": {},
      "outlier": {},
      "unstructured": {},
      "multimodal": {},
      "multitype_coverage": {}
    },
    "field_anomaly_rates": [
      {
        "field": "age",
        "anomaly_rate": 0.0833
      }
    ],
    "checked_at": "2026-08-01T16:00:00",
    "report_id": "0123456789abcdef0123456789abcdef"
  }
}
```

推荐核心卡片直接展示：

- `health_score`
- `core_metrics.missing_rate`
- `core_metrics.field_anomaly_rate`
- `core_metrics.outlier_rate`
- `core_metrics.unstructured_issue_rate`
- `core_metrics.multimodal_issue_rate`

### 4.5 下载报告

```http
GET /dq213/qc/reports/{report_id}
Authorization: Bearer <access_token>
```

返回 `application/json` 文件。报告编号和当前用户绑定，其他用户不能下载。

前端下载示例：

```js
const response = await fetch(`/dq213/qc/reports/${encodeURIComponent(reportId)}`, {
  headers: { Authorization: `Bearer ${token}` },
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const link = document.createElement("a");
link.href = url;
link.download = `dq213-qc-${reportId}.json`;
link.click();
URL.revokeObjectURL(url);
```

### 4.6 CSV 服务端路径

接口支持 `csv_path`，但正式环境默认关闭，避免任意文件读取。前端优先上传并解析为 `rows`，不要直接传服务器文件路径。

---

## 5. 敏感信息识别与匿名化

### 5.1 识别文本隐私信息

```http
POST /dq213/phi/detect
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "text": "患者张伟，身份证110101199001011234，手机13812345678。",
  "include_values": false
}
```

响应示例：

```json
{
  "status": "success",
  "data": {
    "n_entities": 3,
    "by_type": {
      "NAME": 1,
      "ID_CARD": 1,
      "PHONE": 1
    },
    "entities": [
      {
        "type": "PHONE",
        "start": 26,
        "end": 37,
        "replacement": "[手机号]"
      }
    ]
  }
}
```

`include_values=true` 会在结果中包含原始隐私片段，只适合受控人工复核页面。一般产品页面应保持 `false`。

### 5.2 文本脱敏

```json
{
  "text": "患者张伟，身份证110101199001011234，手机13812345678。",
  "mode": "replace"
}
```

支持三种模式：

| 模式 | 行为 | 使用建议 |
| --- | --- | --- |
| `replace` | 替换为 `[姓名]`、`[身份证]`、`[手机号]` 等标签 | 默认推荐 |
| `annotate` | 保留原文并添加实体标注 | 仅限人工复核，仍含原始隐私 |
| `redact` | 将命中内容替换为遮盖字符 | 对外导出场景 |

请求：

```http
POST /dq213/phi/anonymize
Authorization: Bearer <access_token>
Content-Type: application/json
```

响应的主要字段：

```json
{
  "status": "success",
  "data": {
    "mode": "replace",
    "anonymized": "患者[姓名]，身份证[身份证]，手机[手机号]。",
    "detection": {
      "n_entities": 3,
      "by_type": {
        "NAME": 1,
        "ID_CARD": 1,
        "PHONE": 1
      },
      "entities": []
    }
  }
}
```

### 5.3 结构化数据匿名化

```json
{
  "rows": [
    {
      "patient_id": "P-001",
      "patient_name": "张伟",
      "phone": "13812345678",
      "id_card": "110101199001011234",
      "email": "demo@hospital.org",
      "address": "北京市海淀区某路1号",
      "note": "患者张伟联系电话13812345678。"
    }
  ]
}
```

结构化字段支持嵌套对象和数组。主要规则：

- `patient_id`、`patient_key`、`medical_record_number`、`mrn`：服务端 HMAC-SHA256 稳定伪名；
- 姓名、手机号、身份证、邮箱、出生日期：保留必要格式的掩码；
- 地址：完全移除；
- `content`、`note`、`notes`、`text`、`description`、`report_text`、`conclusion`：执行文本 PHI 识别和替换。

响应：

```json
{
  "status": "success",
  "data": {
    "rows": [
      {
        "patient_id": "PID_0123456789abcdef",
        "patient_name": "张*",
        "phone": "138****5678",
        "id_card": "****************34",
        "email": "d***@hospital.org",
        "address": "[已移除]",
        "note": "患者[姓名]联系电话[手机号]。"
      }
    ],
    "n_rows": 1,
    "n_field_ops": 7,
    "processed_at": "2026-08-01T16:00:00"
  }
}
```

客户端不得传 `salt` 或 `secret`，否则返回 `400`。伪名密钥只保存在后端环境变量中。

---

## 6. 患者诊疗轨迹

### 6.1 获取患者列表

```http
GET /dq213/timeline/patients?limit=80
Authorization: Bearer <access_token>
```

```json
{
  "status": "success",
  "data": {
    "ok": true,
    "items": [
      {
        "patient_id": "P-001",
        "diagnosis": "抑郁障碍",
        "admission_date": "2026-01-03"
      }
    ],
    "limit": 80
  }
}
```

只返回当前 JWT 用户拥有的患者。

### 6.2 GET 查询时间线

```http
GET /dq213/timeline/P-001?types=diagnosis,medication,lab&start_date=2026-01-01&end_date=2026-12-31&modalities=image,pdf&keyword=MRI&limit=500
Authorization: Bearer <access_token>
```

Query 参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `types` | 逗号分隔字符串 | 事件类型；不传表示全部 |
| `start_date` | `YYYY-MM-DD` | 开始日期 |
| `end_date` | `YYYY-MM-DD` | 结束日期 |
| `modalities` | 逗号分隔字符串 | 多模态类型筛选 |
| `keyword` | 字符串 | 在事件标题和详情中搜索，最长 200 字符 |
| `limit` | 整数 | 返回事件数，最大 1000 |

### 6.3 POST 组合查询

复杂筛选推荐使用：

```http
POST /dq213/timeline/query
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "patient_id": "P-001",
  "event_types": [
    "diagnosis",
    "admission",
    "discharge",
    "medication",
    "examination",
    "lab",
    "assessment",
    "clinical_note",
    "followup"
  ],
  "start_date": "2026-01-01",
  "end_date": "2026-12-31",
  "modalities": [],
  "keyword": null,
  "limit": 500
}
```

支持事件类型：

| 值 | 中文 |
| --- | --- |
| `diagnosis` | 诊断 |
| `admission` | 入院 |
| `discharge` | 出院 |
| `medication` | 用药 |
| `examination` | 检查 |
| `lab` | 检验 |
| `assessment` | 量表评估 |
| `clinical_note` | 病历文本 |
| `followup` | 随访 |

### 6.4 时间线响应

```json
{
  "status": "success",
  "data": {
    "ok": true,
    "patient_id": "P-001",
    "n_events": 8,
    "n_assets": 2,
    "linked_assets": 2,
    "by_type": {
      "diagnosis": 1,
      "medication": 2,
      "examination": 1,
      "lab": 2,
      "followup": 2
    },
    "by_modality": {
      "structured": 3,
      "image": 1,
      "lab": 2,
      "scale": 2
    },
    "date_range": {
      "start": "2026-01-03",
      "end": "2026-07-15"
    },
    "filters": {},
    "warnings": [],
    "events": [
      {
        "event_type": "examination",
        "event_date": "2026-02-06",
        "title": "检查：头颅 MRI",
        "detail": {
          "exam_type": "头颅 MRI",
          "body_site": "脑",
          "finding": "未见明显异常",
          "conclusion": "随访观察"
        },
        "modality": "image",
        "source_table": "mental_health_examinations",
        "source_id": 12,
        "assets": [
          {
            "asset_id": 21,
            "title": "MRI 影像",
            "modality": "image",
            "mime_type": "image/png",
            "uri": "/medical-assets/P-001/mri.png",
            "thumbnail_uri": "/medical-assets/P-001/mri-thumb.png",
            "size_bytes": 204800,
            "checksum": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "metadata": {
              "series": "T1"
            }
          }
        ]
      }
    ],
    "checked_at": "2026-08-01T16:00:00"
  }
}
```

多模态附件安全约定：

- `uri` 和 `thumbnail_uri` 只会返回站内绝对路径，或合法 `http/https` URL；
- 前端图片建议使用 `thumbnail_uri || uri`；
- 附件链接使用 `target="_blank"` 时同时设置 `rel="noopener noreferrer"`；
- 不要把 `title`、`detail`、`metadata` 作为 HTML 直接插入，使用文本渲染避免 XSS。

---

## 7. 推荐页面调用流程

### 7.1 质量评估页签

1. 页面加载时请求 `GET /dq213/qc/dimensions`；
2. 用户选择“前端数据”或“当前用户数据库”；
3. 请求 `POST /dq213/qc/assess`；
4. 展示核心指标卡、维度分数、字段异常率 Top 列表；
5. 若存在 `report_id`，显示“下载报告”按钮。

### 7.2 脱敏匿名化页签

1. 用户选择文本或结构化 JSON；
2. 文本人工复核时可先调用 `/dq213/phi/detect`；
3. 调用 `/dq213/phi/anonymize` 生成安全结果；
4. `annotate` 模式明显提示“结果仍含原始隐私”；
5. 导出、复制或进入下游分析时默认使用 `replace` 或 `redact`。

### 7.3 诊疗轨迹页签

1. 请求 `/dq213/timeline/patients` 填充患者选择框；
2. 默认勾选全部事件类型；
3. 用户调整日期、事件类型、模态、关键词；
4. 请求 `/dq213/timeline/query`；
5. 按 `event_date` 绘制时间轴；
6. 同一事件下展开 `assets`，实现影像、报告和事件详情联动。

---

## 8. 数据库对接约定

时间线会读取以下表：

```text
mental_health_patients
mental_health_clinical_notes
mental_health_assessments
mental_health_med_orders
mental_health_examinations
mental_health_lab_reports
mental_health_followups
mental_health_multimodal_assets
```

所有子表均按以下条件隔离：

```sql
WHERE patient_id = ? AND owner_user_id = 当前JWT用户ID
```

业务侧写入这些表时必须同时写入正确的 `owner_user_id`。旧数据如果没有明确归属，后端不会自动展示给任意用户，也不会根据前端参数强行归属。

多模态附件与事件联动依赖：

- `event_source_table`：例如 `mental_health_examinations`；
- `event_source_id`：对应事件表主键；
- 两者为空时，附件会作为该患者的未关联附件处理。

---

## 9. 安全与上线检查

- 正式环境保持 `ACCEPTANCE_MODE=0`，不要依赖固定验证码；
- 不要将 `DQ213_PSEUDONYM_SECRET` 或密钥文件内容下发前端、写入前端仓库或接口日志；
- 前端请求不得包含 `secret`、`salt`、`owner_user_id`；
- `annotate` 模式结果仍包含原始隐私，不允许直接外发；
- `csv_path` 默认禁用，优先使用 `rows`；
- 报告目录是后端私有目录，不应由静态文件服务器直接暴露；
- 页面展示所有病历文本、附件标题和 JSON 内容时使用 `textContent` 或框架默认转义；
- 正式融合未注册 `/dq213/demo/seed`；
- 如使用跨域前端，需由网关限制允许源，不建议长期使用任意来源 CORS。

---

## 10. 启停与检查

本模块跟随 AgentPlatform 后端一起运行：

```bash
cd /data1/pjw/AgentPlatform
bash scripts/start.sh
```

关闭终端后服务仍保持运行。状态和日志：

```bash
bash scripts/status.sh
tail -f tmp/logs/backend.log
```

停止后端：

```bash
bash scripts/stop.sh
```

联调检查地址：

```text
http://<服务器地址>:52716/health
http://<服务器地址>:52716/dq213-app
http://<服务器地址>:52716/docs
```
