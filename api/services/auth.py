"""
Sign-in: checking credentials, issuing tokens, and the dependencies that turn a request's bearer token into a user.

Everything provider-specific sits behind AuthProvider, so moving to another identity provider (e.g. Cognito) is a new
AuthProvider class and a change to the auth_provider line below. LocalAuthProvider checks bcrypt hashes in icid.users
and signs its own JWTs.

Demo mode lives here too: the provider makes throwaway demo users, and the dependencies below keep them to their own
IDRs and their own project, and stop them submitting.
"""

import hmac
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request

from api.core.config import CRON_SECRET, JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET_KEY
from api.queries.features import get_feature
from api.queries.flags import get_flag
from api.queries.idrs import get_idr_by_id
from api.queries.non_assessable_areas import get_area
from api.queries.pir_pages import get_page
from api.queries.pirs import get_pir
from api.queries.ped_ramp_corners import get_corner
from api.queries.ped_ramp_inspections import get_inspection
from api.queries.ped_ramp_photos import get_photo, get_photo_tif
from api.queries.projects import get_project_by_id, get_user_roles_on_project, is_user_on_project
from api.queries.properties import get_property
from api.queries.segments import get_segment
from api.queries.match_lines import get_match_line
from api.queries.task_orders import get_task_order
from api.queries.users import create_demo_user, get_user_by_uuid, get_user_for_auth
from api.schemas.auth import UserOut

# Checked when there is no user or no password to check, so an unknown email takes as long to refuse as a wrong
# password. The hash of a random string nobody has.
_NO_USER_HASH = "$2b$12$2nciNUoEvQeR/D4qg1tnC.3U5QOOua.Zlkji./9fpDrhQH2HtvPrG"
_BEARER = {"WWW-Authenticate": "Bearer"}

# Demo users: one project, one client, and a ceiling on how many exist at once (the endpoint that makes them is public)
DEMO_PROJECT_ID = "DEMO01"
DEMO_CLIENT_ID = "C00001"
DEMO_PROJECT_ROLE = "Demo"
MAX_DEMO_USERS = 200

# The client a user an admin creates sits under (users.client_id is required, and the form asks for none)
DEFAULT_CLIENT_ID = "C00001"
# bcrypt reads no more of a password than this many bytes, and refuses a longer one
MAX_PASSWORD_BYTES = 72

# The roles a user can hold on a project (icid.project_users.role). Admin is not one: it is users.role.
PROJECT_ROLES = ("inspector", "oe", "re")


class DemoUnavailableError(Exception):
    """A demo user can't be made right now: the demo project is missing, or too many demo users exist."""


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

    @abstractmethod
    def create_demo_user(self) -> UserOut:
        """
        Make a throwaway demo user, assigned to the demo project.
        Takes nothing.
        Returns the new user; raises DemoUnavailableError when one can't be made.
        """


class LocalAuthProvider(AuthProvider):
    """Users and bcrypt password hashes in icid.users; tokens are JWTs signed with JWT_SECRET_KEY."""

    def verify_credentials(self, email: str, password: str) -> Optional[UserOut]:
        """
        Check an email (matched without regard to case) and password against icid.users.
        Takes the email and the password as typed.
        Returns the user, or None for an unknown email, a deleted user, a user without a password or a wrong password.
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

    def create_demo_user(self) -> UserOut:
        """
        Add a demo user to icid.users (is_demo, no password, no role) and assign them to DEMO01.
        Takes nothing.
        Returns the new user; raises DemoUnavailableError when DEMO01 is missing or MAX_DEMO_USERS already exist.
        """
        row = create_demo_user(DEMO_CLIENT_ID, DEMO_PROJECT_ID, DEMO_PROJECT_ROLE, MAX_DEMO_USERS)
        if row is None:
            if get_project_by_id(DEMO_PROJECT_ID) is None:
                raise DemoUnavailableError("Demo mode is not set up")
            raise DemoUnavailableError("Demo mode is busy, try again later")
        return UserOut.model_validate(row)


def hash_password(password: str) -> str:
    """
    Hash a password for storing in icid.users, as sign-in checks it.
    Takes the password as typed, of at most MAX_PASSWORD_BYTES bytes.
    Returns the bcrypt hash as text; raises ValueError for a longer password.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"A password can't be longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


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
    longer exists or has been deleted.
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
    # A deleted user's token is refused like one for a user who no longer exists
    if row is None or row.get("deleted_at") is not None:
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


