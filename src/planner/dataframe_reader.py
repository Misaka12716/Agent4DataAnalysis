"""
兼容层：表格深度分析已迁至 reader 模块。
保留 read_workspace_excel_schema_and_sample 供旧代码导入。
"""

from reader.legacy import read_workspace_excel_schema_and_sample

__all__ = ["read_workspace_excel_schema_and_sample"]
