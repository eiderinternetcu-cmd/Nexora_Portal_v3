from pydantic import BaseModel
from typing import Generic, TypeVar, Any

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    message: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Any = None


class MessageResponse(BaseModel):
    success: bool = True
    message: str
