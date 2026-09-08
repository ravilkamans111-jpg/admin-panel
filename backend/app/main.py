from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.antifraud_writes import router as antifraud_writes_router
from app.api.auth import router as auth_router
from app.api.cache_clear_actions import router as cache_clear_actions_router
from app.api.cascade_writes import router as cascade_writes_router
from app.api.dashboard import router as dashboard_router
from app.api.merchant_balance_writes import router as merchant_balance_writes_router
from app.api.merchant_bulk_actions import router as merchant_bulk_actions_router
from app.api.payment_method_writes import router as payment_method_writes_router
from app.api.settlement_writes import router as settlement_writes_router
from app.api.transaction_writes import router as transaction_writes_router
from app.core.settings_env import env_settings
from app.db.tenant_registry import dispose_all_engines


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_all_engines()


app = FastAPI(
    title="Brand Admin Panel",
    description="Unified multi-tenant read-only admin panel for AmPay / RajaPay / quiet-forest",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in env_settings.cors_allow_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)
app.include_router(transaction_writes_router)
app.include_router(settlement_writes_router)
app.include_router(antifraud_writes_router)
app.include_router(payment_method_writes_router)
app.include_router(cache_clear_actions_router)
app.include_router(cascade_writes_router)
app.include_router(merchant_bulk_actions_router)
app.include_router(merchant_balance_writes_router)
app.include_router(admin_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
