from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AdminCreateUserRequest(BaseModel):
    username: str
    phone: str
    platform_role: str = "user"
    status: str = "active"


class AdminUpdateUserRequest(BaseModel):
    username: Optional[str] = None
    phone: Optional[str] = None
    platform_role: Optional[str] = None
    status: Optional[str] = None


class AddMemberRequest(BaseModel):
    user_id: int
    role: str = "member"
    permissions: List[str] = []


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    permissions: Optional[List[str]] = None


class DeleteAssetRequest(BaseModel):
    relative_path: str


class CreateTaskRequest(BaseModel):
    task_type: str
    session_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
