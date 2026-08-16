from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from dotenv import find_dotenv, load_dotenv
from pwdlib import PasswordHash


load_dotenv(find_dotenv())

ALGORITHM = "HS256"
DEFAULT_ISSUER = "deep-search-pro"
DEFAULT_AUDIENCE = "deep-search-pro-api"
DEFAULT_TOKEN_EXPIRE_MINUTES = 15
MIN_SECRET_BYTES = 32
REJECTED_SECRET_VALUES = {
    "<random-secret-at-least-32-bytes>",
    "change-me",
}

_password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


class AuthConfigurationError(RuntimeError):
    """Raised when authentication cannot be safely configured."""


@dataclass(frozen=True)
class AuthSettings:
    username: str
    user_id: str
    password_hash: str
    jwt_secret: str
    token_expire_minutes: int = DEFAULT_TOKEN_EXPIRE_MINUTES
    issuer: str = DEFAULT_ISSUER
    audience: str = DEFAULT_AUDIENCE
    ws_auth_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthSettings":
        values = os.environ if environ is None else environ

        username = _required_value(values, "AUTH_USERNAME")
        user_id = _required_value(values, "AUTH_USER_ID")
        password_hash = _required_value(values, "AUTH_PASSWORD_HASH")
        jwt_secret = _required_value(values, "JWT_SECRET_KEY")

        if not _password_hash.current_hasher.identify(password_hash):
            raise AuthConfigurationError("AUTH_PASSWORD_HASH must be an Argon2 hash")

        if (
            len(jwt_secret.encode("utf-8")) < MIN_SECRET_BYTES
            or jwt_secret in REJECTED_SECRET_VALUES
        ):
            raise AuthConfigurationError(
                f"JWT_SECRET_KEY must be a random value of at least {MIN_SECRET_BYTES} bytes"
            )

        token_expire_minutes = _positive_int(
            values.get("AUTH_TOKEN_EXPIRE_MINUTES"),
            "AUTH_TOKEN_EXPIRE_MINUTES",
            DEFAULT_TOKEN_EXPIRE_MINUTES,
        )
        ws_auth_timeout_seconds = _positive_float(
            values.get("AUTH_WS_AUTH_TIMEOUT_SECONDS"),
            "AUTH_WS_AUTH_TIMEOUT_SECONDS",
            5.0,
        )

        issuer = values.get("AUTH_JWT_ISSUER", DEFAULT_ISSUER).strip()
        audience = values.get("AUTH_JWT_AUDIENCE", DEFAULT_AUDIENCE).strip()
        if not issuer or not audience:
            raise AuthConfigurationError(
                "AUTH_JWT_ISSUER and AUTH_JWT_AUDIENCE must not be empty"
            )

        return cls(
            username=username,
            user_id=user_id,
            password_hash=password_hash,
            jwt_secret=jwt_secret,
            token_expire_minutes=token_expire_minutes,
            issuer=issuer,
            audience=audience,
            ws_auth_timeout_seconds=ws_auth_timeout_seconds,
        )


@dataclass(frozen=True)
class Principal:
    subject: str
    expires_at: float | None = None


def _required_value(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise AuthConfigurationError(f"{name} is required")
    return value


def _positive_int(raw_value: str | None, name: str, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise AuthConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise AuthConfigurationError(f"{name} must be a positive integer")
    return value


def _positive_float(raw_value: str | None, name: str, default: float) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise AuthConfigurationError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise AuthConfigurationError(f"{name} must be a positive number")
    return value


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def authenticate_credentials(
    username: str,
    password: str,
    settings: AuthSettings | None = None,
) -> Principal | None:
    settings = settings or AuthSettings.from_env()
    username_matches = secrets.compare_digest(username, settings.username)
    password_matches = verify_password(password, settings.password_hash)
    if not username_matches or not password_matches:
        return None
    return Principal(subject=settings.user_id)


def issue_access_token(
    subject: str,
    settings: AuthSettings | None = None,
    now: datetime | None = None,
) -> str:
    settings = settings or AuthSettings.from_env()
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    issued_at = issued_at.astimezone(timezone.utc)
    issued_timestamp = int(issued_at.timestamp())
    expires_timestamp = int(
        (issued_at + timedelta(minutes=settings.token_expire_minutes)).timestamp()
    )
    payload = {
        "sub": subject,
        "iss": settings.issuer,
        "aud": settings.audience,
        "iat": issued_timestamp,
        "exp": expires_timestamp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(
    token: str,
    settings: AuthSettings | None = None,
) -> Principal:
    settings = settings or AuthSettings.from_env()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            issuer=settings.issuer,
            audience=settings.audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid access token") from exc

    subject = payload.get("sub")
    expires_at = payload.get("exp")
    if (
        not isinstance(subject, str)
        or not secrets.compare_digest(subject, settings.user_id)
        or not isinstance(expires_at, (int, float))
    ):
        raise ValueError("invalid access token")
    return Principal(subject=subject, expires_at=float(expires_at))


def authentication_error(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_auth_settings() -> AuthSettings:
    try:
        return AuthSettings.from_env()
    except AuthConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured",
        ) from exc


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> Principal:
    if not token:
        raise authentication_error()
    settings = get_auth_settings()
    try:
        return decode_access_token(token, settings)
    except ValueError:
        raise authentication_error()
