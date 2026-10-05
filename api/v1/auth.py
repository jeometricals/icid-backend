import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from api.core.config import JWT_EXPIRY_SECONDS
from api.schemas.auth import LoginRequest, LoginResponse, UserOut
from api.services.auth import DemoUnavailableError, auth_provider, current_user, optional_user
from api.services.demo import delete_demo_user_cascade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    """
    Sign a user in.
    Takes their email and password.
    Returns a bearer token, its lifetime in seconds and the user; raises 401 when the credentials match no one.
    """
    user = auth_provider.verify_credentials(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return LoginResponse(access_token=auth_provider.issue_token(user), expires_in=JWT_EXPIRY_SECONDS, user=user)


@router.get("/me", response_model=UserOut)
def me(user: UserOut = Depends(current_user)) -> UserOut:
    """
    Return the signed-in user.
    Takes the user the request's bearer token belongs to.
    Returns that user.
    """
    return user


@router.post("/demo", response_model=LoginResponse)
def start_demo() -> LoginResponse:
    """
    Start a demo: make a throwaway demo user on the demo project and sign them in. Needs no credentials.
    Takes nothing.
    Returns what login returns (bearer token, lifetime, user); raises 503 when a demo user can't be made.
    """
    try:
        user = auth_provider.create_demo_user()
    except DemoUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return LoginResponse(access_token=auth_provider.issue_token(user), expires_in=JWT_EXPIRY_SECONDS, user=user)


@router.post("/logout", status_code=204)
def logout(user: Optional[UserOut] = Depends(optional_user)) -> Response:
    """
    Sign out. Tokens aren't tracked on the server, so there is nothing to revoke: the client drops its token. A demo user signing out is deleted, with everything they made.
    Takes the signed-in user, if the request carries a valid token.
    Returns 204, signed in or not, and even if a demo user's delete fails (the daily cleanup removes them later).
    """
    if user is not None and user.is_demo:
        try:
            delete_demo_user_cascade(user.uuid)
        except Exception as exc:  # noqa: BLE001 - signing out must not fail
            logger.error("Could not delete demo user %s at logout: %s", user.uuid, exc)
    return Response(status_code=204)
