"""SQLAlchemy adapter for the authentication-owned user repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.user import User
from app.modules.auth.models import UserAccount
from app.modules.auth.repository import (
    CreateLocalUser,
    UserEmailConflictError,
)


class SqlAlchemyUserRepository:
    """Persist local accounts while keeping ORM and transactions in infrastructure."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_local_user(self, command: CreateLocalUser) -> UserAccount:
        """Atomically reject duplicate email and create one local account."""
        try:
            async with self._session.begin():
                existing = await self._session.scalar(
                    select(User).where(func.lower(User.email) == command.email.lower())
                )
                if existing is not None:
                    raise UserEmailConflictError

                user = User(
                    email=command.email,
                    password_hash=command.password_hash,
                    password_updated_at=command.password_updated_at,
                    display_name=command.display_name,
                    status="active",
                )
                self._session.add(user)
                await self._session.flush()
        except IntegrityError as exc:
            raise UserEmailConflictError from exc
        return _account_from_model(user)

    async def find_by_email(self, email: str) -> UserAccount | None:
        """Read one local account using the database's case-insensitive email rule."""
        user = await self._session.scalar(
            select(User).where(func.lower(User.email) == email.lower())
        )
        return _account_from_model(user) if user is not None else None

    async def find_active_by_id(self, user_id: UUID) -> UserAccount | None:
        """Read an active account for bearer-token authorization."""
        user = await self._session.scalar(
            select(User).where(User.id == user_id, User.status == "active")
        )
        return _account_from_model(user) if user is not None else None


def _account_from_model(user: User) -> UserAccount:
    return UserAccount(
        id=user.id,
        email=user.email,
        password_hash=user.password_hash,
        password_updated_at=user.password_updated_at,
        display_name=user.display_name,
        status=user.status,
        created_at=user.created_at,
    )
