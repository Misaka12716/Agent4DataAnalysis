# backend/resource_models.py
# 个人资源管理 Pydantic 请求/响应模型

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MkdirRequest(BaseModel):
    """新建文件夹。"""

    parent_id: Optional[int] = Field(None, description="父文件夹 id，根目录传 null")
    name: str = Field(..., min_length=1, max_length=255, description="文件夹名称")


class MoveFileRequest(BaseModel):
    """移动文件/文件夹。"""

    target_parent_id: Optional[int] = Field(None, description="目标父文件夹 id，根目录传 null")


class PromoteDatasetRequest(BaseModel):
    """将表格文件晋升为数据集。"""

    name: Optional[str] = Field(None, description="数据集名称，默认用文件名")
    description: Optional[str] = Field(None, description="描述")


class DatasetCreateRequest(BaseModel):
    """从已有文件创建数据集（也可走 upload multipart）。"""

    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = None
    source_file_id: int = Field(..., description="来源文件空间节点 id")


class DatasetUpdateRequest(BaseModel):
    """更新数据集元信息。"""

    name: Optional[str] = Field(None, min_length=1, max_length=256)
    description: Optional[str] = None
    status: Optional[str] = Field(None, description="active / archived")


class DatasetRollbackRequest(BaseModel):
    """回滚到指定版本。"""

    version: int = Field(..., ge=1)


class DatasetVersionNoteRequest(BaseModel):
    """上传新版本时的备注（表单也可传 note）。"""

    note: Optional[str] = None


class ModelPredictRequest(BaseModel):
    """在线预测：传入特征行。"""

    rows: List[Dict[str, Any]] = Field(..., min_length=1, description="特征字典列表")


class ModelUploadMeta(BaseModel):
    """模型上传附带元数据（也可走 Form 字段）。"""

    model_name: str = Field(..., min_length=1, max_length=256)
    model_type: Optional[str] = None
    task_type: Optional[str] = None
    features: Optional[List[str]] = None
    metrics: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
