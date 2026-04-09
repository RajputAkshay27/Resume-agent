import uvicorn
import os
import json
import hashlib
import base64
import shutil
import tempfile
import logging
import urllib.request
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticBaseModel
from agent import create_agent
from dotenv import load_dotenv
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from schemas import TailoredResume
from latex_bridge import render_resume, compile_pdf, validate_template

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ValidateTemplateRequest(PydanticBaseModel):
    template_content: str


def run():
    agent = create_agent()

    # Use SQLite for persistent session storage across restarts
    session_service = SqliteSessionService("sqlite:///sessions.db")

    resume_building_agent = ADKAgent(
        adk_agent=agent,
        app_name="resume_builder_agent",
        session_timeout_seconds=3600,
        session_service=session_service,
        use_in_memory_services=False
    )

    app = FastAPI(title='Resume Builder Agent - Multi-Agent Pipeline')

    # Allow Next.js frontend to call endpoints directly
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_url],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Global exception handler — catch unhandled errors before they
    # corrupt the SSE event stream with unexpected 500 responses
    # ------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method, request.url, exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "detail": str(exc)},
        )

    # ------------------------------------------------------------------
    # GET /history — Chat history for CopilotKit
    # ------------------------------------------------------------------
    @app.get("/history")
    async def get_history(threadId: str):
        """Return the chat history for a given CopilotKit threadId."""
        AGENT_NAME = "resume_builder_agent"
        user_id = f"thread_user_{threadId}"
        try:
            response = await session_service.list_sessions(
                app_name=AGENT_NAME,
                user_id=user_id
            )
            sessions = response.sessions if response else []

            target_session = None
            for s in sessions:
                state = getattr(s, "state", None) or {}
                stored_thread = state.get("_ag_ui_thread_id")
                if stored_thread == threadId:
                    full_session = await session_service.get_session(
                        app_name=AGENT_NAME,
                        user_id=user_id,
                        session_id=s.id
                    )
                    target_session = full_session
                    break

            if not target_session:
                return JSONResponse(content={"messages": []})

            events = getattr(target_session, "events", []) or []
            messages = []
            for event in events:
                author = getattr(event, "author", None)
                content = getattr(event, "content", None)
                if not author or not content:
                    continue
                role = "user" if author == "user" else "assistant"
                parts = getattr(content, "parts", []) or []
                text_parts = [
                    p.text for p in parts
                    if hasattr(p, "text") and p.text and p.text.strip()
                ]
                if not text_parts:
                    continue
                text = "\n".join(text_parts)
                messages.append({
                    "id": getattr(event, "id", None) or f"{role}-{len(messages)}",
                    "role": role,
                    "content": text,
                })

            return JSONResponse(content={"messages": messages})

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JSONResponse(content={"messages": [], "error": str(e)})

    # ------------------------------------------------------------------
    # GET /download-resume — Compile and download PDF
    # ------------------------------------------------------------------
    @app.get("/download-resume")
    async def download_resume(threadId: str, background_tasks: BackgroundTasks, filename: str = None):
        """Compile the tailored resume to PDF using the Jinja2 template pipeline."""

        secret = os.getenv("NEXTAUTH_SECRET", "super_secret_temporary_key_replace_me_in_production")
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        url = f"{frontend_url}/api/internal/context?threadId={threadId}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})

        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch context: {e}")

        # Get the tailored profile and template
        tailored_json = data.get("tailoredProfile")
        template_content = data.get("templateContent")

        if not tailored_json:
            raise HTTPException(status_code=404, detail="No tailored resume data found. Generate one first via the chat agent.")
        if not template_content:
            raise HTTPException(status_code=404, detail="No Jinja2 LaTeX template found. Upload one in Settings.")

        # Parse the tailored data
        tailored_dict = json.loads(tailored_json) if isinstance(tailored_json, str) else tailored_json
        tailored = TailoredResume(**tailored_dict)

        # Calculate hash for caching
        rendered_tex = render_resume(template_content, tailored)
        current_hash = hashlib.md5(rendered_tex.encode("utf-8")).hexdigest()

        # Check cache
        stored_pdf_key = data.get("pdfKey")
        stored_pdf_hash = data.get("pdfHash")

        # Caching disabled per user request to ensure fresh PDF generation every time
        # if stored_pdf_key and stored_pdf_hash == current_hash:
        #     print(f"[/download-resume] Cache hit! Serving existing PDF: {stored_pdf_key}")
        #     proxy_url = f"http://localhost:3000/api/internal/context?pdfKey={stored_pdf_key}"
        #     proxy_req = urllib.request.Request(proxy_url, headers={"Authorization": f"Bearer {secret}"})
        #     try:
        #         with urllib.request.urlopen(proxy_req) as proxy_response:
        #             content = proxy_response.read()
        #             final_name = filename or "resume"
        #             if not final_name.lower().endswith(".pdf"):
        #                 final_name += ".pdf"
        # Compile PDF
        temp_dir = tempfile.mkdtemp(prefix="resume_dl_")
        try:
            pdf_path = compile_pdf(rendered_tex, temp_dir)

            # Read and upload
            with open(pdf_path, "rb") as f:
                pdf_data = f.read()
            pdf_b64 = base64.b64encode(pdf_data).decode("utf-8")
            import time
            timestamp = int(time.time())
            new_pdf_key = f"thread_pdfs/{threadId}-{timestamp}.pdf"

            # Background: delete old PDF and upload new one
            def update_storage():
                try:
                    post_data = {
                        "threadId": threadId,
                        "pdfKey": new_pdf_key,
                        "pdfHash": str(timestamp),
                        "pdfData": pdf_b64,
                    }
                    # Delete old PDF key if different
                    if stored_pdf_key and stored_pdf_key != new_pdf_key:
                        post_data["deleteOldPdfKey"] = stored_pdf_key

                    body = json.dumps(post_data).encode("utf-8")
                    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
                    update_req = urllib.request.Request(
                        f"{frontend_url}/api/internal/context",
                        data=body,
                        headers={
                            "Authorization": f"Bearer {secret}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(update_req) as r:
                        pass
                except Exception as e:
                    logger.error("[/download-resume] Storage update error: %s", e)
            background_tasks.add_task(update_storage)
            background_tasks.add_task(lambda: shutil.rmtree(temp_dir, ignore_errors=True))

            final_name = filename or "resume"
            if not final_name.lower().endswith(".pdf"):
                final_name += ".pdf"
            return FileResponse(pdf_path, media_type="application/pdf", filename=final_name)

        except RuntimeError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # GET /download-latex — Download rendered .tex (read-only)
    # ------------------------------------------------------------------
    @app.get("/download-latex")
    async def download_latex(threadId: str, filename: str = None):
        """Render the Jinja2 template with tailored data and return .tex source."""
        secret = os.getenv("NEXTAUTH_SECRET", "super_secret_temporary_key_replace_me_in_production")
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        url = f"{frontend_url}/api/internal/context?threadId={threadId}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})

        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        tailored_json = data.get("tailoredProfile")
        template_content = data.get("templateContent")

        if not tailored_json or not template_content:
            raise HTTPException(status_code=404, detail="Missing tailored data or template.")

        tailored_dict = json.loads(tailored_json) if isinstance(tailored_json, str) else tailored_json
        tailored = TailoredResume(**tailored_dict)
        rendered_tex = render_resume(template_content, tailored)

        final_name = filename or "resume"
        if not final_name.lower().endswith(".tex"):
            final_name += ".tex"
        return Response(
            content=rendered_tex,
            media_type="application/x-tex",
            headers={"Content-Disposition": f'attachment; filename="{final_name}"'},
        )

    # ------------------------------------------------------------------
    # POST /validate-template — Validate a Jinja2 template
    # ------------------------------------------------------------------
    @app.post("/validate-template")
    async def validate_template_endpoint(req: ValidateTemplateRequest):
        """Validate a Jinja2 LaTeX template and return analysis."""
        result = validate_template(req.template_content)
        return JSONResponse(content=result)

    # IMPORTANT: mount the agent catch-all AFTER specific routes
    add_adk_fastapi_endpoint(app, resume_building_agent, path="/")

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