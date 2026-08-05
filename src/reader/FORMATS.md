# Reader 多格式处理逻辑

本文说明工作区 Reader 如何识别文件格式、分发 Handler，以及对各类文件产出何种 digest。

## 1. 总览

Reader 通过 LangGraph 流水线扫描工作区并生成结构化摘要：

```
scan_workspace → process_files → merge_digest → synthesize_markdown
```

| 阶段 | 职责 |
|------|------|
| `scan_workspace` | 递归列举文件，经 `FormatRegistry.resolve` 得到 `file_type` |
| `process_files` | 对每个文件调用 `registry.dispatch_digest`，得到单文件 digest |
| `merge_digest` | 汇总为 `workspace_digest`（`files` + `summary`） |
| `synthesize_markdown` | 转为 Markdown；过长时用 `DEFAULT_READER_MODEL` 压缩 |

格式权威来源是 **FormatRegistry**（`registry/`），不是硬编码扩展名表。`file_types.py` 仅作薄封装，委托 Registry。

## 2. 格式识别（FormatRegistry）

### 2.1 匹配顺序

对给定文件名（可选 MIME / 文件头字节），`resolve()` 在**已启用**规则中匹配：

1. **扩展名**（小写，含点）
2. 扩展名未命中时，用 **MIME**（去掉 `;charset=...`）
3. 仍未命中时，用 **magic 前缀**（文件头 hex，如 PDF 的 `25504446`）

多条命中时排序：

1. **自定义规则优先于内置**（同 priority）
2. **priority 降序**

未匹配 → 类别 `binary`，handler `binary`。

### 2.2 类别与深度解析

| category | 是否深度解析 | 说明 |
|----------|--------------|------|
| `table` / `image` / `text` / `document` / `imaging` | 是 | 可上传白名单 |
| `binary` | 否 | 仅登记大小与说明 |

深度解析类别集合：`DEEP_PARSE_CATEGORIES`。上传接口只允许这些类别的扩展名。

### 2.3 规则来源

- **内置**：`registry/builtin_rules.py`
- **自定义**：`knowledge/format_rules.json`（可增删改；不可覆盖 `builtin.*` 的 format_id，但可用更高 priority 的自定义规则抢匹配，或 `set_builtin_enabled` 禁用内置）

### 2.4 内置规则一览

| format_id | 扩展名 | category | handler_id |
|-----------|--------|----------|------------|
| `builtin.table.xlsx` | `.xlsx` `.xls` | table | `table` |
| `builtin.table.csv` | `.csv` `.tsv` | table | `table` |
| `builtin.image.raster` | `.png` `.jpg` `.jpeg` `.gif` `.webp` `.bmp` | image | `image` |
| `builtin.text.plain` | `.txt` `.md` `.log` | text | `text` |
| `builtin.text.structured` | `.json` `.yaml` `.yml` `.xml` `.html` `.htm` | text | `text` |
| `builtin.document.pdf` | `.pdf` | document | `document_pdf` |
| `builtin.document.docx` | `.docx` | document | `document_docx` |
| `builtin.imaging.dicom` | `.dcm` `.dicom` | imaging | `imaging_dicom` |

## 3. 分发（dispatch_digest）

```
resolve(path) → handler_id / category
→ handlers[handler_id].digest(workspace_root, relative_path, **kwargs)
→ 补齐 file_type / relative_path / format_id / handler_id
```

- `image` handler 额外传入 `lang`（影响 OCR 提示与失败文案）
- 无对应 handler 时回退 `binary`
- 单文件异常由 `process_files` 捕获，写入 `errors`，并生成带 `error` 字段的 digest

## 4. 各类 Handler 处理逻辑

### 4.1 表格（`table`）— `handlers/table.py`

**输入**：`.xlsx` / `.xls` / `.csv` / `.tsv`

**读取**：

| 格式 | 方式 |
|------|------|
| `.csv` | `pd.read_csv`，分隔符 `,`；编码尝试 `utf-8` → `utf-8-sig` → `gbk` → `gb2312` → `latin-1` |
| `.tsv` | 同上，分隔符 `\t` |
| `.xlsx` | `pd.read_excel(..., engine="openpyxl")` |
| `.xls` | `pd.read_excel(..., engine="xlrd")` |

一律 `header=None` 读入整表，再决定表头。

**表头策略**：

1. 若 `READER_ENABLE_LLM_TABLE_HEADER` 开启：用 `DEFAULT_MODEL` 分析前若干行，得到 `data_start_row` 与 `column_headers`，修正 DataFrame；digest 含 `header_analysis`
2. 否则：第 0 行作表头（空/NaN → `Unnamed: i`），列名去重（`col` / `col_1`…），其余行作数据

**产出 digest 要点**：

- `columns`、`shape`、`sample_rows`（行数由 `READER_TABLE_SAMPLE_ROWS`）
- `pandas_info`、`read_hint`（下游如何用 pandas 复读）
- 失败时：`error` + 空 `columns`

**依赖**：`openpyxl`（xlsx）、`xlrd`（xls）；缺失时读表失败并记入 `error`。

**仓库样例**：`tests/fixtures/table/mixed-types.csv`、`tests/fixtures/table/large-dataset.csv`（首行可为 Sample-Files 风格注释；默认路径把第 0 行当表头，不按 `#` 跳过）。

