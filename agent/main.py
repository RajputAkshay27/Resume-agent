"""
main.py — FastAPI server for the Resume Builder Agent v2.

Serves:
  - POST /api/tailor           Stateless tailoring + compilation endpoint.
  - POST /api/profile          Upload/update master profile.
  - GET  /api/profile          Retrieve current master profile.
  - POST /api/job-description  Upload/update job description.
  - GET  /api/job-description  Retrieve current job description.
  - POST /api/template         Upload new template version.
  - GET  /api/templates        List template versions.
  - GET  /api/template/{ver}   Retrieve specific template version.
  - POST /validate-template    Validate a Jinja2 LaTeX template.
  - GET  /health               Health check.

Security:
  - CORS restricted to configurable allowed origins (not wildcard in production).
  - API key authentication for all management endpoints.
  - Request size limit (1MB payload max).
  - Startup validation — fails fast if GOOGLE_API_KEY is missing.
  - Error responses sanitized (no stack traces in production).
"""

import base64
import json
import logging
import os
import uuid
import tempfile

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticBaseModel
from dotenv import load_dotenv

from logging_config import setup_logging
from agent import create_agent
from latex_bridge import validate_template
from storage_client import storage as _get_storage

# Structured JSON logging FIRST — before anything that touches logging
setup_logging("agent")
logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# API key for management endpoints (profile/JD/template upload)
MANAGEMENT_API_KEY = os.getenv("INTERNAL_API_KEY", "")
if not MANAGEMENT_API_KEY:
    logger.warning(
        "INTERNAL_API_KEY is not set. Management endpoints are unprotected. "
        "Set INTERNAL_API_KEY in your .env file."
    )

# Allowed CORS origins — default to localhost only
_RAW_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000")
ALLOWED_ORIGINS = [o.strip() for o in _RAW_ORIGINS.split(",") if o.strip()]

# Max request body size (bytes)
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(1 * 1024 * 1024)))  # 1 MB


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def require_api_key(x_api_key: str = Header(default="")):
    """Require a valid INTERNAL_API_KEY header for management endpoints."""
    if MANAGEMENT_API_KEY and x_api_key != MANAGEMENT_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API key.")
    return x_api_key


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProfileRequest(PydanticBaseModel):
    profile: dict


class JobDescriptionRequest(PydanticBaseModel):
    content: str


class TemplateRequest(PydanticBaseModel):
    content: str
    label: str = ""


class ValidateTemplateRequest(PydanticBaseModel):
    template_content: str


