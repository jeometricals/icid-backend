from fastapi import APIRouter, Depends, HTTPException

from api.queries.users import cleanup_abandoned_demo_users
from api.schemas.admin import DemoCleanupResult
from api.services.auth import admin_or_cron

# Every route needs the scheduler's secret or a signed-in admin
router = APIRouter(prefix="/v1/admin", tags=["Admin"], dependencies=[Depends(admin_or_cron)])


# GET as well as POST: Vercel Cron can only send a GET
@router.api_route("/cleanup-demos", methods=["GET", "POST"], response_model=DemoCleanupResult)
def cleanup_demos() -> DemoCleanupResult:
    """
    Delete the demo users abandoned for more than 24 hours, with everything of theirs. Run daily by Vercel Cron; an admin can run it by hand.
    Takes nothing.
    Returns how many users were deleted; raises 401, 403 (not an admin) and 500.
    """
    purged_count = cleanup_abandoned_demo_users()

    if purged_count is None:
        raise HTTPException(status_code=500, detail="Failed to clean up demo users")

    return DemoCleanupResult(purged_count=purged_count)