---

### 4.2 图片（`image`）— `handlers/image.py`

**输入**：常见光栅图（png/jpeg/gif/webp/bmp）

**处理**：

1. Pillow 读宽高、`mode`、文件大小
2. 若配置了 `DEFAULT_VISION_MODEL`：整图 base64 + `SYSTEM_PROMPT_READER_VISION`，经视觉模型 OCR/描述 → `vision_description`
3. 未配置视觉模型：仅元数据，`vision_description` 说明未做 OCR

**产出**：`width` / `height` / `mode` / `file_size_bytes` / `vision_description`（或 `error`）

**依赖**：Pillow；视觉能力另需可用的 Vision API。

---

### 4.3 文本（`text`）— `handlers/text.py`

**输入**：纯文本与结构化文本（见上表）

**处理**：

1. 探测编码（同表格常见编码列表），读取预览，长度上限 `READER_TEXT_PREVIEW_CHARS`
2. 统计总行数；标记是否截断
3. 扩展名为 `json` 时额外轻量摘要：`json_type`、键列表/数组长度等（解析失败不影响 preview）

**产出**：`encoding`、`preview`、`line_count`、`preview_truncated`；JSON 另有结构字段

**说明**：yaml/xml/html 等与 txt 相同路径，不做专用 AST 解析，只做文本预览。

---

### 4.4 PDF（`document_pdf`）— `handlers/document_pdf.py`

**处理**：

1. 登记 `file_size_bytes`
2. 优先 `pypdf`，回退 `PyPDF2`；均缺失 → 仅元数据 + `note` 提示安装
3. 抽取页数、标题/作者；前最多 3 页文本拼成 `preview`（总长约 2000 字符）

**产出**：`page_count`、`preview`、`preview_truncated`、`title`、`author`、`parser`

---

### 4.5 DOCX（`document_docx`）— `handlers/document_docx.py`

**处理**：

1. 登记大小；无 `python-docx` → 仅元数据 + `note`
2. 抽取非空段落；表格转 `|` 分隔文本
3. 合并后截断为约 2000 字符 `preview`

**产出**：`paragraph_count`、`table_count`、`preview`、`preview_truncated`、`parser`

---

### 4.6 DICOM（`imaging_dicom`）— `handlers/imaging_dicom.py`

**处理**：

1. 登记大小；无 `pydicom` → 仅元数据 + `note`
2. `dcmread(..., stop_before_pixels=True, force=True)`，不读像素数据
3. 提取 PatientID/Name、StudyDate、Modality、Study/Series Description、Rows/Columns

**产出**：上述标签字段（字符串化）；不做像素级视觉描述

**仓库样例**：`tests/fixtures/imaging/患者CT.dcm`（中文文件名；`stop_before_pixels` 只读标签）。

---

### 4.7 回退（`binary`）— `handlers/fallback.py`

未注册或无法深度解析的类型：记录 `file_size_bytes` 与说明性 `note`，不解析内容。

## 5. 依赖降级策略

启动 Registry 时会调用 `deps.check_reader_parse_deps()`：缺库只打 **warning**，不阻断服务。

| 库 | 影响格式 | 缺库行为 |
|----|----------|----------|
| openpyxl / xlrd | xlsx / xls | digest 带 `error` |
| Pillow | image | 元数据失败 / `error` |
| pypdf 或 PyPDF2 | pdf | 仅大小 + `note` |
| python-docx | docx | 仅大小 + `note` |
| pydicom | dicom | 仅大小 + `note` |

## 6. Digest 汇总与 Markdown

- **merge**：`workspace_digest.files[relative_path] = digest`，`summary` 按类别计数
- **synthesize**：优先 `workspace_digest_to_markdown`；长度 > 12000 时尝试 LLM 压缩，失败则回退结构化 Markdown

单文件 digest 的通用字段约定：

| 字段 | 含义 |
|------|------|
| `file_type` | 类别 |
| `relative_path` | 工作区相对路径 |
| `format` / `format_id` | 扩展名或规则 id |
| `handler_id` | 实际 Handler |
| `error` / `note` | 失败或降级说明 |

## 7. 相关配置与扩展点

**配置（见 `configs` / 环境变量）**：

- `READER_ENABLE_LLM_TABLE_HEADER`、`READER_TABLE_SAMPLE_ROWS`
- `READER_TEXT_PREVIEW_CHARS`
- `DEFAULT_VISION_MODEL`、`DEFAULT_READER_MODEL`、`DEFAULT_MODEL`

**扩展新格式**：

1. 实现 `FormatHandler`（`handler_id` + `digest(...)`）并 `register_handler`
2. 在内置规则或 `knowledge/format_rules.json` 增加 `FormatRule`（extensions / mime / category / handler_id / priority）
3. 确保 category 属于 `DEEP_PARSE_CATEGORIES` 后即可进入上传白名单

## 8. 关键代码索引

| 模块 | 路径 |
|------|------|
| 流水线 | `graph.py`、`nodes/` |
| 注册与分发 | `registry/registry.py` |
| 内置规则 | `registry/builtin_rules.py` |
| Handlers | `handlers/*.py` |
| 入口 | `agent.py`（`run_workspace_reader*`） |
| 依赖探测 | `deps.py` |
