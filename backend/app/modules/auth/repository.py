"""Authentication-owned persistence commands and port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.auth.models import UserAccount


@dataclass(frozen=True, slots=True)
class CreateLocalUser:
    """Typed command for creating one password-authenticated account."""

    email: str
    password_hash: str
    password_updated_at: datetime
    display_name: str


class UserEmailConflictError(RuntimeError):
    """The normalized email is already owned by another account."""


class UserRepository(Protocol):
    """Persistence port for authentication-owned account facts."""

    async def create_local_user(self, command: CreateLocalUser) -> UserAccount: ...

    async def find_by_email(self, email: str) -> UserAccount | None: ...

    async def find_active_by_id(self, user_id: UUID) -> UserAccount | None: ...
