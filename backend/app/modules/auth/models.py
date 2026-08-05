"""Authentication-owned account state exposed to application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UserAccount:
    """Account facts required by authentication and authorized API commands."""

    id: UUID
    email: str | None
    password_hash: str | None
    password_updated_at: datetime | None
    display_name: str
    status: str
    created_at: datetime
