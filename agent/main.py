import uvicorn
import os
import json
import logging
import urllib.request
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticBaseModel
from logging_config import setup_logging
from agent import create_agent
from dotenv import load_dotenv
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from typing import Optional
import uuid
import tempfile
import base64

from latex_bridge import validate_template

# Set up structured JSON logging FIRST — before anything that touches logging
setup_logging("agent")
logger = logging.getLogger(__name__)


class ValidateTemplateRequest(PydanticBaseModel):
    template_content: str


def run():
    agent = create_agent()

    # Use SQLite for persistent session storage across restarts
    # Ensure the data directory exists
    os.makedirs("data", exist_ok=True)
    session_service = SqliteSessionService("sqlite:///data/sessions.db")

    resume_building_agent = ADKAgent(
        adk_agent=agent,
        app_name="resume_builder_agent",
        session_timeout_seconds=3600,
        cleanup_interval_seconds=360000,
        session_service=session_service,
        use_in_memory_services=False
    )

    app = FastAPI(title='Resume Builder Agent - Multi-Agent Pipeline')

    # Allow Next.js frontend to call endpoints directly
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialise OTel SDK (traces + metrics + auto-instrumentation)
    from telemetry import setup_telemetry
    setup_telemetry(app)

    # ------------------------------------------------------------------
    # Global exception handler — catch unhandled errors before they
    # corrupt the SSE event stream with unexpected 500 responses
    # ------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s",
            request.method, str(request.url),
            exc_info=True,
            extra={"error": str(exc)},
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "detail": str(exc)},
        )

    # ------------------------------------------------------------------
    # GET /history — Chat history for CopilotKit
    # ------------------------------------------------------------------
    @app.get("/history")
    async def get_history(threadId: str = None):
        """Return the chat history for a given CopilotKit threadId."""
        if not threadId:
            return JSONResponse(content={"messages": []})

        AGENT_NAME = "resume_builder_agent"
        user_id = f"thread_user_{threadId}"
        logger.debug("Fetching history", extra={"thread_id": threadId, "user_id": user_id})

        # --- SESSION LOGGING START ---
        # As requested: List all sessions and the number of events in them
        try:
            all_sessions_response = await session_service.list_sessions(app_name=AGENT_NAME)
            all_sessions = all_sessions_response.sessions if all_sessions_response and all_sessions_response.sessions else []
            logger.debug("Session count", extra={"total_sessions": len(all_sessions)})
            for s in all_sessions:
                try:
                    full_session = await session_service.get_session(
                        app_name=AGENT_NAME, user_id=s.user_id, session_id=s.id
                    )
                    event_count = len(getattr(full_session, "events", []) or [])
                    logger.debug(
                        "Session summary",
                        extra={"session_id": s.id, "user_id": s.user_id, "event_count": event_count},
                    )
                except Exception as sess_err:
                    logger.warning("Error reading session", extra={"session_id": s.id, "error": str(sess_err)})
        except Exception as e:
            logger.warning("Failed to list all sessions", extra={"error": str(e)})
        # --- SESSION LOGGING END ---

        # Heuristic phrases that indicate agent reasoning rather than user-facing response
        SKIP_PHRASES = [
            "The user wants", "According to the", "Following my", "Plan:", 
            "Step 1:", "I will now", "I must:", "My workflow is:", 
            "Looking at the context", "The read_state call", "I will call", 
            "I encountered an internal", "Corrected Payload Construction:", 
            "The previous submission failed"
        ]

        def extract_text(content):
            """Safely extract and clean user-facing text from complex AG-UI content."""
            if not content:
                return ""
            if isinstance(content, str):
                return content
            
            parts = []
            if hasattr(content, "parts") and content.parts:
                parts = content.parts
            elif isinstance(content, dict):
                parts = content.get("parts", [])
            
            if not parts:
                try: 
                    return str(content) if not isinstance(content, dict) else ""
                except: 
                    return ""

            if not isinstance(parts, (list, tuple)):
                parts = [parts]

            text_bits = []
            for p in parts:
                if not p: continue
                try:
                    # 1. Broad metadata check for thoughts/tool-calls
                    is_internal = False
                    if isinstance(p, dict):
                        is_internal = any(p.get(k) for k in ["thought", "function_call", "function_response", "invocation_id"])
                    else:
                        is_internal = any(getattr(p, k, None) for k in ["thought", "function_call", "function_response", "invocation_id"])
                    
                    if is_internal:
                        continue

                    # 2. String conversion
                    val = None
                    if isinstance(p, str): 
                        val = p
                    elif hasattr(p, "text"): 
                        val = getattr(p, "text", None)
                    elif isinstance(p, dict): 
                        val = p.get("text") or p.get("content")
                    
                    if val is not None:
                        val_str = str(val).strip()
                        # 3. Heuristic Skip
                        if any(val_str.startswith(phrase) for phrase in SKIP_PHRASES):
                            continue
                        text_bits.append(val_str)
                except:
                    continue

            return "\n".join([t for t in text_bits if t]).strip()

        try:
            # 1. Fetch ALL sessions for this thread user
            response = await session_service.list_sessions(app_name=AGENT_NAME, user_id=user_id)
            sessions = response.sessions if (response and response.sessions) else []

            if not sessions:
                return JSONResponse(content={"messages": []})

            # 2. Collect & deduplicate events from EVERY session
            seen_ids = set()
            all_events = []
            
            for s in sessions:
                try:
                    full_s = await session_service.get_session(
                        app_name=AGENT_NAME, user_id=user_id, session_id=s.id
                    )
                    if not full_s:
                        continue
                        
                    for ev in (getattr(full_s, "events", []) or []):
                        ev_id = getattr(ev, "id", None)
                        if ev_id and ev_id in seen_ids:
                            continue
                        if ev_id:
                            seen_ids.add(ev_id)
                        all_events.append(ev)
                except Exception:
                    continue

            logger.debug(
                "Processing events",
                extra={"thread_id": threadId, "total_events": len(all_events), "sessions": len(sessions)},
            )

            # ==========================================
            # THE FIX: SORT EVENTS CHRONOLOGICALLY
            # ==========================================
            def get_timestamp(event):
                # Try common timestamp attributes from Google Agent SDKs
                ts = getattr(event, "create_time", None) or getattr(event, "timestamp", None)
                if isinstance(ts, dict): # Handle protobuf timestamp dicts if applicable
                    return ts.get("seconds", 0)
                return ts or 0

            # Sort from oldest to newest so the UI renders top-to-bottom correctly
            all_events.sort(key=get_timestamp)

            # 3. Process events into clean user-facing messages
            messages = []
            for event in all_events:
                try:
                    author = getattr(event, "author", None)
                    if not author:
                        continue

                    is_p = getattr(event, "partial", False) if not isinstance(event, dict) else event.get("partial", False)
                    is_t = getattr(event, "thought", False) if not isinstance(event, dict) else event.get("thought", False)
                    if is_p or is_t:
                        continue

                    if author in ["compilation_agent", "tailoring_agent"] and is_p:
                        continue

                    text = extract_text(getattr(event, "content", None) if not isinstance(event, dict) else event.get("content"))
                    if not text:
                        continue

                    role = "user" if author == "user" else "assistant"
                    messages.append({
                        "id": getattr(event, "id", None) or f"{role}-{len(messages)}",
                        "role": role,
                        "content": text,
                    })
                except Exception:
                    continue

            logger.info(
                "History recovered",
                extra={"thread_id": threadId, "message_count": len(messages)},
            )
            return JSONResponse(content={"messages": messages})

        except Exception as e:
            logger.error("History failure", exc_info=True, extra={"error": str(e)})
            return JSONResponse(content={"messages": [], "error": str(e)})

    # ------------------------------------------------------------------
    # POST /validate-template — Validate a Jinja2 template
    # ------------------------------------------------------------------
    @app.post("/validate-template")
    async def validate_template_endpoint(req: ValidateTemplateRequest):
        """Validate a Jinja2 LaTeX template and return analysis."""
        result = validate_template(req.template_content)
        return JSONResponse(content=result)
    # ------------------------------------------------------------------
    # POST /api/tailor — Direct stateless resume tailoring and compilation
    # ------------------------------------------------------------------
    class TailorRequest(PydanticBaseModel):
        master_profile: Optional[dict] = None
        job_description: Optional[str] = None
        preferences: Optional[dict] = None
        template_content: Optional[str] = None

    @app.post("/api/tailor")
    async def api_tailor(req: TailorRequest):
        # 1. Create a temp directory to hold the inputs and output
        temp_dir = tempfile.mkdtemp(prefix="resume_tailor_")
        
        # Preserve original env variables
        orig_env = {
            "MASTER_PROFILE_PATH": os.getenv("MASTER_PROFILE_PATH"),
            "JOB_DESCRIPTION_PATH": os.getenv("JOB_DESCRIPTION_PATH"),
            "PREFERENCES_PATH": os.getenv("PREFERENCES_PATH"),
            "RESUME_TEMPLATE_PATH": os.getenv("RESUME_TEMPLATE_PATH"),
            "OUTPUT_DIR": os.getenv("OUTPUT_DIR"),
        }
        
        try:
            # Write inputs to temp directory
            master_profile_path = os.path.join(temp_dir, "master_profile.json")
            job_description_path = os.path.join(temp_dir, "job_description.txt")
            preferences_path = os.path.join(temp_dir, "preferences.json")
            template_path = os.path.join(temp_dir, "resume_template.tex")
            
            # Load local fallbacks if not provided in the request
            master_profile = req.master_profile
            if not master_profile:
                from tools import _load_local_profile
                master_profile = _load_local_profile()
                
            job_description = req.job_description
            if not job_description:
                from tools import _load_local_jd
                job_description = _load_local_jd()
                
            preferences = req.preferences
            if not preferences:
                from tools import _load_local_prefs
                preferences = _load_local_prefs()
                
            template_content = req.template_content
            if not template_content:
                from tools import _load_local_template
                template_content = _load_local_template()

            if not master_profile:
                raise HTTPException(status_code=400, detail="Master profile is empty or not found.")
            if not job_description:
                raise HTTPException(status_code=400, detail="Job description is empty or not found.")
            if not template_content:
                raise HTTPException(status_code=400, detail="Template content is empty or not found.")

            # Save files to temp dir
            with open(master_profile_path, "w", encoding="utf-8") as f:
                json.dump(master_profile, f, indent=2)
            with open(job_description_path, "w", encoding="utf-8") as f:
                f.write(job_description)
            with open(preferences_path, "w", encoding="utf-8") as f:
                json.dump(preferences or {}, f, indent=2)
            with open(template_path, "w", encoding="utf-8") as f:
                f.write(template_content)

            # Override env variables
            os.environ["MASTER_PROFILE_PATH"] = master_profile_path
            os.environ["JOB_DESCRIPTION_PATH"] = job_description_path
            os.environ["PREFERENCES_PATH"] = preferences_path
            os.environ["RESUME_TEMPLATE_PATH"] = template_path
            os.environ["OUTPUT_DIR"] = temp_dir
            
            # Temporarily clear FRONTEND_URL to force local fallback
            orig_frontend_url = os.environ.get("FRONTEND_URL")
            if "FRONTEND_URL" in os.environ:
                del os.environ["FRONTEND_URL"]

            # 2. Run the agent using the ADK Runner
            from google.adk import Runner
            from google.genai import types
            
            # Create runner
            runner = Runner(
                app_name="resume_builder_agent",
                agent=agent,
                session_service=session_service,
                auto_create_session=True,
            )
            
            session_id = str(uuid.uuid4())
            user_id = f"thread_user_{session_id}"
            
            logger.info("Starting local tailoring agent run...")
            
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(parts=[types.Part.from_text(text="Tailor my resume for the job description and compile the final PDF.")]),
            ):
                logger.debug("Tailor run event: %s", event)

            # Restore FRONTEND_URL if it was set
            if orig_frontend_url is not None:
                os.environ["FRONTEND_URL"] = orig_frontend_url

            # 3. Read output files
            tex_path = os.path.join(temp_dir, "resume.tex")
            pdf_path = os.path.join(temp_dir, "resume.pdf")
            
            if not os.path.exists(tex_path):
                session = await session_service.get_session(app_name="resume_builder_agent", user_id=user_id, session_id=session_id)
                tailored_resume = session.state.get("tailored_resume") if session else None
                if tailored_resume:
                    from latex_bridge import render_resume, compile_pdf
                    try:
                        from schemas import TailoredResume
                        validated_resume = TailoredResume(**tailored_resume)
                        rendered_tex = render_resume(template_content, validated_resume)
                        with open(tex_path, "w", encoding="utf-8", newline="") as f:
                            f.write(rendered_tex)
                        try:
                            pdf_path = compile_pdf(rendered_tex, temp_dir)
                        except Exception as e:
                            logger.warning("Post-run compilation failed: %s", e)
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=f"Failed to generate LaTeX from tailored resume state: {e}")
                else:
                    raise HTTPException(status_code=500, detail="Tailoring agent completed but did not produce a tailored resume.")

            # Read generated files
            with open(tex_path, "r", encoding="utf-8") as f:
                rendered_tex = f.read()
                
            pdf_base64 = None
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    pdf_base64 = base64.b64encode(f.read()).decode("utf-8")
                    
            # Read final state from session service
            session = await session_service.get_session(app_name="resume_builder_agent", user_id=user_id, session_id=session_id)
            tailored_resume = session.state.get("tailored_resume") if session else {}

            return {
                "tailored_resume": tailored_resume,
                "rendered_tex": rendered_tex,
                "compiled_pdf_base64": pdf_base64,
                "success": True
            }

        except Exception as e:
            logger.error("Stateless tailoring API error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
            
        finally:
            # Restore original environment
            for k, v in orig_env.items():
                if v is None:
                    if k in os.environ:
                        del os.environ[k]
                else:
                    os.environ[k] = v
                    
            # Clean up temp directory
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

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