"""记忆文档领域契约"""
from __future__ import annotations

import enum
import hashlib
import pydantic
import datetime


class MemoryDocumentScope(enum.StrEnum):
    """记忆文档适合范围"""
    USER="user"  #跨项目用户上下文
    PROJECT="project" #当前项目
    EXPERT="expert"   #单个专家

class MemoryIndexStatus(enum.StrEnum):
    """文档索引同步状态"""
    MISSING="missing"
    CURRENT="current"
    STALE="stale"
    ERROR="error"

def _compute_revision(content:str)->str:
    """SHA-256 内容指纹(用于乐观锁)"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

class MemoryDocument(pydantic.BaseModel):
    """一份规范记忆文档的不可变快照"""
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    scope: MemoryDocumentScope
    content: str
    revision: str
    exists: bool
    updated_at:datetime.datetime|None=None
    size_bytes:int= pydantic.Field(ge=0)
    index_status: MemoryIndexStatus=MemoryIndexStatus.MISSING
    exceeds_injection_budget:bool=False

    @pydantic.field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value:datetime.datetime|None)->datetime.datetime|None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)


class MemoryDocumentMutation(pydantic.BaseModel):
    """一次性已提交的记忆文档变更及其精确 diff."""
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    document: MemoryDocument
    diff:str


class MemorySearchHit(pydantic.BaseModel):
    """FTS 搜索命中的一条标题/段落"""
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    scope: MemoryDocumentScope
    heading:str|None=None
    content:str
    ordinal: int = pydantic.Field(ge=0)


class MemoryPromptProjection(pydantic.BaseModel):
    """提示词注入投影（含截断标记）"""
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    content:str
    truncated_scopes: tuple[MemoryDocumentScope,...]=()
    revisions: dict[MemoryDocumentScope,str]=pydantic.Field(default_factory=dict)


class MemoryRevisionConflictError(RuntimeError):
    """乐观并发检测到文档已被更新"""

    def __init__(self, *, expected_revision: str, actual_revision: str) -> None:
        super().__init__("Memory.md was modified aftee it was loaded")
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class MemoryDocumentNotConfiguredError(RuntimeError):
    """请求的scope未注入规范路径"""


# 兼容别名：这两个异常原先定义在 ``MemoryDocument`` 类体内，
# 外部代码按 ``memory_document.MemoryDocumentNotConfiguredError`` 访问，
# 但类内定义无法通过模块路径取到（AttributeError）。现移到模块级，
# 并在类内保留同名引用，两种访问方式都可用。
MemoryDocument.MemoryRevisionConflictError = MemoryRevisionConflictError
MemoryDocument.MemoryDocumentNotConfiguredError = MemoryDocumentNotConfiguredError


































