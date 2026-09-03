# SSE 详细说明（当前实现）

本文档汇总当前项目中所有与 SSE（Server-Sent Events）相关的后端实现、事件协议、持久化策略与前端消费方式，基于当前代码实际行为整理。

## 1. SSE 入口与接口定义

- 接口：`POST /run-analysis`
- 路由入口：`src/backend/server.py`
- 服务函数：`build_run_analysis_response`（`src/backend/route_services.py`）
- 返回类型：`StreamingResponse`
- `media_type`：`text/event-stream`
- 响应头：
  - `Cache-Control: no-cache`
  - `X-Accel-Buffering: no`（关闭 Nginx 缓冲，降低流式延迟）

请求体为 JSON：

```json
{
  "session_id": "your-session-id",
  "input_data": "你的分析需求"
}
```

接口前置校验：

- `session_id` 必须存在于会话映射中，否则返回 `404`
- 会话查询异常返回 `500`

## 2. SSE 事件帧格式

后端统一输出标准 SSE 文本帧：

```text
data: <json-string>

```

即每条事件由一行 `data: ...` 加一个空行组成（`\n\n` 结尾）。

在 `src/backend/analysis_stream.py` 中由 `_push_to_session` 统一生成该格式，同时先执行会话内容落库再向客户端推送。

## 3. 事件生产链路（后端）

### 3.1 总流程

`/run-analysis` -> `streaming_task_generator` -> `run_orchestrated_analysis_stream` -> LangGraph 各节点产出事件 -> 队列转发 -> SSE 输出。

流水线通过 `ensure_runtime(session_id)` 绑定执行层；Reader 直接读取 `workspaces/<user_id>/<session_id>/` 下的文件。流水线结束后调用 `release_runtime(session_id)`（沙箱模式下内部 pause VM）。

### 3.2 编排层事件来源

`src/orchestrator/analysis_pipeline_graph.py` 中实际产出的主要事件：

- `orchestrator`：Supervisor 决策事件
- `planner`：Planner 子流事件（`data` 为 planner 原始事件对象）
- `coder`：Coder 结果
- `worker`：Worker 执行结果
- `report_chunk`：Reporter 报告流式片段
- `error`：图执行结束但未正常完成报告阶段
- `streaming_error`：图执行异常（抛异常）

### 3.3 队列转发模型

`run_orchestrated_analysis_stream` 内部采用 `asyncio.Queue`：

- 各节点把事件 `put` 到队列
- 前台循环持续 `get` 并 `yield`
- 结束时写入哨兵 `_GRAPH_STREAM_END`
- 若终态异常/未正常完成，先入队终态事件（`error` 或 `streaming_error`）再结束

这保证了普通阶段事件和终态事件都通过同一通道输出，时序一致。

## 4. 各事件类型的字段结构

以下为当前代码中可确认的字段（其中 `data` 内部结构由对应模块决定，可能扩展）：

### 4.1 orchestrator

```json
{
  "type": "orchestrator",
  "data": {
    "next": "planner|coder|worker|reporter|finish",
    "reason": "路由原因",
    "feedback": "给下一阶段的反馈",
    "supervisor_invoke": 1,
    "timestamp": "YYYY-MM-DD HH:MM:SS"
  }
}
```

用途：展示编排决策过程（为什么跳到某阶段、当前第几次调度）。

### 4.2 planner

```json
{
  "type": "planner",
  "data": {
    "...": "来自 AgentPlanner.run_flow_with_workspace 的原始事件"
  }
}
```

用途：透传 Planner 过程事件，常见包含阶段进度与阶段结果。

### 4.3 coder

```json
{
  "type": "coder",
  "data": [
    {
      "...": "代码生成或修正写入结果对象"
    }
  ]
}
```

用途：返回代码写入结果（首次生成或基于 Worker 错误的修正）。

### 4.4 worker

```json
{
  "type": "worker",
  "data": {
    "success": true,
    "results": [],
    "logs": "",
    "error_messages": []
  }
}
```

