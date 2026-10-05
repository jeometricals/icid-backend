from pydantic import BaseModel


class DemoCleanupResult(BaseModel):
    """How many abandoned demo users a cleanup run deleted."""

    purged_count: int