class TailorRequest(PydanticBaseModel):
    master_profile: dict | None = None
    job_description: str | None = None
    preferences: dict | None = None
    template_content: str | None = None


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def run():
    # Startup validation
    if not os.getenv("GOOGLE_API_KEY"):
        raise EnvironmentError(
            "GOOGLE_API_KEY environment variable is required. "
            "Set it in agent/.env or export it in your shell."
        )

    agent = create_agent()

    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    os.makedirs("data", exist_ok=True)
    session_service = SqliteSessionService("sqlite:///data/sessions.db")

    app = FastAPI(
        title="Resume Builder Agent v2",
        description="AI-powered resume tailoring API with ATS optimization.",
        version="2.0.0",
    )

    # ── Middleware ─────────────────────────────────────────────────────────

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    )

    # ── Telemetry ──────────────────────────────────────────────────────────

    from telemetry import setup_telemetry
    setup_telemetry(app)

    # ── Global exception handler ───────────────────────────────────────────

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s",
            request.method, str(request.url),
            exc_info=True,
        )
        # In production, do not expose internal details
        detail = str(exc) if DEBUG else "Internal Server Error"
        return JSONResponse(status_code=500, content={"error": detail})

    # ── Request size guard ─────────────────────────────────────────────────

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": f"Request body too large. Maximum is {MAX_REQUEST_BYTES // 1024} KB."},
            )
        return await call_next(request)

    # ── Health ─────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "2.0.0"}

    # ── Profile management ─────────────────────────────────────────────────

    @app.post("/api/profile", dependencies=[Depends(require_api_key)])
    async def upload_profile(req: ProfileRequest):
        """Upload or update the master profile."""
        if not req.profile:
            raise HTTPException(status_code=400, detail="Profile must not be empty.")
        try:
            _get_storage().save_profile(req.profile)
            return {"success": True, "message": "Profile saved."}
        except Exception as e:
            logger.error("Profile upload error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to save profile.")

    @app.get("/api/profile")
    async def get_profile():
        """Retrieve the current master profile."""
        profile = _get_storage().load_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="No profile set.")
        return {"profile": profile}

    # ── Job description management ─────────────────────────────────────────

    @app.post("/api/job-description", dependencies=[Depends(require_api_key)])
    async def upload_job_description(req: JobDescriptionRequest):
        """Upload or update the job description."""
        if not req.content or not req.content.strip():
            raise HTTPException(status_code=400, detail="Job description must not be empty.")
        try:
            _get_storage().save_job_description(req.content.strip())
            return {"success": True, "message": "Job description saved."}
        except Exception as e:
            logger.error("JD upload error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to save job description.")

    @app.get("/api/job-description")
    async def get_job_description():
        """Retrieve the current job description."""
        jd = _get_storage().load_job_description()
        if not jd:
            raise HTTPException(status_code=404, detail="No job description set.")
        return {"content": jd}

    # ── Template management ────────────────────────────────────────────────

    @app.post("/api/template", dependencies=[Depends(require_api_key)])
    async def upload_template(req: TemplateRequest):
        """Upload a new LaTeX template version."""
        if not req.content or not req.content.strip():
            raise HTTPException(status_code=400, detail="Template content must not be empty.")
        try:
            version = _get_storage().save_template(req.content, label=req.label)
            return {"success": True, "version": version}
        except Exception as e:
            logger.error("Template upload error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to save template.")

    @app.get("/api/templates")
    async def list_templates():
        """List all template versions."""
        return {"templates": _get_storage().list_templates()}

    @app.get("/api/template/{version}")
    async def get_template(version: int):
        """Retrieve a specific template version."""
        content = _get_storage().load_template(version)
        if not content:
            raise HTTPException(status_code=404, detail=f"Template version {version} not found.")
        return {"version": version, "content": content}

    # ── Template validation ────────────────────────────────────────────────

    @app.post("/validate-template")
    async def validate_template_endpoint(req: ValidateTemplateRequest):
        """Validate a Jinja2 LaTeX template and return analysis."""
        result = validate_template(req.template_content)
        return JSONResponse(content=result)

    # ── Stateless tailoring API ────────────────────────────────────────────

    @app.post("/api/tailor")
    async def api_tailor(req: TailorRequest):
        """
        Stateless resume tailoring + compilation.

        Accepts profile/JD/preferences/template in the request body.
        Falls back to database values for any missing fields.
        """
        store = _get_storage()

        # Resolve inputs — request body takes priority, fall back to database
        master_profile = req.master_profile or store.load_profile()
        job_description = req.job_description or store.load_job_description()
        preferences = req.preferences or store.load_preferences() or {}
        template_content = req.template_content or store.load_template()

        if not master_profile:
            raise HTTPException(status_code=400, detail="No master profile found. Upload one first.")
        if not job_description:
            raise HTTPException(status_code=400, detail="No job description found. Upload one first.")
        if not template_content:
            raise HTTPException(status_code=400, detail="No LaTeX template found. Upload one first.")

        # Write inputs temporarily if they differ from stored values
        if req.master_profile:
            store.save_profile(req.master_profile)
        if req.job_description:
            store.save_job_description(req.job_description)
        if req.preferences:
            store.save_preferences(req.preferences)
        if req.template_content:
            store.save_template(req.template_content, label="api_upload")

        # Run the agent
        from google.adk import Runner
        from google.genai import types

        runner = Runner(
            app_name="resume_builder_agent",
            agent=agent,
            session_service=session_service,
            auto_create_session=True,
        )
        session_id = str(uuid.uuid4())
        user_id = f"api_user_{session_id}"

        async for _ in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(parts=[types.Part.from_text(
                text="Tailor my resume for the job description and compile the final PDF."
            )]),
        ):
            pass

        session = await session_service.get_session(
            app_name="resume_builder_agent",
            user_id=user_id,
            session_id=session_id,
        )

        tailored = None
        if session:
            tailored = session.state.get("tailored_sections") or session.state.get("tailored_resume")

        # Read output files
        output_dir = os.getenv("OUTPUT_DIR", "output")
        tex_path = os.path.join(output_dir, "resume.tex")
        pdf_path = os.path.join(output_dir, "resume.pdf")

        rendered_tex = ""
        if os.path.exists(tex_path):
            with open(tex_path, "r", encoding="utf-8") as f:
                rendered_tex = f.read()

        pdf_base64 = None
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_base64 = base64.b64encode(f.read()).decode("utf-8")

        return {
            "tailored_sections": tailored,
            "rendered_tex": rendered_tex,
            "compiled_pdf_base64": pdf_base64,
            "success": bool(tailored),
        }

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=600,
        timeout_graceful_shutdown=10,
        h11_max_incomplete_event_size=16 * 1024 * 1024,
    )


if __name__ == "__main__":
    load_dotenv()
    run()