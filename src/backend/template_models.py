# backend/template_models.py
# 模板管理 API 的 Pydantic 请求/响应模型

from pydantic import BaseModel, Field
from typing import Optional, List, Any
from db.template_schema import VALID_DISEASE_TYPES


class TemplateStep(BaseModel):
    """分析步骤定义"""
    step: int
    name: str
    action: str
    params: Optional[dict] = None


class TemplateCreateRequest(BaseModel):
    """创建模板请求"""
    template_name: str = Field(..., min_length=1, max_length=256)
    disease_type: str = Field(..., description="专病类型: depression/schizophrenia/anxiety/sleep/child_adolescent")
    scales: List[str] = Field(..., min_items=1)
    analysis_steps: List[TemplateStep] = Field(..., min_items=1)
    report_structure: List[str] = Field(..., min_items=1)
    version: str = Field(default="1.0.0")


class TemplateUpdateRequest(BaseModel):
    """更新模板请求（所有字段可选）"""
    template_name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    disease_type: Optional[str] = None
    scales: Optional[List[str]] = None
    analysis_steps: Optional[List[TemplateStep]] = None
    report_structure: Optional[List[str]] = None


class TemplateResponse(BaseModel):
    """模板响应"""
    id: int
    template_name: str
    disease_type: str
    scales: List[str]
    analysis_steps: List[dict]
    report_structure: List[str]
    version: str
    version_history: Optional[list] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TemplateImportResponse(BaseModel):
    """批量导入响应"""
    imported: int
    skipped: int
    details: Optional[List[str]] = None
