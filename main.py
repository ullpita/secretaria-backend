"""Secretaria — FastAPI backend."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lib.config import settings
from routers import vapi, auth, actions, phone_setup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Secretaria backend starting (env=%s)", settings.APP_ENV)
    yield
    logger.info("Secretaria backend shutting down")


app = FastAPI(
    title="Secretaria API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vapi.router)
app.include_router(auth.router)
app.include_router(actions.router)
app.include_router(phone_setup.router)


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.APP_ENV, "build": "v4"}


@app.get("/debug/config")
async def debug_config():
    return {
        "TWILIO_ACCOUNT_SID_set": bool(settings.TWILIO_ACCOUNT_SID),
        "TWILIO_ACCOUNT_SID_prefix": settings.TWILIO_ACCOUNT_SID[:4] if settings.TWILIO_ACCOUNT_SID else None,
        "TWILIO_AUTH_TOKEN_set": bool(settings.TWILIO_AUTH_TOKEN),
        "VAPI_API_KEY_set": bool(settings.VAPI_API_KEY),
        "BACKEND_URL": settings.BACKEND_URL,
    }