def require_project_role(*roles: str) -> Callable[..., UserOut]:
    """
    Build a FastAPI dependency for routes under an IDR: the signed-in user must hold one of the roles on its project.
    Takes the project roles that may pass ('inspector', 'oe', 're'); raises ValueError for none or an unknown one.
    Returns the dependency, which returns the user, or raises 404 for an IDR that doesn't exist and 403 for a user
    with none of the roles. An admin passes without holding any.
    """
    if not roles or not set(roles) <= set(PROJECT_ROLES):
        raise ValueError(f"Project roles are {', '.join(PROJECT_ROLES)}; got {roles!r}")

    def dependency(idr_id: UUID, user: UserOut = Depends(current_user)) -> UserOut:
        """
        Check the signed-in user's roles on the project of the IDR in the path.
        Takes the idr_id path parameter and the current user.
        Returns the user; raises 404 or 403 as described above.
        """
        if user.role == "admin":
            return user
        idr = get_idr_by_id(idr_id)
        if idr is None:
            raise HTTPException(status_code=404, detail="IDR not found")
        if not get_user_roles_on_project(user.uuid, idr["project_id"]) & set(roles):
            raise HTTPException(status_code=403, detail="Role required: " + "/".join(roles))
        return user

    return dependency


# The review statuses an IDR can be edited in: the column holding that stage's reviewer and the project roles that
# may review at it
_STAGE_REVIEWERS = {
    "stage1_review": ("stage1_reviewer_uuid", ("oe", "re")),
    "stage2_review": ("re_reviewer_uuid", ("re",)),
}


