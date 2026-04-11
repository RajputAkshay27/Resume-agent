import uvicorn
import os
import json
import logging
import urllib.request
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticBaseModel
from agent import create_agent
from dotenv import load_dotenv
from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from latex_bridge import validate_template

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
        cleanup_interval_seconds=360000,
        session_service=session_service,
        use_in_memory_services=False
    )

    app = FastAPI(title='Resume Builder Agent - Multi-Agent Pipeline')

    # Allow Next.js frontend to call endpoints directly
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3001")
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
    async def get_history(threadId: str = None):
        """Return the chat history for a given CopilotKit threadId."""
        if not threadId:
            return JSONResponse(content={"messages": []})

        AGENT_NAME = "resume_builder_agent"
        user_id = f"thread_user_{threadId}"
        print(f"[DEBUG] Fetching history for threadId: {threadId} (user_id: {user_id})")

        # --- SESSION LOGGING START ---
        # As requested: List all sessions and the number of events in them
        try:
            all_sessions_response = await session_service.list_sessions(app_name=AGENT_NAME)
            all_sessions = all_sessions_response.sessions if all_sessions_response and all_sessions_response.sessions else []
            print(f"[HISTORY LOG] Database contains {len(all_sessions)} total sessions.")
            for s in all_sessions:
                try:
                    # Get full session to count events
                    full_session = await session_service.get_session(
                        app_name=AGENT_NAME, user_id=s.user_id, session_id=s.id
                    )
                    event_count = len(getattr(full_session, "events", []) or [])
                    print(f"[HISTORY LOG] Session: {s.id} | User: {s.user_id} | Events: {event_count}")
                except Exception as sess_err:
                    print(f"[HISTORY LOG] Error reading session {s.id}: {sess_err}")
        except Exception as e:
            print(f"[HISTORY LOG] Failed to list all sessions: {e}")
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

            print(f"[DEBUG] Processing {len(all_events)} total events across {len(sessions)} sessions for {threadId}")

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

            print(f"[DEBUG] Recovered {len(messages)} CLEAN messages for {threadId}")
            return JSONResponse(content={"messages": messages})

        except Exception as e:
            print(f"[ERROR] History failure: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(content={"messages": [], "error": str(e)})
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