"""学校、学院、专业、课程与批次的作用域模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OrganizationType(str, Enum):
    SCHOOL = "school"
    COLLEGE = "college"
    MAJOR = "major"
    COURSE = "course"
    BATCH = "batch"


class OrganizationUnit(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: OrganizationType
    parent_id: str = ""
    code: str = ""
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
