from typing import Any, Dict, List, Optional

from pydantic import BaseModel, model_validator


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
    user_id: Optional[int] = None
    phone: Optional[str] = None
    role: str = "member"
    permissions: List[str] = []

    @model_validator(mode="after")
    def require_user_id_or_phone(self) -> "AddMemberRequest":
        has_uid = self.user_id is not None and int(self.user_id) > 0
        has_phone = bool((self.phone or "").strip())
        if has_uid == has_phone:
            raise ValueError("请提供 user_id 或 phone 其中之一")
        return self


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    permissions: Optional[List[str]] = None


class DeleteAssetRequest(BaseModel):
    relative_path: str


class CreateTaskRequest(BaseModel):
    task_type: str
    session_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
