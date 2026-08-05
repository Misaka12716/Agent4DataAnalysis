# 对话数据分析实验素材

本目录存放**外部导入**的测试素材，供 [`scripts/run_conversation_analysis_experiment.py`](../../../scripts/run_conversation_analysis_experiment.py) 对比单文件 vs 多文件组合下 `POST /run-analysis` 的输出效果。

真实数据文件默认被 `.gitignore` 忽略；仓库只保留目录约定、默认 prompt 与 `meta.json.example`。

## 目录布局

```text
conversation_analysis/
  prompts/
    default.txt                 # 默认分析需求（单/多文件共用，保证可比）
  cases/
    single/<case_id>/
      meta.json                 # 必填：由 meta.json.example 复制改名
      <data files...>           # meta.files 中列出的文件
    multi/<case_id>/
      meta.json
      <data files...>
  results/                      # 脚本输出（勿手改；gitignore）
```

## `meta.json` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 展示名 |
| `files` | string[] | 相对本 case 目录的文件名列表；`single` 通常 1 个，`multi` ≥ 2 |
| `prompt` | string? | 可选；缺省使用 `prompts/default.txt` |

示例见：

- [`cases/single/example_case/meta.json.example`](cases/single/example_case/meta.json.example)
- [`cases/multi/example_case/meta.json.example`](cases/multi/example_case/meta.json.example)

## 导入步骤

1. 在 `cases/single/` 或 `cases/multi/` 下新建 `<case_id>/` 目录。
2. 将外部数据文件放入该目录。
3. 复制对应的 `meta.json.example` 为 `meta.json`，填写 `files`（及可选 `prompt`）。
4. 运行 dry-run 校验：

```bash
python scripts/run_conversation_analysis_experiment.py --dry-run
```

5. 后端已启动且持有 Token 后执行完整实验：

```bash
python scripts/run_conversation_analysis_experiment.py --token "$TOKEN"
```

## 场景语义

| 场景 | 路径 | 含义 |
|------|------|------|
| 单文件 | `cases/single/` | 会话工作区只上传 1 个数据文件后发起分析 |
| 多文件组合 | `cases/multi/` | 同一会话上传多个文件，对比联合分析效果 |

每个 case 使用**独立** `session_id`，避免工作区互相污染。对比时建议固定同一 `prompt`（或显式在 meta 中写出），以便横向比较报告与 SSE 事件。
