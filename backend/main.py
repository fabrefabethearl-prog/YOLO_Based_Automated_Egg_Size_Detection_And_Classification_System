"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.paths import FRONTEND, ROOT
from backend.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME, cookie_is_secure, get_current_user
from backend.models import User
from backend.routers import auth, benchmark, camera, dataset, logs, phone_camera, scans, settings, stats

load_dotenv(ROOT / ".env")

LOCAL_ORIGINS = [
    "http://localhost",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from backend.db import init_db
    from backend.services.detector import detector

    init_db()
    detector.load()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="EggScan", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        response = JSONResponse(status_code=exc.status_code, content={"detail": detail})
        if exc.status_code == 401 and detail in {"Not authenticated.", "Session expired. Please sign in again."}:
            response.delete_cookie(
                key=SESSION_COOKIE_NAME,
                path="/",
                secure=cookie_is_secure(request),
                httponly=True,
                samesite="strict",
            )
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        parts = []
        for err in exc.errors():
            loc = " ".join(str(item) for item in err.get("loc", []) if item not in ("body", "query"))
            message = err.get("msg", "Invalid value")
            if message.startswith("Value error, "):
                message = message.replace("Value error, ", "", 1)
            parts.append(f"{loc}: {message}" if loc else message)
        return JSONResponse(status_code=422, content={"detail": "; ".join(parts) or "Check the form and try again."})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        from backend.services.audit import write_log

        write_log("ERROR", f"{request.method} {request.url.path}: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server."})

    @app.middleware("http")
    async def no_cache_frontend(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raw_origin = request.headers.get("origin") or request.headers.get("referer")
            parsed_origin = urlsplit(raw_origin) if raw_origin else None
            expected_host = request.headers.get("host", "").casefold()
            origin_is_same = bool(
                parsed_origin
                and parsed_origin.scheme == request.url.scheme
                and parsed_origin.netloc.casefold() == expected_host
            )
            if not origin_is_same:
                response = JSONResponse(status_code=403, content={"detail": "Request origin could not be verified."})
                response.headers["X-EggScan-Origin"] = "app"
                return response
            # Phone pairing validation is capability-token authenticated; cookie-authenticated
            # state changes, including sign-in and logout, require the double-submit CSRF token.
            if request.url.path != "/api/camera/phone/validate":
                cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
                header_token = request.headers.get(CSRF_HEADER_NAME, "")
                if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                    response = JSONResponse(status_code=403, content={"detail": "Security token expired. Refresh the page and try again."})
                    response.headers["X-EggScan-Origin"] = "app"
                    return response
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["X-EggScan-Origin"] = "app"
        if request.url.path == "/" or request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/size-classes")
    def size_classes(_user: User = Depends(get_current_user)):
        from backend.db import SessionLocal
        from backend.services.serialize import category_dict
        from backend.services.settings_store import get_categories

        db = SessionLocal()
        try:
            return {"categories": [category_dict(row) for row in get_categories(db)]}
        finally:
            db.close()

    app.include_router(auth.router)
    app.include_router(camera.router)
    app.include_router(phone_camera.router)
    app.include_router(scans.router)
    app.include_router(stats.router)
    app.include_router(settings.router)
    app.include_router(benchmark.router)
    app.include_router(dataset.router)
    app.include_router(logs.router)

    @app.get("/")
    def index():
        page = FRONTEND / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="Frontend is missing.")
        return FileResponse(page)

    @app.get("/phone-camera")
    def phone_camera_page():
        page = FRONTEND / "phone-camera.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="Phone camera page is missing.")
        return FileResponse(page)

    static_dir = FRONTEND if FRONTEND.is_dir() else Path(".")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


app = create_app()
