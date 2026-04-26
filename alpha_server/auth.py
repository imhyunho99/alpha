"""JWT 기반 인증 모듈.

- 환경변수 ALPHA_JWT_SECRET 으로 서명 키를 주입한다 (없으면 기동 시 임시 키 생성).
- 사용자 저장소는 ~/AlphaModels/users.json (해시된 비밀번호 + role).
- /auth/register, /auth/login 엔드포인트와 require_user / require_admin 의존성을 제공.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

USERS_FILE = os.path.expanduser("~/AlphaModels/users.json")
JWT_ALGO = "HS256"
JWT_TTL_HOURS = int(os.getenv("ALPHA_JWT_TTL_HOURS", "12"))


def _load_secret() -> str:
    secret = os.getenv("ALPHA_JWT_SECRET")
    if secret:
        return secret
    secret_path = os.path.expanduser("~/AlphaModels/.jwt_secret")
    if os.path.exists(secret_path):
        with open(secret_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    new_secret = secrets.token_urlsafe(48)
    with open(secret_path, "w", encoding="utf-8") as f:
        f.write(new_secret)
    os.chmod(secret_path, 0o600)
    return new_secret


JWT_SECRET = _load_secret()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPublic(BaseModel):
    username: str
    role: str


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=2 ** 14, r=8, p=1, dklen=32
    ).hex()
    return f"scrypt${salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("utf-8"), n=2 ** 14, r=8, p=1, dklen=32
    ).hex()
    return hmac.compare_digest(candidate, digest)


def _load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users(users: dict) -> None:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)
    os.chmod(USERS_FILE, 0o600)


def register_user(username: str, password: str, role: str = "user") -> UserPublic:
    if len(username) < 3 or len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="username은 3자 이상, password는 8자 이상이어야 합니다.",
        )
    users = _load_users()
    if username in users:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 존재하는 사용자명입니다."
        )
    users[username] = {"password": _hash_password(password), "role": role}
    _save_users(users)
    return UserPublic(username=username, role=role)


def authenticate(username: str, password: str) -> UserPublic:
    users = _load_users()
    record = users.get(username)
    if not record or not _verify_password(password, record["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="자격 증명이 올바르지 않습니다.",
        )
    return UserPublic(username=username, role=record.get("role", "user"))


def issue_token(user: UserPublic) -> TokenResponse:
    expires = datetime.now(tz=timezone.utc) + timedelta(hours=JWT_TTL_HOURS)
    payload = {
        "sub": user.username,
        "role": user.role,
        "exp": int(expires.timestamp()),
        "iat": int(datetime.now(tz=timezone.utc).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    return TokenResponse(access_token=token, expires_in=JWT_TTL_HOURS * 3600)


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="토큰이 만료되었습니다."
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다."
        )


def require_user(token: Optional[str] = Depends(oauth2_scheme)) -> UserPublic:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = _decode(token)
    return UserPublic(username=payload["sub"], role=payload.get("role", "user"))


def require_admin(user: UserPublic = Depends(require_user)) -> UserPublic:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다."
        )
    return user


def ensure_default_admin() -> None:
    """ALPHA_DEFAULT_ADMIN_PASSWORD가 설정되어 있고 사용자가 한 명도 없을 때 admin 계정을 생성한다."""
    users = _load_users()
    if users:
        return
    pw = os.getenv("ALPHA_DEFAULT_ADMIN_PASSWORD")
    if not pw or len(pw) < 8:
        return
    users["admin"] = {"password": _hash_password(pw), "role": "admin"}
    _save_users(users)
    print("✅ 기본 admin 계정이 생성되었습니다 (ALPHA_DEFAULT_ADMIN_PASSWORD 사용).")
