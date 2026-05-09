# 智能体会话记忆（Session Memory Markdown）设计说明

本文档面向 **AgentPlatform** 多智能体流水线（Supervisor → Planner / Coder / Worker / Reporter），说明「每个 `session_id` 维护一份 Markdown 记忆文件」时应包含哪些**内容项目**，以及这些项目与现有实现之间的对应关系。实现时可位于会话工作区根目录（例如 `SESSION_MEMORY.md`）或由服务侧统一落盘；本文只规定**语义与结构**，不绑定具体文件名或写入时机。

---

## 1. 背景与要解决的两个问题

### 1.1 Prompt 过长与跨智能体重复传递

当前编排中，各子智能体的输入并非完全独立，而是层层叠加、裁剪：

| 阶段 | 主要输入来源（与代码一致的方向） |
|------|----------------------------------|
| **Supervisor** | 用户原始需求、`supervisor_feedback`、状态摘要 hint、`worker_error_excerpt`（stderr 等截断摘要） |
| **Planner** | 用户需求 + **编排反馈**（`append_orchestrator_feedback`）、以及工作区导出的 **Excel/CSV 结构化说明**（`file_info`） |
| **Coder** | `planner_summary` / `requirement_analysis` / `steps_outline`、**workspace_context**（文件列表 + Excel schema 采样）、Supervisor 反馈（写入步骤分解等）、修正模式下 **Worker 错误摘要** |
| **Reporter** | `planner_summary`、结构化 **worker_results**（成功与否、各文件 stdout/stderr 等） |

若没有会话级「单一事实来源」，同一事实（例如「当前规划要点」「最近一次执行失败原因」）会在多轮提示词中反复全文拷贝，既浪费窗口又容易不一致。

**记忆 Markdown 的定位**：存放经压缩、分层后的**稳定摘要**与**指针**（指向工作区路径、关键文件名），各智能体优先读记忆 + 少量增量，而不是每次都拼接完整历史。

### 1.2 工作区随时间变化，多轮对话难以感知「当下」

系统特性包括：

- 每个会话有独立工作区目录（如 `TEMP_FOLDER/workspaces/<session_id>`），上传文件会规范命名为 `data.xlsx`、`data_1.csv` 等。
- Planner/Coder 侧通过 `list_workspace_files`、`read_workspace_excel_schema_and_sample` 等构建 **`workspace_context`**，但这是在**某次图运行**内构建的；用户在中途上传新文件、Coder 写入新脚本、Worker 产生新日志后，若没有统一的「工作区现状」记录，后续轮次模型容易依据**过时清单**推理。

**记忆 Markdown 的定位**：在关键事件后刷新「工作区目录清单 + 数据文件角色 + 生成代码文件 + 最近执行结果摘要」，使多轮对话中的「当前画面」可检索、可对齐。

---

## 2. 与现有流水线状态的对应关系（便于落地时字段对齐）

下列字段来自编排图状态 `PipelineState` 及各节点行为（见 `src/orchestrator/analysis_pipeline_graph.py`），记忆文档中的条目应能**还原或索引**这些信息，而不必逐字复制全文长文本。

| 记忆文档应覆盖的概念 | 流水线中的来源 |
|---------------------|----------------|
| 会话标识与工作区根路径 | `session_id`、`SessionStore.get_workspace_path` / `resolve_workspace_root` |
| 用户任务表述与编排反馈 | `input_data`、`supervisor_feedback`、`append_orchestrator_feedback` |
| 规划产物 | `plan_data`（需求解析、步骤分解、规划全文）、`planner_summary` |
| 工作区文件与表格 schema | `workspace_context["file_list"]`、`workspace_context["excel_schema"]` |
| 代码写入结果 | `coder_results`、`code_file_paths`、`correction_attempts` |
| 执行结果 | `worker_results`（success、各文件 stdout/stderr、error_messages） |
| 监督决策 | `orchestrator` 事件中的 `next`、`reason`、`feedback` |
| 报告是否完成 | `reporter_done` |

此外，SSE 持久化到 MySQL 的 `session_content` 是**逐事件 JSON 行**的完整流水；记忆 Markdown 应是其**语义压缩视图**，二者互补而非互相替代。

