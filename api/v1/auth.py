from fastapi import APIRouter, Depends, HTTPException, Response

from api.core.config import JWT_EXPIRY_SECONDS
from api.schemas.auth import LoginRequest, LoginResponse, UserOut
from api.services.auth import auth_provider, current_user

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


@router.post("/logout", status_code=204)
def logout() -> Response:
    """
    Sign out. Tokens aren't tracked on the server, so there is nothing to revoke: the client drops its token.
    Takes nothing.
    Returns 204, signed in or not.
    """
    return Response(status_code=204)
