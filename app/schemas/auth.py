from pydantic import BaseModel, Field
from app.models.user import UserRole


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPayload(BaseModel):
    sub: str           # user UUID
    jti: str           # unique token ID
    role: UserRole
    type: str          # "admin_access" | "admin_refresh" (legacy: "access" | "refresh")
    exp: int
    iat: int
    aud: str | None = None   # "nexora-admin" (added in JWT hardening; legacy tokens lack it)
    iss: str | None = None   # "nexora-api"