---

## 3. 记忆 Markdown 建议包含的内容项目

以下为推荐章节结构。可根据产品形态合并或改名，但**语义建议保留**。

### 3.1 会话元数据（Session Meta）

- **session_id**：与会话唯一绑定。
- **工作区绝对路径或约定路径说明**：便于运维与人类阅读；模型侧仍以服务端解析为准。
- **语言 / 分析输出语言**：与 `lang`、`LANGUAGE` 配置一致。
- **记忆版本或最后更新时间**：支持并发写入时的合并策略（见第 4 节）。
- **可选：会话标题**：若业务写入 `session_user.title`。

### 3.2 用户目标与需求演进（User Intent）

- **当前生效的用户需求表述**：在多轮对话中可维护「合并后的 canonical 表述」，避免仅保留最后一句话导致丢失约束。
- **本轮相对上一轮的增量**：用户新补充的约束、否定的方向、优先级变化（短列表即可）。
- **与 Planner 对齐的「编排反馈」摘要**：对应 Supervisor 传给 Planner 的反馈类信息，但记忆侧宜 **二次摘要**（见第 5 节长度策略）。

### 3.3 工作区清单与文件角色（Workspace Inventory）

与 `list_workspace_files` 及实际上传/生成行为对齐：

- **根目录文件列表**（仅文件名即可，必要时标注类型：数据 / 代码 / 日志 / 其他）。
- **数据文件**：规范化名称（`data.*`、`data_N.*`）与**原始意图**（若可从上传记录得知）。
- **代码文件**：默认 `main.py` 及 `code_file_paths` 中的路径；注明最后一次已知写入是否成功（来自 `coder_results` 摘要）。
- **可选：子目录约定**：若未来扩展子目录结构，在此声明当前约定，避免模型假设错误目录。

### 3.4 数据与 Schema 摘要（Data & Schema Digest）

对应 Planner/Coder 使用的 **Excel/CSV 结构化说明**，记忆层建议：

- **每张关键表的用途一句话**（业务语义）。
- **列名与类型/采样行的极度压缩版**（禁止把超大采样无损贴入记忆；可用「hash / 行数 / 列数」代替细节）。
- **已知数据质量问题**：缺失值、异常 sheet、编码问题等（来自历史 Worker stderr 或人工备注）。

### 3.5 规划状态（Planning Snapshot）

对应 `plan_data` / `planner_summary`：

- **需求解析（短摘要）**：保留约束与指标，删除赘述。
- **步骤分解（结构化列表）**：有序步骤，便于 Coder/Reporter 对齐。
- **规划全文或外部引用**：若正文过长，记忆中只保留摘要 + 「详见工作区某文件」若业务选择另存规划文件。

### 3.6 代码与修正状态（Code & Corrections）

对应 Coder 节点与 `correction_attempts`：

- **目标入口文件**（如 `main.py`）。
- **生成 vs 修正模式**最近一次为何（Worker 失败触发修正 / Supervisor 要求重做等）。
- **修正轮次与剩余额度提示**（仅摘要级别：如「已修正 2/5 次」），与配置 `MAX_CODER_CORRECTIONS` 对齐语义。
- **最近一次写入失败原因**（短）：磁盘、权限、模型未输出等（来自 `coder_results`）。

### 3.7 执行履历与错误账本（Execution Ledger）

对应 `worker_results` 及 `_worker_error_text` 的设计意图（供下一轮 Coder 消费）：

- **最近一次执行是否整体 success**。
- **按文件的 returncode、stdout 要点、stderr 摘要**：长度必须有上限（代码中错误摘要已有截断思路，记忆应继承）。
- **超时、工作区不存在、脚本缺失**等基础设施级错误的单独记录。
- **累计失败主题**：例如「pandas 版本」「路径硬编码」等从多次 stderr 抽象的标签（可选，利于跨轮 avoid）。

### 3.8 编排决策摘要（Orchestration Trace，紧凑）

对应 SSE `type=orchestrator` 与 `_clamp_route` 的**结果**，而非完整对话：

