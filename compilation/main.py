"""
main.py — Resume LaTeX Compilation Service

Two endpoints:
  POST /render  — Accept tailored resume JSON + template_key, fetch template
                  from S3, render Jinja2 → .tex, store in S3, return hash.
  POST /compile — Accept .tex content + thread_id, compile via pdflatex,
                  upload PDF to S3, return pdf_key.
"""

import os
import hashlib
import base64
import shutil
import tempfile
import logging
import time

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from latex_bridge import render_resume, compile_pdf
from logging_config import setup_logging

# Import guardrails for LaTeX injection protection
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
try:
    from guardrails import sanitize_latex
except ImportError:
    # Fallback: no-op sanitizer if guardrails module is not available
    def sanitize_latex(tex: str) -> str:  # type: ignore
        return tex

load_dotenv()

# Structured JSON logging setup FIRST
setup_logging("compilation")
logger = logging.getLogger("compilation-service")

# ── Config ──────────────────────────────────────────────────────────────────

STORAGE_SERVICE_URL = os.getenv("STORAGE_SERVICE_URL", "http://localhost:8001")
# Sanitize: strip quotes and ensure http://
STORAGE_SERVICE_URL = STORAGE_SERVICE_URL.replace('"', "").replace("'", "").strip()
if not STORAGE_SERVICE_URL.startswith("http"):
    STORAGE_SERVICE_URL = f"http://{STORAGE_SERVICE_URL}"

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3001")

# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(title="Resume LaTeX Compilation Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, BACKEND_URL],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Initialise OTel SDK (traces + metrics + compile histogram)
from telemetry import setup_telemetry, get_compile_histogram
setup_telemetry(app)
_compile_histogram = get_compile_histogram()

# ── Auth ─────────────────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")
    return x_api_key

# ── Storage Helpers ───────────────────────────────────────────────────────────

def _storage_download(key: str) -> bytes:
    """Download a file from the Storage Service by key."""
    url = f"{STORAGE_SERVICE_URL}/download/{key}"
    resp = httpx.get(url, headers={"X-API-Key": INTERNAL_API_KEY}, timeout=30)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Storage key not found: {key}")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Storage error: {resp.text}")
    return resp.content


def _storage_upload_bytes(key: str, content: bytes, content_type: str) -> str:
    """Upload raw bytes to the Storage Service. Returns the key."""
    content_b64 = base64.b64encode(content).decode("utf-8")
    url = f"{STORAGE_SERVICE_URL}/upload-base64"
    resp = httpx.post(
        url,
        json={"key": key, "content_b64": content_b64, "content_type": content_type},
        headers={"X-API-Key": INTERNAL_API_KEY, "Content-Type": "application/json"},
        timeout=60,
    )
    if not resp.is_success:
        raise HTTPException(status_code=502, detail=f"Storage upload error: {resp.text}")
    return key

# ── Request / Response Models ─────────────────────────────────────────────────

class RenderRequest(BaseModel):
    tailored_data: dict
    template_key: str
    thread_id: str


class RenderResponse(BaseModel):
    tex_key: str
    latex_hash: str
    success: bool


class CompileRequest(BaseModel):
    tex_content: str
    thread_id: str


class CompileResponse(BaseModel):
    pdf_key: str
    success: bool

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/render", response_model=RenderResponse, dependencies=[Depends(verify_api_key)])
async def render_latex(req: RenderRequest):
    """
    Render a Jinja2 LaTeX template with tailored resume data.

    1. Fetches the template from S3 using template_key.
    2. Renders the Jinja2 template with the tailored data.
    3. Computes MD5 hash of the rendered .tex.
    4. Uploads the rendered .tex to S3.
    5. Returns { tex_key, latex_hash }.
    """
    logger.info("[/render] thread_id=%s, template_key=%s", req.thread_id, req.template_key)

    # 1. Fetch template from Storage Service
    try:
        template_bytes = _storage_download(req.template_key)
        template_content = template_bytes.decode("utf-8")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch template: {e}")

    # 2. Render
    try:
        rendered_tex = render_resume(template_content, req.tailored_data)
    except Exception as e:
        logger.error("[/render] Render failed: %s", e, exc_info=True)
        raise HTTPException(status_code=422, detail=f"Render failed: {e}")

    # 3. Compute MD5 hash
    latex_hash = hashlib.md5(rendered_tex.encode("utf-8")).hexdigest()
    logger.info("[/render] latex_hash=%s", latex_hash)

    # 4. Upload .tex to Storage Service
    tex_key = f"thread_latex/{req.thread_id}.tex"
    try:
        _storage_upload_bytes(tex_key, rendered_tex.encode("utf-8"), "text/plain")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to upload .tex: {e}")

    logger.info("[/render] .tex uploaded: %s", tex_key)
    return RenderResponse(tex_key=tex_key, latex_hash=latex_hash, success=True)


@app.post("/compile", response_model=CompileResponse, dependencies=[Depends(verify_api_key)])
async def compile_latex(req: CompileRequest):
    """
    Compile a .tex string to PDF using pdflatex.

    1. Writes .tex to a temp dir.
    2. Runs pdflatex (3 passes).
    3. Uploads the resulting PDF to S3.
    4. Returns { pdf_key }.
    """
    logger.info("[/compile] thread_id=%s, tex_length=%d", req.thread_id, len(req.tex_content))

    temp_dir = tempfile.mkdtemp(prefix="resume_compile_")
    try:
        start = time.perf_counter()
        pdf_path = compile_pdf(req.tex_content, temp_dir)
        compile_duration = time.perf_counter() - start
        _compile_histogram.record(
            compile_duration,
            attributes={"thread_id": req.thread_id},
        )

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        timestamp = int(time.time())
        pdf_key = f"thread_pdfs/{req.thread_id}-{timestamp}.pdf"
        _storage_upload_bytes(pdf_key, pdf_bytes, "application/pdf")

        logger.info(
            "PDF compiled and uploaded",
            extra={"pdf_key": pdf_key, "compile_duration_s": round(compile_duration, 2)},
        )
        return CompileResponse(pdf_key=pdf_key, success=True)

    except RuntimeError as e:
        logger.error("[/compile] pdflatex error: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[/compile] Unexpected error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Compilation error: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
