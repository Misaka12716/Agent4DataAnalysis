# 通用多智能体框架
主题思路：利用知识与工具资源池化，提高多智能体在数据分析等领域的性能。
注意：请所有修改都在docs文件夹内注明！

## Agentic Workflow过程设计
[详见飞书文档](https://hqkygix2uv.feishu.cn/docx/ZRszdUuDBoO9ZexpccLchRoGn0g)

## 一种平衡agent灵活性和workflow结构化执行的框架
[详见飞书文档](https://hqkygix2uv.feishu.cn/wiki/P4QnwaWYMiL1xEkchsqcwS0bnUf)

## 主要模块介绍

### Planner
Planner 模块负责根据用户目标自动生成多智能体协作计划，并将任务分配给各 Agent。它是任务分解与流程编排的核心，支持灵活的计划生成与动态调整。详细用法见 [docs/Planner.md](./docs/Planner.md)。
Planner还会判断任务的复杂程度，根据任务的复杂程度，将任务分配给不同的 Agent。
- 如果任务分为很多个并行的子任务，那么在编程时，会将这些子任务分配给不同的 Agent 并行执行。这样得到的，就可能是Jupyter Notebook中的多个Cell，或者是代码文件夹中的多个文件。
- 如果任务比较简单，那么就可以将任务分配给一个 Agent 执行。这样得到的就是单个代码文件。

### Executor
Executor 模块负责根据 Planner 生成的计划，协调各 Agent 之间的工作流程。它确保任务按序执行，管理资源分配与状态同步，同时支持异常处理与重试机制。

#### 分支1：简单任务（Single Agent）