- **最近一次 next_route 与钳制后的 reason（一句话）**。
- **给下一阶段的 feedback 摘要**（尤其是给 Planner/Coder 的关键一句）。
- **Supervisor 调用次数 / 是否触发强制 Reporter** 等与上限相关的状态（与 `MAX_SUPERVISOR_INVOCATIONS` 等配置语义对齐即可）。

避免将整个编排历史逐条写入记忆；建议只保留**最近 N 次**或**滚动窗口**。

### 3.9 报告与结论占位（Reporting）

对应 `stream_report` 产物：

- **若报告已流式结束**：可存「最终结论」短摘要或指向持久化报告路径（若产品将报告另存为文件）。
- **若未完成**：标记「报告未生成 / 因流水线中断未完成」，避免后续智能体误以为已有结论。

### 3.10 开放问题与假设（Open Issues & Assumptions）

集中存放尚未验证的假设、待用户确认的问题、与数据定义冲突之处。此项能显著减轻「在长 prompt 里重复提醒」的负担。

### 3.11 多轮时间线（可选，轻量）

极短条目：**时间戳 + 事件类型**（上传、新代码写入、执行失败、规划更新），用于调试与审计；不必复制 SSE 全量内容。

---

## 4. 维护策略建议

### 4.1 何时更新

建议在以下事件后刷新或追加记忆（具体由实现决定）：

- 会话创建 / 工作区初始化；
- 文件上传成功；
- Planner 产出有效规划；
- Coder 写入或修正完成；
- Worker 执行完成（无论成败）；
- Reporter 结束或 SSE 终态（`streaming_ended` / `streaming_error`）；
- 用户发送新一轮 `input_data` 触发分析前（可先合并「当前工作区快照」）。

### 4.2 整文件刷新 vs 分区追加

- **分区模板**：固定章节标题（如「## 工作区清单」），每类信息集中更新，便于模型检索。
- **冲突处理**：以服务端权威状态为准（工作区真实列表、数据库中的 session 元数据），记忆滞后时用下一次扫描覆盖对应章节。

### 4.3 与 `session_content`（MySQL）的分工

| 存储 | 角色 |
|------|------|
| `session_content` | 面向前端恢复与审计的 **完整 SSE JSON 行** |
| 记忆 Markdown | 面向智能体的 **压缩认知模型**：当前任务、工作区、规划与错误账本 |

实现上可从 `session_content` **派生**记忆更新，但记忆正文不应等同于日志堆砌。

---

## 5. 长度控制与隐私

- **默认对各节设置最大字符或「一览表 + 引用」策略**：长 stdout、完整 schema、完整代码不应常驻记忆正文；可改为「摘要 + 工作区内文件名引用」。
- **Secrets**：API Key、数据库密码等不得写入会话记忆；若 stderr 可能含敏感信息，应脱敏后再入记忆。
- **与用户可见报告一致**：若记忆中含结论性语句，宜与 Reporter 输出可追溯对齐，避免「记忆与展示不一致」。

---

## 6. 小结：记忆文档的最小必备集合（MVP）

若第一版只能实现部分章节，建议优先保证以下项目 **齐全且随运行刷新**：

1. **session_id + 工作区路径（或等价定位信息）**  
2. **当前用户任务 / 约束摘要**  
3. **工作区文件清单 + 数据文件角色**  
4. **（若有）规划：需求解析 + 步骤分解的短版本**  
5. **代码入口文件与最近一次写入/修正状态**  
6. **最近一次 Worker 整体结果 + 分文件错误/输出摘要（有界长度）**  
7. **最近一次 Supervisor 决策要点（next / reason / 给下游的关键反馈）**  
8. **开放问题与待确认项（若有）**  

在此基础上再扩展第 3.10、3.11 节与更细的执行账本，可进一步提升多轮与跨智能体一致性。

---

## 7. 参考代码位置（AgentPlatform 仓库）

- 顶层编排与状态：`src/orchestrator/analysis_pipeline_graph.py`（`PipelineState`、各 `_*_node`）  
- 会话与工作区持久化：`src/db/session_store.py`、`src/utils/workspace_manager.py`  
- 流式持久化与重连：`src/backend/analysis_stream.py`  
- 产品级流程说明：`README.md`  

本文档仅描述设计期望；具体文件名、写入 API、与编排图的钩子位置需在实现任务中单独落地。