def stage_reviewer(idr_id: UUID, user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for editing an IDR in review: the signed-in user must be the reviewer who accepted it at the stage it is in now (stage1_reviewer_uuid in Stage 1 review, re_reviewer_uuid in Stage 2 review), and still hold a role that reviews at that stage on its project. Tighter than require_project_role, which any holder of the role passes. An admin stands in for the reviewer.
    Takes the idr_id path parameter and the current user.
    Returns the user; raises 404 for an IDR that doesn't exist, 400 for one that isn't in review (a deleted one included), and 403 for anyone but that reviewer.
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise HTTPException(status_code=404, detail="IDR not found")
    if idr["status"] not in _STAGE_REVIEWERS or idr.get("deleted_at") is not None:
        raise HTTPException(status_code=400, detail="Only an IDR in review can be edited by a reviewer")
    if user.role == "admin":
        return user
    column, roles = _STAGE_REVIEWERS[idr["status"]]
    if idr[column] != user.uuid or not get_user_roles_on_project(user.uuid, idr["project_id"]) & set(roles):
        raise HTTPException(status_code=403, detail="Only the reviewer who accepted this IDR can edit it")
    return user


def admin_or_cron(authorization: Optional[str] = Header(None)) -> Optional[UserOut]:
    """
    FastAPI dependency for scheduled jobs an admin may also run by hand: lets in the scheduler, which sends CRON_SECRET as its bearer token (as Vercel Cron does), or a signed-in admin.
    Takes the Authorization header.
    Returns the admin, or None for the scheduler; raises 401 without a valid token and 403 for a user who isn't an admin. With CRON_SECRET unset, only an admin gets in.
    """
    if CRON_SECRET and authorization and hmac.compare_digest(
            authorization.encode("utf-8"), f"Bearer {CRON_SECRET}".encode("utf-8")):
        return None
    return current_admin(current_user(authorization))


def optional_user(authorization: Optional[str] = Header(None)) -> Optional[UserOut]:
    """
    FastAPI dependency: the signed-in user if the request carries a valid bearer token.
    Takes the Authorization header.
    Returns the user, or None for a missing, malformed, expired or invalid token, or when the user can't be looked
    up (never raises).
    """
    try:
        return current_user(authorization)
    except Exception:  # noqa: BLE001 - its one caller, signing out, must not fail
        return None


def _refuse_demo(user: UserOut, detail: str) -> UserOut:
    """
    Turn a demo user away.
    Takes the user and what to tell a demo user.
    Returns the user; raises 403 with that detail for a demo user.
    """
    if user.is_demo:
        raise HTTPException(status_code=403, detail=detail)
    return user


def require_full_user(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for submitting: the signed-in user, who must not be a demo user.
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: submit is disabled")


def project_member(project_id: str, user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for routes that read one project's data: the signed-in user must be assigned to the project (in any role), or be an admin.
    Takes the project_id path parameter and the current user.
    Returns the user; raises 404 for a project that doesn't exist and 403 for a user who isn't on it.
    """
    if get_project_by_id(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != "admin" and not is_user_on_project(user.uuid, project_id):
        raise HTTPException(status_code=403, detail="Project access required")
    return user


def _readable_on_project(found: Optional[dict], user: UserOut, not_found: str) -> dict:
    """
    Check that the signed-in user may read a row that belongs to a project: they are assigned to its project (in any role), or are an admin.
    Takes the row (None when it doesn't exist), the current user and the 404's detail.
    Returns the row; raises 404 for a missing row (and, as the project fence does, for a demo user's view of another project's) and 403 for a user who isn't on its project.
    """
    if found is None:
        raise HTTPException(status_code=404, detail=not_found)
    if user.role != "admin" and not is_user_on_project(user.uuid, found["project_id"]):
        if user.is_demo:
            raise HTTPException(status_code=404, detail=not_found)
        raise HTTPException(status_code=403, detail="Project access required")
    return found


def property_member(property_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one property: the signed-in user must be assigned to its project (in any role), or be an admin.
    Takes the property_id path parameter and the current user.
    Returns the property dict; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_property(property_id), user, "Property not found")


def pir_page_member(pir_page_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one page of a PIR: the signed-in user must be assigned to its PIR's property's project (in any role), or be an admin.
    Takes the pir_page_id path parameter and the current user.
    Returns the page dict (with its PIR's and property's deleted states); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_page(pir_page_id), user, "Page not found")


def flag_member(flag_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one flag: the signed-in user must be assigned to its PIR's property's project (in any role), or be an admin.
    Takes the flag_id path parameter and the current user.
    Returns the flag dict; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_flag(flag_id), user, "Flag not found")


def non_assessable_area_member(non_assessable_area_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one non-assessable area: the signed-in user must be assigned to its flag's PIR's property's project (in any role), or be an admin.
    Takes the non_assessable_area_id path parameter and the current user.
    Returns the area dict (with its flag's, page's, PIR's and property's deleted states); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_area(non_assessable_area_id), user, "Non-assessable area not found")


def feature_member(feature_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one feature: the signed-in user must be assigned to its page's PIR's property's project (in any role), or be an admin.
    Takes the feature_id path parameter and the current user.
    Returns the feature dict (with its page's, PIR's and property's deleted states); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_feature(feature_id), user, "Feature not found")


def segment_member(segment_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one segment: the signed-in user must be assigned to its page's PIR's property's project (in any role), or be an admin.
    Takes the segment_id path parameter and the current user.
    Returns the segment dict (with its page's, PIR's and property's deleted states); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_segment(segment_id), user, "Segment not found")


def match_line_member(match_line_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one match line: the signed-in user must be assigned to its page's PIR's property's project (in any role), or be an admin.
    Takes the match_line_id path parameter and the current user.
    Returns the match line dict (with its page's, PIR's and property's deleted states); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_match_line(match_line_id), user, "Match line not found")


def corner_member(corner_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one ped ramp corner: the signed-in user must be assigned to its project (in any role), or be an admin.
    Takes the corner_id path parameter and the current user.
    Returns the corner dict, with its live ramps; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_corner(corner_id), user, "Corner not found")


def inspection_member(inspection_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one ped ramp inspection: the signed-in user must be assigned to its corner's project (in any role), or be an admin.
    Takes the inspection_id path parameter and the current user.
    Returns the inspection dict; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_inspection(inspection_id), user, "Inspection not found")


def task_order_member(task_order_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one task order: the signed-in user must be assigned to its project (in any role), or be an admin.
    Takes the task_order_id path parameter and the current user.
    Returns the task order dict, with its live bundled properties; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_task_order(task_order_id), user, "Task order not found")


def pir_member(pir_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes that read one PIR: the signed-in user must be assigned to its property's project (in any role), or be an admin.
    Takes the pir_id path parameter and the current user.
    Returns the PIR dict; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_pir(pir_id), user, "PIR not found")


def no_demo_users(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for routes demo users have no business on (e.g. the users list).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: not available")


def no_demo_signatures(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for the signature routes: demo users can't submit, so they have no signature.
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: signatures are not available")


def no_demo_idr_flag_writes(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for excluding and including an IDR's flags: demo users only read them (their IDRs are never submitted, so never claim anything).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: IDR flags are read-only")


def no_demo_task_order_writes(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for the task-order writes project members make: demo users only read task orders (what they wrote would outlive their own deletion).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: task orders are read-only")


def no_demo_property_writes(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for the property writes project members make: demo users only read properties (what they wrote would outlive their own deletion).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: properties are read-only")


def no_demo_pir_writes(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for the PIR writes project members make: demo users only read PIRs (what they wrote would outlive their own deletion).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: PIRs are read-only")


def no_demo_flag_writes(user: UserOut = Depends(current_user)) -> UserOut:
    """
    FastAPI dependency for the flag writes project members make: demo users only read flags (what they wrote would outlive their own deletion).
    Takes the current user.
    Returns them; raises 403 for a demo user.
    """
    return _refuse_demo(user, "Demo mode: flags are read-only")


def demo_idr_fence(request: Request, user: UserOut = Depends(current_user)) -> None:
    """
    FastAPI dependency for routes under an IDR: a demo user reaches only the IDRs they are the reporter of.
    Takes the request (for its idr_id path parameter) and the current user.
    Returns nothing; raises 404, as for an IDR that doesn't exist, when a demo user asks for someone else's.
    Other users, and routes without an idr_id, pass untouched.
    """
    idr_id = request.path_params.get("idr_id")
    if not user.is_demo or idr_id is None:
        return
    try:
        idr = get_idr_by_id(UUID(idr_id))
    except ValueError:
        return  # not a uuid: the route answers 422
    if idr is not None and idr["reporter_uuid"] != user.uuid:
        raise HTTPException(status_code=404, detail="IDR not found")


def demo_project_fence(request: Request, user: UserOut = Depends(current_user)) -> None:
    """
    FastAPI dependency for routes about one project: a demo user reaches only the projects they are assigned to.
    Takes the request (for its project_id path or query parameter) and the current user.
    Returns nothing; raises 404, as for a project that doesn't exist, when a demo user asks for another project.
    Other users, and routes without a project_id, pass untouched.
    """
    project_id = request.path_params.get("project_id") or request.query_params.get("project_id")
    if user.is_demo and project_id is not None and not is_user_on_project(user.uuid, project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def photo_tif_member(tif_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for a TIF's photo routes: the signed-in user must be assigned to its corner's project (in any role), or be an admin.
    Takes the tif_id path parameter and the current user.
    Returns the TIF's status, ramp, project and inspection status (get_photo_tif); raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_photo_tif(tif_id), user, "TIF not found")


def ped_ramp_photo_member(photo_id: UUID, user: UserOut = Depends(current_user)) -> dict:
    """
    FastAPI dependency for routes on one ped ramp photo: the signed-in user must be assigned to its project (in any role), or be an admin.
    Takes the photo_id path parameter and the current user.
    Returns the photo dict, with its TIF's and inspection's statuses; raises 404 or 403 as _readable_on_project does.
    """
    return _readable_on_project(get_photo(photo_id), user, "Photo not found")
