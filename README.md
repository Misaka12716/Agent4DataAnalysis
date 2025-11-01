# 通用多智能体框架
主题思路：利用知识与工具资源池化，提高多智能体在数据分析等领域的性能。

## Agentic Workflow过程设计
[详见飞书文档](https://hqkygix2uv.feishu.cn/docx/ZRszdUuDBoO9ZexpccLchRoGn0g)

## 一种平衡agent灵活性和workflow结构化执行的框架
[详见飞书文档](https://hqkygix2uv.feishu.cn/wiki/P4QnwaWYMiL1xEkchsqcwS0bnUf)

## 主要模块介绍

### Planner
Planner 模块负责根据用户目标自动生成多智能体协作计划，并将任务分配给各 Agent。它是任务分解与流程编排的核心，支持灵活的计划生成与动态调整。详细用法见 [docs/Planner.md](./docs/Planner.md)。