用途：返回 Python 执行结果（经 `runtime.commands.run`）。默认本地 Runtime 下由 **`RUNNER_PYTHON`**（`.env` 配置的解释器，依赖见 `requirements-runner.txt`）执行，与 FastAPI 主环境隔离；`CUBE_SANDBOX_ENABLED=1` 时走 Cube 沙箱内 Python。供前端展示和后续编排判断。

### 4.5 report_chunk

```json
{
  "type": "report_chunk",
  "content": "报告增量文本"
}
```

用途：报告正文流式增量输出，前端通常拼接 `content` 形成最终报告。

### 4.6 error

```json
{
  "type": "error",
  "message": "流水线未正常完成报告阶段（可能规划/代码多次失败或达到上限）",
  "timestamp": "YYYY-MM-DD HH:MM:SS"
}
```

触发条件：图执行完成，但 `reporter_done` 为 false。

### 4.7 streaming_error

```json
{
  "type": "streaming_error",
  "error": "异常信息",
  "timestamp": "YYYY-MM-DD HH:MM:SS"
}
```

触发条件：

- 编排图执行抛异常（`run_orchestrated_analysis_stream` 内）
- 或更上层 `streaming_task_generator` 捕获到异常

## 5. 结束语义（非常关键）

当前有两层“结束”信号：

1. 编排层可能产出终态错误事件：`error` 或 `streaming_error`
2. 若 `streaming_task_generator` 判断全过程 `ended_ok=True`，会额外补发：

```json
{
  "type": "streaming_ended",
  "message": "分析任务流式输出结束",
  "timestamp": "YYYY-MM-DD HH:MM:SS"
}
```

`ended_ok` 会在收到 `type=error` 或 `type=streaming_error` 时被置为 `False`，此时不会发送 `streaming_ended`。

建议前端按以下策略判断结束：

- 收到 `streaming_ended`：正常结束
- 收到 `error` / `streaming_error`：异常结束
- HTTP 连接断开但无终态事件：视为中断，建议走快照恢复

## 6. 会话持久化与 SSE 的关系

每条即将发送的 payload 都会先落库，再推送：

- 位置：`_push_to_session`（`src/backend/analysis_stream.py`）
- 行为：
  1. `json.dumps(payload, ensure_ascii=False)` 得到 JSON 片段
  2. 追加写入 `SessionStore.append_content(session_id, fragment + "\n")`
  3. 返回 SSE 文本帧 `data: ...\n\n`

这意味着：

- `/session/snapshot` 可拿到累计内容 + 当前版本，用于断线恢复
- 内容是“累计文本”模式（持续 append），不是独立事件表结构
- 即使某些异常出现，已推送前的事件通常已被记录

## 7. 前端消费方式（当前实现）

[`web/src/api/analysis.ts`](../web/src/api/analysis.ts) 的流式读取方式：

- 使用 `fetch("POST", "/run-analysis", { body: JSON })` 获取 `ReadableStream`
- 循环读取并按行解析 SSE `data: {...}` JSON
- 遇到 `type=report_chunk` 时追加 `content` 到报告正文
- 其他 `type` 写入编排时间线供调试展示

详见 [`Frontend.md`](Frontend.md)。

## 8. 联调建议与注意事项

- 客户端必须容忍非 `report_chunk` 事件，并按 `type` 分流展示
- 遇到 `error`/`streaming_error` 时应停止拼接报告并提示失败原因
- 建议保留原始事件列表，便于排查 Supervisor 路由和失败回溯
- 若部署在反向代理后，确保禁用响应缓冲（当前已设置 `X-Accel-Buffering: no`，代理侧也需对应配置）
- 长链接场景下可配合 `/session/snapshot` 做断线重连恢复
- 执行 Runtime 与可选 Cube Sandbox 见项目 README 与 `src/runtime/` 文档

## 9. 一次完整流的典型事件顺序（示例）

```text
orchestrator -> planner -> orchestrator -> coder -> orchestrator -> worker -> orchestrator -> report_chunk* -> streaming_ended
```

异常路径示例：

```text
... -> orchestrator -> coder -> orchestrator -> error
```

或

```text
... -> streaming_error
```

