"""Software 1 LLM Agent — auto-stack solvers from a natural-language task.

Entrypoints:

  - :func:`software1_agent.agent.solve_task` — programmatic API
  - ``python -m operator_agent`` — CLI

The agent is a thin orchestrator on top of:

  - ``operator_pipeline.registry``  (solver factories)
  - ``operator_pipeline.mapping_engine``  (per-step
    LLM column / param resolution)
  - ``operator_pipeline.runner``  (per-step executor)
  - ``operator_pipeline.llm_client``  (.env-driven
    OpenAI-compatible chat client; same model used for planning and
    mapping)

中文说明
========
对外暴露 ``solve_task``：自然语言任务 → 规划 JSON → 逐步执行。
规划与列映射可共用同一套 LLM（见 ``llm_client``）；执行细节见 ``agent`` 模块。
"""
from operator_agent.agent import solve_task, SolveResult  # noqa: F401
