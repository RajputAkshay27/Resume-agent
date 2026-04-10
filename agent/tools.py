"""
Agent tools for the Resume Builder multi-agent system.

These tools bridge the Python agent with the Next.js frontend APIs and
the structured generation / LaTeX compilation pipeline.
"""

import os
import json
import logging
import urllib.request

from google.adk.tools.tool_context import ToolContext
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from pydantic import ValidationError
from schemas import SectionPreferences, TailoredResume
from latex_bridge import validate_template as _validate_template

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_thread_id(tool_context: ToolContext) -> str:
    """Get the CopilotKit thread ID from the agent session state."""
    tid = tool_context.state.get("_ag_ui_thread_id")
    if not tid:
        tid = tool_context._invocation_context.session.id
    return tid


def _internal_get(path: str) -> dict:
    """Authenticated GET to the Next.js internal API."""
    secret = os.getenv("NEXTAUTH_SECRET", "super_secret_temporary_key_replace_me_in_production")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3001").rstrip("/")
    url = f"{frontend_url}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _internal_post(path: str, data: dict) -> dict:
    """Authenticated POST to the Next.js internal API."""
    secret = os.getenv("NEXTAUTH_SECRET", "super_secret_temporary_key_replace_me_in_production")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3001").rstrip("/")
    url = f"{frontend_url}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _ensure_dict(val) -> dict:
    """Ensure that the value is a dictionary, parsing it if it is a string.
    Handles double-encoded JSON, and list-wrapped JSON which often occur
    in Prisma/SQLite or when the LLM returns an array."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, list):
        if len(val) > 0:
            return _ensure_dict(val[0])
        return {}
    if isinstance(val, str):
        # Try parsing up to 3 layers of JSON nesting
        current = val
        for _ in range(3):
            try:
                parsed = json.loads(current.strip())
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    if len(parsed) > 0:
                        current = parsed[0] # unwrap and continue
                    else:
                        return {}
                elif isinstance(parsed, str):
                    current = parsed  # keep unwrapping
                else:
                    break
            except (json.JSONDecodeError, TypeError):
                break
    return {}


def _coerce_prefs(raw: dict) -> dict:
    """Coerce section preference values to their correct Python types.
    Handles cases where 'true'/'false' are stored as strings and
    count values come as strings like '2' instead of int 2."""
    coerced = {}
    for key, value in raw.items():
        if key in ("include_summary", "include_skills"):
            if isinstance(value, str):
                coerced[key] = value.lower() in ("true", "1", "yes")
            elif isinstance(value, bool):
                coerced[key] = value
            else:
                coerced[key] = bool(value)
        elif key.endswith("_count"):
            if value is None or value == "null" or value == "":
                coerced[key] = None
            elif isinstance(value, str):
                try:
                    coerced[key] = int(value)
                except ValueError:
                    coerced[key] = None
            else:
                coerced[key] = value
        else:
            coerced[key] = value
    return coerced




# ---------------------------------------------------------------------------
# Tool: Read State
# ---------------------------------------------------------------------------

def read_state(tool_context: ToolContext):
    """Read your internal session state to view the stored master profile, JD, section preferences, and tailored resume data."""
    try:
        scratchpad = tool_context.state.get("scratchpad") or {}
        tailored_resume = tool_context.state.get("tailored_resume")

        result = {}

        # Surface scratchpad contents at the top level for readability
        if scratchpad.get("master_profile"):
            profile = scratchpad["master_profile"]
            result["master_profile_summary"] = {
                "experience_count": len(profile.get("experience", [])),
                "project_count": len(profile.get("projects", [])),
                "achievement_count": len(profile.get("achievements", [])),
                "skills": list(profile.get("skills", {}).keys()),
            }
        if scratchpad.get("job_description"):
            jd = scratchpad["job_description"]
            result["job_description_preview"] = jd[:300] + "..." if len(jd) > 300 else jd
        if scratchpad.get("section_prefs"):
            result["section_prefs"] = scratchpad["section_prefs"]

        # Expose tailored_resume existence and summary (lives at top-level state, set by ADK output_key)
        if tailored_resume:
            if isinstance(tailored_resume, dict):
                data = tailored_resume
            elif hasattr(tailored_resume, "model_dump"):
                data = tailored_resume.model_dump()
            else:
                data = {}
            result["tailored_resume"] = {
                "status": "present",
                "experience_count": len(data.get("experience", [])),
                "project_count": len(data.get("projects", [])),
                "achievement_count": len(data.get("achievements", [])),
                "has_summary": bool(data.get("tailored_summary")),
                "has_skills": bool(data.get("skills")),
            }
        else:
            result["tailored_resume"] = "NOT PRESENT — tailoring must be run before compilation"

        if not result:
            return "Session state is empty. No data has been loaded yet."

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("[read_state] Unhandled error: %s", e, exc_info=True)
        return f"Error reading session state: {str(e)}. Please retry."



# ---------------------------------------------------------------------------
# Tool: Render and Compile PDF
# ---------------------------------------------------------------------------

def render_latex(tool_context: ToolContext):
    """Render the tailored resume data into a Jinja2 LaTeX template and store the .tex file in S3.
    The PDF will be compiled on-demand when the user clicks the download button."""
    try:
        tailored = tool_context.state.get("tailored_resume")
        if not tailored:
            return "No tailored resume data in state. Please make sure the tailoring_agent has successfully generated it."

        thread_id = _get_thread_id(tool_context)

        # 1. Fetch context (template_key and tailored profile) from the frontend API
        data = _internal_get(f"/api/internal/context?threadId={thread_id}")
        template_key = data.get("templateKey")
        if not template_key:
            return "No LaTeX template found. Please upload a template in the context panel (Edit JD → Chat Template)."

        # 2. Validate and resolve the tailored resume
        try:
            # This handles 'unwrap_list' and strict type checking
            validated_resume = TailoredResume(**tailored) if isinstance(tailored, dict) else tailored
            tailored_dict = validated_resume.model_dump(exclude_none=True)
        except ValidationError as e:
            return f"Tailored resume validation failed. The format is incorrect: {e}"
        except Exception as e:
            return f"Could not parse tailored resume from state: {e}"

        # 3. Call the LaTeX Service /render endpoint
        latex_service_url = os.getenv("LATEX_SERVICE_URL", "http://localhost:8002")
        latex_service_url = latex_service_url.replace('"', '').replace("'", '').strip()
        if not latex_service_url.startswith("http"):
            latex_service_url = f"http://{latex_service_url}"

        api_key = os.getenv("INTERNAL_API_KEY", "default_secret_key_change_me")
        payload = {
            "tailored_data": tailored_dict,
            "template_key": template_key,
            "thread_id": thread_id,
        }
        body = json.dumps(payload).encode("utf-8")
        render_req = urllib.request.Request(
            f"{latex_service_url}/render",
            data=body,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(render_req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            return f"LaTeX render failed (HTTP {e.code}): {error_body}"
        except Exception as e:
            return f"Could not reach LaTeX Service: {e}"

        tex_key = result.get("tex_key")
        latex_hash = result.get("latex_hash")

        # 4. Persist tailored profile + tex metadata in the frontend DB
        _internal_post("/api/internal/context", {
            "threadId": thread_id,
            "tailoredProfile": json.dumps(tailored_dict),
            "texKey": tex_key,
            "latexHash": latex_hash,
        })

        return (
            f"LaTeX rendered and stored successfully!\n"
            f"Hash: {latex_hash}\n"
            f"The user can now download the PDF or LaTeX source from the header buttons."
        )

    except Exception as e:
        logger.error("[render_latex] Unhandled error: %s", e, exc_info=True)
        return f"Error in render_latex: {str(e)}. Please retry."

# ---------------------------------------------------------------------------
# Tool for Tailoring Agent: Single combined context fetch
# ---------------------------------------------------------------------------

def get_all_context(tool_context: ToolContext) -> str:
    """Fetch ALL context needed for resume tailoring in a single call: the master profile, job description, and user preferences. Call this ONCE to get everything."""
    try:
        thread_id = _get_thread_id(tool_context)
        try:
            data = _internal_get(f"/api/internal/context?threadId={thread_id}")
        except Exception as e:
            return f"Error fetching context: {e}"

        # --- Master Profile ---
        profile_dict = _ensure_dict(data.get("masterProfile", {}))
        if not profile_dict:
            return "No Master Profile found. Please ask the user to fill in their profile at /profile."

        # --- Job Description ---
        jd = data.get("jobDescription", "")
        if not jd:
            return "No Job Description found for this thread. Please ask the user to paste a JD in the context panel."

        # --- User Preferences ---
        raw_prefs = _ensure_dict(data.get("sectionPrefs", {}))
        prefs_dict = _coerce_prefs(raw_prefs)

        pref_lines = []
        if prefs_dict.get("experience_count") is not None:
            pref_lines.append(f"- Experience: Select exactly {prefs_dict['experience_count']} items.")
        if prefs_dict.get("project_count") is not None:
            pref_lines.append(f"- Projects: Select exactly {prefs_dict['project_count']} items.")
        if prefs_dict.get("achievement_count") is not None:
            pref_lines.append(f"- Achievements: Select exactly {prefs_dict['achievement_count']} items.")
        if not prefs_dict.get("include_summary", True):
            pref_lines.append("- Summary: Do NOT include a summary.")
        if not prefs_dict.get("include_skills", True):
            pref_lines.append("- Skills: Do NOT include skills.")
        if prefs_dict.get("custom_instructions"):
            pref_lines.append(f"- Special User Custom Instructions: {prefs_dict['custom_instructions']}")
        prefs_text = "\n".join(pref_lines) if pref_lines else "No strict quantity limits specified."

        # --- Persist to scratchpad for enforce_preferences callback ---
        scratchpad = tool_context.state.get("scratchpad") or {}
        scratchpad["master_profile"] = profile_dict
        scratchpad["job_description"] = jd
        scratchpad["section_prefs"] = prefs_dict
        tool_context.state["scratchpad"] = scratchpad

        # --- Build combined response ---
        exp_count = len(profile_dict.get("experience", []))
        proj_count = len(profile_dict.get("projects", []))

        return f"""## MASTER PROFILE
{json.dumps(profile_dict, indent=2)}

