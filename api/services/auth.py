"""
Sign-in: checking credentials, issuing tokens, and the dependencies that turn a request's bearer token into a user.

Everything provider-specific sits behind AuthProvider, so moving to another identity provider (e.g. Cognito) is a new
AuthProvider class and a change to the auth_provider line below. LocalAuthProvider checks bcrypt hashes in icid.users
and signs its own JWTs.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException

from api.core.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET_KEY
from api.queries.users import get_user_by_uuid, get_user_for_auth
from api.schemas.auth import UserOut

# Checked when there is no user or no password to check, so an unknown email takes as long to refuse as a wrong
# password. The hash of a random string nobody has.
_NO_USER_HASH = "$2b$12$2nciNUoEvQeR/D4qg1tnC.3U5QOOua.Zlkji./9fpDrhQH2HtvPrG"
_BEARER = {"WWW-Authenticate": "Bearer"}


class AuthProvider(ABC):
    """Where users are checked and tokens come from. One concrete class per identity provider."""

    @abstractmethod
    def verify_credentials(self, email: str, password: str) -> Optional[UserOut]:
        """
        Check an email and password.
        Takes the email and the password as typed.
        Returns the user they belong to, or None when they match no one.
        """

    @abstractmethod
    def issue_token(self, user: UserOut) -> str:
        """
        Make an access token for a user.
        Takes the user.
        Returns the token.
        """


class LocalAuthProvider(AuthProvider):
    """Users and bcrypt password hashes in icid.users; tokens are JWTs signed with JWT_SECRET_KEY."""

    def verify_credentials(self, email: str, password: str) -> Optional[UserOut]:
        """
        Check an email (matched without regard to case) and password against icid.users.
        Takes the email and the password as typed.
        Returns the user, or None for an unknown email, a user without a password or a wrong password.
        """
        row = get_user_for_auth(email)
        password_hash = row["password_hash"] if row else None
        if not _password_matches(password, password_hash or _NO_USER_HASH) or not password_hash:
            return None
        return UserOut.model_validate(row)

    def issue_token(self, user: UserOut) -> str:
        """
        Sign a JWT naming the user, valid for JWT_EXPIRY_SECONDS from now.
        Takes the user.
        Returns the encoded token (claims: sub, iat, exp).
        """
        now = datetime.now(timezone.utc)
        claims = {"sub": str(user.uuid), "iat": now, "exp": now + timedelta(seconds=JWT_EXPIRY_SECONDS)}
        return jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _password_matches(password: str, password_hash: str) -> bool:
    """
    Check a password against a bcrypt hash.
    Takes the password as typed and the stored hash.
    Returns whether they match; False too for a password bcrypt won't take (over 72 bytes) or an unreadable hash.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# The one line to change when the identity provider does
auth_provider: AuthProvider = LocalAuthProvider()


def current_user(authorization: Optional[str] = Header(None)) -> UserOut:
    """
    FastAPI dependency: the user a request's bearer token belongs to.
    Takes the Authorization header.
    Returns the user; raises 401 for a missing or malformed header, an expired or invalid token, or a user who no
    longer exists.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated", headers=_BEARER)
    try:
        claims = jwt.decode(authorization.removeprefix("Bearer ").strip(), JWT_SECRET_KEY,
                            algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]})
        user_uuid = UUID(claims["sub"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired", headers=_BEARER)
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token", headers=_BEARER)
    row = get_user_by_uuid(user_uuid)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid token", headers=_BEARER)
    return UserOut.model_validate(row)


def current_admin(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency: the signed-in user, who must be an admin.
    Takes the current user.
    Returns them; raises 403 when their role isn't "admin".
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
