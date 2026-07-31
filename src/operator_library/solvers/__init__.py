"""Software 1 reference solvers.

Each solver is a standalone, library-backed implementation of a Software 1
capability.  These solvers replace the csv-copied operators wherever the
original is broken, missing, or incomplete.

Convention:

  - ``contract: SolverContract`` declared at module level
  - ``run(df, mapping, output_dir) -> dict`` instance method
  - ``get_solver()`` factory function returning an instance

Solvers do NOT hardcode user column names; they use the ``mapping`` dict
produced by the column mapper to resolve roles to actual columns.

中文说明
========
本目录是 Software 1 的"参考实现"集合：每个文件 = 一个独立 solver，
对应原始 csv 算子目录里的某条能力（F01..F14）。当原始算子缺失 /
有 bug / 实现不完整时，就用这里的 solver 顶上去。

统一约定：
- 模块级声明 ``contract = SolverContract(...)``（多 solver 模块用
  ``XXX_CONTRACT``）
- 类方法 ``run(df, mapping, output_dir) -> dict``，返回值至少要有
  contract.output_files 里声明的 key
- 模块级工厂 ``get_solver(...)``（多 solver 模块用 ``get_xxx_solver``）

solver **绝不**写死用户的列名；列名解析由 ``mapping`` 完成。
"""
