"""
tools.py — Agent tools for the Resume Builder v2 multi-agent system.

Changes from v1:
  - Removed: _load_local_profile/jd/prefs/template (replaced by storage_client)
  - Removed: _internal_get / _internal_post (dead Next.js frontend API calls)
  - Removed: all urllib.request usage
  - Added:   storage_client integration (SQLite-first)
  - Updated: get_all_context() — token-optimized, only sends TailoredSections-relevant data,
             prunes context based on SectionPreferences, uses compact format
  - Renamed: submit_tailored_resume → submit_tailored_sections (validates TailoredSections only)
  - Updated: render_latex() — merges StaticProfile + TailoredSections via FullResume compositor
  - Added:   guardrails integration (fabrication check, sanitization)
"""

import json
import logging
import os

from google.adk.tools.tool_context import ToolContext
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from pydantic import ValidationError

from schemas import (
    FullResume,
    SectionPreferences,
    StaticProfile,
    TailoredSections,
)
from latex_bridge import render_resume, compile_pdf
from storage_client import storage
from guardrails import (
    detect_prompt_injection,
    sanitize_text_input,
    verify_no_fabrication,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context compression helpers (token optimization)
# ---------------------------------------------------------------------------

def _compact_experience(experiences: list[dict]) -> str:
    """
    Serialize experience list in compact format for LLM context.

    Uses single-line key-value format instead of json.dumps(indent=2)
    to reduce token count while preserving all information.
    """
    lines: list[str] = []
    for i, exp in enumerate(experiences, 1):
        lines.append(
            f"[Exp {i}] {exp.get('title', '')} @ {exp.get('company', '')} "
            f"({exp.get('start_date', '')}–{exp.get('end_date', '')})"
        )
        for bullet in exp.get("bullets", []):
            lines.append(f"  - {bullet}")
    return "\n".join(lines)


def _compact_projects(projects: list[dict]) -> str:
    """Serialize projects list in compact format for LLM context."""
    lines: list[str] = []
    for i, proj in enumerate(projects, 1):
        tech = proj.get("technologies", "")
        desc = proj.get("description", "")
        lines.append(
            f"[Proj {i}] {proj.get('name', '')} | Tech: {tech}"
        )
        if desc:
            lines.append(f"  Description: {desc}")
        for bullet in proj.get("bullets", []):
            lines.append(f"  - {bullet}")
    return "\n".join(lines)


def _compact_skills(skills: dict) -> str:
    """Serialize skills dict in compact single-line format."""
    return " | ".join(
        f"{cat}: {', '.join(items)}"
        for cat, items in skills.items()
        if items
    )


def _compact_achievements(achievements: list[str]) -> str:
    """Serialize achievements list in compact format."""
    return " | ".join(achievements) if achievements else ""


def _coerce_prefs(raw: dict) -> dict:
    """
    Coerce section preference values to their correct Python types.

    Handles cases where boolean values are stored as strings ('true'/'false')
    and count values come as strings ('2') instead of int 2.
    """
    coerced: dict = {}
    for key, value in raw.items():
        if key in ("include_summary", "include_skills"):
            if isinstance(value, str):
                coerced[key] = value.lower() in ("true", "1", "yes")
            elif isinstance(value, bool):
                coerced[key] = value
            else:
                coerced[key] = bool(value)
        elif key.endswith("_count") or key.startswith("bullets_"):
            if value is None or value in ("null", ""):
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
# Tool: read_state
# ---------------------------------------------------------------------------

def read_state(tool_context: ToolContext) -> str:
    """
    Read the current session state.

    Returns a compact summary of what is available in state:
    master profile, job description, preferences, and tailored sections.
    """
    try:
        scratchpad = tool_context.state.get("scratchpad") or {}
        tailored = (
            tool_context.state.get("tailored_sections")
            or tool_context.state.get("tailored_resume")
        )

        result: dict = {}

        if scratchpad.get("master_profile"):
            profile = scratchpad["master_profile"]
            result["profile_summary"] = {
                "experience_count": len(profile.get("experience", [])),
                "project_count": len(profile.get("projects", [])),
                "skills_categories": list(profile.get("skills", {}).keys()),
            }

        if scratchpad.get("job_description"):
            jd = scratchpad["job_description"]
            result["jd_preview"] = jd[:200] + ("..." if len(jd) > 200 else "")

        if scratchpad.get("section_prefs"):
            result["preferences"] = scratchpad["section_prefs"]

        if scratchpad.get("jd_analysis"):
            analysis = scratchpad["jd_analysis"]
            result["jd_analysis"] = {
                "role": analysis.get("role_title"),
                "seniority": analysis.get("seniority_level"),
                "skills_found": sum(
                    len(v) for v in analysis.get("required_skills", {}).values()
                ),
            }

        if tailored:
            data = tailored if isinstance(tailored, dict) else tailored.model_dump()
            result["tailored_sections"] = {
                "status": "present",
                "experience_count": len(data.get("experience", [])),
                "project_count": len(data.get("projects", [])),
                "has_summary": bool(data.get("summary")),
                "has_skills": bool(data.get("skills")),
                "achievement_count": len(data.get("achievements", [])),
            }
        else:
            result["tailored_sections"] = "NOT PRESENT — run tailoring first."

        if not result:
            return "Session state is empty. No data has been loaded yet."

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.error("[read_state] Error: %s", e, exc_info=True)
        return f"Error reading session state: {e}. Please retry."


# ---------------------------------------------------------------------------
# Tool: get_all_context (token-optimized)
# ---------------------------------------------------------------------------

def get_all_context(tool_context: ToolContext) -> str:
    """
    Fetch ALL context needed for resume tailoring in a single call.

    TOKEN OPTIMIZATION:
      1. Only sends TailoredSections-relevant data (experience, projects, skills,
         achievements) — NOT name, email, phone, education, or certifications.
         These static fields are preserved in session state for the renderer.
      2. Sections excluded by SectionPreferences are omitted entirely from the
         LLM context (e.g. if project_count=0, projects are not sent).
      3. Uses compact single-line format instead of indented JSON.
      4. JD analysis (if cached) replaces raw JD prose for further token savings.

    Call this ONCE to get everything the tailoring agent needs.
    """
    try:
        store = storage()

        # ── Load master profile ───────────────────────────────────────────
        profile_dict = store.load_profile() or {}
        if not profile_dict:
            return (
                "No Master Profile found in the database. "
                "Please add your profile using: python cli.py profile update --file path/to/profile.json"
            )

        # ── Load job description ──────────────────────────────────────────
        jd_raw = store.load_job_description() or ""
        if not jd_raw:
            return (
                "No Job Description found. "
                "Please set one using: python cli.py jd set --file path/to/jd.txt"
            )

        # Sanitize JD for injection patterns
        if detect_prompt_injection(jd_raw, "job_description"):
            logger.warning("[get_all_context] Potential prompt injection detected in JD — proceeding with caution.")

        try:
            jd = sanitize_text_input(jd_raw, max_length=15_000, field_name="job_description")
        except ValueError as e:
            return f"Job description rejected: {e}"

        # ── Load preferences ──────────────────────────────────────────────
        raw_prefs = store.load_preferences() or {}
        prefs_coerced = _coerce_prefs(raw_prefs)
        prefs = SectionPreferences(**prefs_coerced)

        # ── Separate static from dynamic profile sections ─────────────────
        # Static fields (NOT sent to LLM — stored in scratchpad for renderer)
        static_data = {
            "name": profile_dict.get("name", ""),
            "email": profile_dict.get("email", ""),
            "phone": profile_dict.get("phone", ""),
            "linkedin": profile_dict.get("linkedin", ""),
            "github": profile_dict.get("github", ""),
            "website": profile_dict.get("website", ""),
            "education": profile_dict.get("education", []),
            "certifications": profile_dict.get("certifications", []),
        }

        # Dynamic fields (sent to LLM — pruned by preferences)
        all_experience: list[dict] = profile_dict.get("experience", [])
        all_projects: list[dict] = profile_dict.get("projects", [])
        all_skills: dict = profile_dict.get("skills", {})
        all_achievements: list[str] = profile_dict.get("achievements", [])

        # ── Persist to scratchpad (for enforce_preferences + renderer) ────
        scratchpad = tool_context.state.get("scratchpad") or {}
        scratchpad["master_profile"] = profile_dict
        scratchpad["static_profile"] = static_data
        scratchpad["job_description"] = jd
        scratchpad["section_prefs"] = prefs.model_dump()
        tool_context.state["scratchpad"] = scratchpad

        # ── Build preference instructions ─────────────────────────────────
        pref_lines: list[str] = []
        if prefs.experience_count is not None:
            pref_lines.append(f"Experience: select exactly {prefs.experience_count} items.")
        if prefs.project_count is not None:
            pref_lines.append(f"Projects: select exactly {prefs.project_count} items.")
        if prefs.achievement_count is not None:
            pref_lines.append(f"Achievements: select exactly {prefs.achievement_count} items.")
        if prefs.bullets_per_experience is not None:
            pref_lines.append(f"Bullets per Experience: include exactly {prefs.bullets_per_experience} bullet points for each experience entry.")
        if prefs.bullets_per_project is not None:
            pref_lines.append(f"Bullets per Project: include exactly {prefs.bullets_per_project} bullet points for each project entry.")
        if not prefs.include_summary:
            pref_lines.append("Summary: Do NOT include a summary (set to empty string).")
        if not prefs.include_skills:
            pref_lines.append("Skills: Do NOT include skills (return empty dict).")
        if prefs.custom_instructions:
            pref_lines.append(f"Custom: {prefs.custom_instructions}")
        prefs_text = "\n".join(f"- {p}" for p in pref_lines) if pref_lines else "No strict limits — select the most relevant items."

        # ── Build compact profile context (token-optimized) ───────────────
        context_parts: list[str] = ["## CANDIDATE PROFILE (Dynamic Sections Only)\n"]

        # Experience — always included unless explicitly excluded (count=0)
        if prefs.experience_count != 0 and all_experience:
            context_parts.append("### Experience")
            context_parts.append(_compact_experience(all_experience))
            context_parts.append("")

        # Projects — omit entirely if project_count=0 (saves tokens)
        if prefs.project_count != 0 and all_projects:
            context_parts.append("### Projects")
            context_parts.append(_compact_projects(all_projects))
            context_parts.append("")

        # Skills — omit if include_skills=False
        if prefs.include_skills and all_skills:
            context_parts.append("### Skills")
            context_parts.append(_compact_skills(all_skills))
            context_parts.append("")

        # Achievements — omit if achievement_count=0
        if prefs.achievement_count != 0 and all_achievements:
            context_parts.append("### Achievements")
            context_parts.append(_compact_achievements(all_achievements))
            context_parts.append("")

        # ── JD context (prefer cached analysis for token efficiency) ──────
        jd_analysis = scratchpad.get("jd_analysis")
        if jd_analysis:
            context_parts.append("## JOB REQUIREMENTS (Pre-analyzed)")
            context_parts.append(f"Role: {jd_analysis.get('role_title', '')}")
            context_parts.append(f"Seniority: {jd_analysis.get('seniority_level', '')}")
            required_skills = jd_analysis.get("required_skills", {})
            if required_skills:
                context_parts.append("Required Skills:")
                for cat, skills in required_skills.items():
                    context_parts.append(f"  {cat}: {', '.join(skills)}")
            responsibilities = jd_analysis.get("key_responsibilities", [])
            if responsibilities:
                context_parts.append("Key Responsibilities:")
                for resp in responsibilities[:5]:
                    context_parts.append(f"  - {resp}")
        else:
            # Fall back to full JD text (truncated for token safety)
            context_parts.append("## JOB DESCRIPTION")
            context_parts.append(jd[:4000] + ("\n[...truncated...]" if len(jd) > 4000 else ""))

        context_parts.append("")
        context_parts.append("## USER PREFERENCES")
        context_parts.append(prefs_text)

        logger.info(
            "[get_all_context] Context built — %d experiences, %d projects, %d skill categories. "
            "JD analysis cached: %s.",
            len(all_experience), len(all_projects), len(all_skills),
            bool(jd_analysis),
        )

        return "\n".join(context_parts)

    except Exception as e:
        logger.error("[get_all_context] Error: %s", e, exc_info=True)
        return f"Error fetching context: {e}. Please retry."


# ---------------------------------------------------------------------------
# Tool: submit_tailored_sections (renamed from submit_tailored_resume)
# ---------------------------------------------------------------------------

def submit_tailored_sections(payload: dict, tool_context: ToolContext) -> str:
    """
    Submit the finalized tailored resume sections for validation and storage.

    Validates ONLY the TailoredSections schema (summary, experience, projects,
    skills, achievements) — NOT static identity fields. Also performs a fabrication
    check against the master profile to prevent hallucinated companies or projects.

    Call this ONLY AFTER get_all_context and after drafting all content.

    Args:
        payload: A dict matching the TailoredSections schema.
    """
    try:
        # 1. Schema validation
        TailoredSections(**payload)

        # 2. Fabrication check
        from guardrails import verify_no_fabrication
        scratchpad = tool_context.state.get("scratchpad") or {}
        master_profile = scratchpad.get("master_profile") or {}
        if not master_profile:
            from storage_client import storage
            master_profile = storage().load_profile() or {}

        warnings = verify_no_fabrication(payload, master_profile)
        if warnings:
            warning_str = "\n".join(warnings)
            logger.warning("[submit_tailored_sections] Rejecting payload due to fabrication:\n%s", warning_str)
            return (
                f"FAILURE: Fabrication check failed. You generated data not present in the master profile:\n{warning_str}\n\n"
                "Please rewrite the tailored sections to contain ONLY the companies and projects listed in the master profile, "
                "then call submit_tailored_sections again with the FULL corrected payload."
            )

        return "SUCCESS: Tailored sections validated and accepted."

    except ValidationError as e:
        errors = [
            f"- {' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        error_str = "\n".join(errors)
        logger.warning("[submit_tailored_sections] Validation failed:\n%s", error_str)
        return (
            f"FAILURE: Validation failed:\n{error_str}\n\n"
            "Fix these fields and call submit_tailored_sections again with the FULL corrected payload."
        )

    except Exception as e:
        logger.error("[submit_tailored_sections] Unexpected error: %s", e)
        return f"FAILURE: Unexpected error during validation: {e}"


# ---------------------------------------------------------------------------
# Tool: render_latex
# ---------------------------------------------------------------------------

def render_latex(tool_context: ToolContext) -> str:
    """
    Render the tailored resume data into a Jinja2 LaTeX template and compile to PDF.

    Merges StaticProfile (from scratchpad) + TailoredSections (from session state)
    into a FullResume compositor before rendering — the LLM never touches
    the static identity fields.
    """
    try:
        tailored_data = (
            tool_context.state.get("tailored_sections")
            or tool_context.state.get("tailored_resume")
        )
        if not tailored_data:
            return (
                "No tailored sections in state. "
                "Please run the tailoring agent first."
            )

        # ── Validate tailored sections ────────────────────────────────────
        try:
            tailored = TailoredSections(**tailored_data) if isinstance(tailored_data, dict) else tailored_data
        except ValidationError as e:
            return f"Tailored sections validation failed: {e}"

        # ── Load static profile from scratchpad ───────────────────────────
        scratchpad = tool_context.state.get("scratchpad") or {}
        static_data = scratchpad.get("static_profile") or {}

        if not static_data:
            # Fallback: load from storage
            profile_dict = storage().load_profile() or {}
            static_data = {
                "name": profile_dict.get("name", ""),
                "email": profile_dict.get("email", ""),
                "phone": profile_dict.get("phone", ""),
                "linkedin": profile_dict.get("linkedin", ""),
                "github": profile_dict.get("github", ""),
                "website": profile_dict.get("website", ""),
                "education": profile_dict.get("education", []),
                "certifications": profile_dict.get("certifications", []),
            }

        try:
            static = StaticProfile(**static_data)
        except ValidationError as e:
            return f"Static profile validation failed: {e}"

        # ── Fabrication check ─────────────────────────────────────────────
        master_profile = scratchpad.get("master_profile") or {}
        warnings = verify_no_fabrication(tailored_data if isinstance(tailored_data, dict) else tailored.model_dump(), master_profile)
        if warnings:
            logger.warning("[render_latex] Fabrication warnings (rendering anyway): %s", warnings)

        # ── Compose FullResume ────────────────────────────────────────────
        full_resume = FullResume(static=static, tailored=tailored)

        # ── Load template from storage ────────────────────────────────────
        template_content = storage().load_template()
        if not template_content:
            # Fallback to file-based template
            for path in ["data/resume_template.tex", "resume_template.tex"]:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        template_content = f.read()
                    break

        if not template_content:
            return (
                "No LaTeX template found. Add one using: "
                "python cli.py template add --file path/to/template.tex"
            )

        # ── Render ────────────────────────────────────────────────────────
        try:
            rendered_tex = render_resume(template_content, full_resume)
        except Exception as e:
            return f"LaTeX render failed: {e}"

        # ── Compile PDF ───────────────────────────────────────────────────
        output_dir = os.getenv("OUTPUT_DIR", "output")
        os.makedirs(output_dir, exist_ok=True)

        raw_prefs = scratchpad.get("section_prefs") or {}
        output_filename = raw_prefs.get("output_file_name") or "resume"
        import re as _re
        output_filename = _re.sub(r"[^a-zA-Z0-9_\-]", "", output_filename)
        if not output_filename:
            output_filename = "resume"

        tex_path = os.path.join(output_dir, f"{output_filename}.tex")
        with open(tex_path, "w", encoding="utf-8", newline="") as f:
            f.write(rendered_tex)

        pdf_path: str | None = None
        try:
            pdf_path = compile_pdf(rendered_tex, output_dir, filename=output_filename)
        except Exception as e:
            logger.warning("[render_latex] PDF compilation failed (pdflatex installed?): %s", e)

        # ── Save tailored output to history ───────────────────────────────
        try:
            storage().save_tailored_output(
                tailored.model_dump() if hasattr(tailored, "model_dump") else tailored_data
            )
        except Exception as e:
            logger.warning("[render_latex] Failed to save tailored output to history: %s", e)

        if pdf_path:
            msg = f"PDF compiled successfully → {pdf_path}"
        else:
            msg = "LaTeX rendered successfully. The document is ready."

        return msg

    except Exception as e:
        logger.error("[render_latex] Unhandled error: %s", e, exc_info=True)
        return f"Error in render_latex: {e}. Please retry."


# ---------------------------------------------------------------------------
# Agent Callbacks
# ---------------------------------------------------------------------------

def capture_tailored_output(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> None:
    """
    after_model_callback: Inspect tool calls for submit_tailored_sections
    and persist the validated payload to session state.
    """
    if not llm_response or not llm_response.content:
        return None

    parts = llm_response.content.parts or []
    for part in parts:
        call = getattr(part, "function_call", None)
        if call and call.name == "submit_tailored_sections":
            payload = call.args.get("payload") if call.args else None
            if not payload:
                continue
            try:
                sections = TailoredSections(**payload)
                callback_context.state["tailored_sections"] = sections.model_dump(exclude_none=True)
                logger.info(
                    "[capture_tailored_output] Tailored sections saved to state: "
                    "%d exp, %d proj, has_summary=%s.",
                    len(sections.experience),
                    len(sections.projects),
                    bool(sections.summary),
                )
            except Exception as e:
                logger.debug(
                    "[capture_tailored_output] Validation failed (LLM will retry): %s", e
                )
    return None


def enforce_preferences(callback_context: CallbackContext) -> None:
    """
    after_agent_callback: Enforce section preferences by truncating any extra
    items the LLM produced beyond the user's limits.
    """
    tailored = callback_context.state.get("tailored_sections") or callback_context.state.get("tailored_resume")
    if not tailored:
        return

    raw_prefs = callback_context.state.get("scratchpad", {}).get("section_prefs", {})
    prefs = SectionPreferences(**raw_prefs) if raw_prefs else SectionPreferences()

    data = tailored if isinstance(tailored, dict) else tailored.model_dump()
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
        data["summary"] = ""
        changed = True
    if not prefs.include_skills and data.get("skills"):
        data["skills"] = {}
        changed = True

    # Truncate bullets per experience entry
    if prefs.bullets_per_experience is not None:
        for exp in data.get("experience", []):
            if len(exp.get("bullets", [])) > prefs.bullets_per_experience:
                exp["bullets"] = exp["bullets"][: prefs.bullets_per_experience]
                changed = True

    # Truncate bullets per project entry
    if prefs.bullets_per_project is not None:
        for proj in data.get("projects", []):
            if len(proj.get("bullets", [])) > prefs.bullets_per_project:
                proj["bullets"] = proj["bullets"][: prefs.bullets_per_project]
                changed = True

    if changed:
        try:
            validated = TailoredSections(**data)
            callback_context.state["tailored_sections"] = validated.model_dump(exclude_none=True)
            logger.info("[enforce_preferences] Preferences enforced and state updated.")
        except Exception as e:
            logger.error("[enforce_preferences] Validation failed after enforcement: %s", e)