## JOB DESCRIPTION
{jd}

## USER PREFERENCES ({exp_count} experiences, {proj_count} projects available in profile)
{prefs_text}"""
    except Exception as e:
        logger.error("[get_all_context] Unhandled error: %s", e, exc_info=True)
        return f"Error fetching context data: {str(e)}. Please retry."


def submit_tailored_resume(payload: dict, tool_context: ToolContext) -> str:
    """
    Submit the finalized tailored resume data for validation and storage. 
    Call this tool ONLY AFTER you have fetched all context via get_all_context 
    and analyzed the job description against the master profile.
    
    Args:
        payload (dict): A dictionary matching the TailoredResume schema. 
                        Must include summary, experience, skills and other things based on user prefrence.
    """
    try:

        # Validate the payload against the schema
        # We don't save to state here; the after_model_callback handles that
        # to ensure consistency with the tool's return value.
        TailoredResume(**payload)
        return "SUCCESS: Resume formatted correctly and accepted."
    except ValidationError as e:
        # Extract the error details to send back to the LLM
        errors = []
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error['loc'])
            msg = error['msg']
            errors.append(f"- {loc}: {msg}")
        
        error_str = "\n".join(errors)
        logger.warning("TailoredResume validation failed: %s", error_str)
        return (
            f"FAILURE: Resume validation failed with the following errors:\n{error_str}\n\n"
            "Please fix these fields/missing components and call `submit_tailored_resume` again with the FULL corrected payload."
        )
    except Exception as e:
        logger.error("Unexpected error in submit_tailored_resume: %s", e)
        return f"FAILURE: An unexpected error occurred during validation: {str(e)}"


# ---------------------------------------------------------------------------
# Agent Callbacks / Logic
# ---------------------------------------------------------------------------

def capture_tailored_output(
        callback_context: CallbackContext,
        llm_response: LlmResponse,
) -> None:
    """after_model_callback: Inspect tool calls for submit_tailored_resume
    and manually persist the validated payload to state.
    """
    if not llm_response or not llm_response.content:
        return None

    parts = llm_response.content.parts or []
    for part in parts:
        # Each part might contain a function_call object
        call = getattr(part, "function_call", None)
        if call and call.name == "submit_tailored_resume":
            # Extract payload from the tool call arguments
            payload = call.args.get("payload") if call.args else None
            if not payload:
                continue
            
            try:
                resume = TailoredResume(**payload)
                data = resume.model_dump(exclude_none=True)
                

                callback_context.state["tailored_resume"] = data
            except Exception as e:
                # We don't log this as an error because the tool function itself 
                # handles sending the error description back to the LLM for a retry.
                logger.debug("capture_tailored_output: tool call validation failed (LLM will retry): %s", e)
    
    return None

def enforce_preferences(callback_context: CallbackContext) -> None:
    """after_agent_callback: Enforce section preferences by truncating
    any extra items the LLM may have produced.

    At this point, tailored_resume SHOULD be in state (written by
    capture_tailored_output). If it's missing, there's nothing to enforce.
    """
    tailored = callback_context.state.get("tailored_resume")
    

    if not tailored:
        return

    raw_prefs = callback_context.state.get("scratchpad", {}).get("section_prefs", {})
    prefs = SectionPreferences(**raw_prefs) if raw_prefs else SectionPreferences()

    if hasattr(tailored, "model_dump"):
        data = tailored.model_dump()
    elif isinstance(tailored, dict):
        data = dict(tailored)
    else:
        return

    changed = False
    if prefs.experience_count is not None and len(data.get("experience", [])) > prefs.experience_count:
        data["experience"] = data["experience"][: prefs.experience_count]
        changed = True
    if prefs.project_count is not None and len(data.get("projects", [])) > prefs.project_count:
        data["projects"] = data["projects"][: prefs.project_count]
        changed = True
    if prefs.achievement_count is not None and len(data.get("achievements", [])) > prefs.achievement_count:
        data["achievements"] = data["achievements"][: prefs.achievement_count]
        changed = True
    if not prefs.include_summary and data.get("summary"):
        data["summary"] = ""  # Set to empty string instead of None to satisfy compulsory schema
        changed = True
    if not prefs.include_skills and data.get("skills"):
        data["skills"] = {}
        changed = True

    
    if changed:
        try:
            validated = TailoredResume(**data)
            callback_context.state["tailored_resume"] = validated.model_dump(exclude_none=True)
        except Exception as e:
            logger.error("enforce_preferences: VALIDATION FAILED after enforcement: %s", e)
    pass
