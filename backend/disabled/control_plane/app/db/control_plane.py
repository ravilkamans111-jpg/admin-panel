"""Control-plane database connection (engine + session factory).

Lowest layer: pure connection management, no queries. `app/repositories`
builds on top of the sessions this module hands out; nothing here imports
from `repositories`, `services`, or `api`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings_env import env_settings

control_plane_engine = create_async_engine(env_settings.control_plane_database_url, pool_pre_ping=True)
ControlPlaneSessionLocal = async_sessionmaker(control_plane_engine, expire_on_commit=False)


async def get_control_plane_session() -> AsyncIterator[AsyncSession]:
    async with ControlPlaneSessionLocal() as session:
        yield session
