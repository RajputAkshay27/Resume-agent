"""
Structured generation engine for the Resume Agent.

Uses NVIDIA NIM's Nemotron-3-Super-120B-A12B with guided_json for
token-level JSON schema enforcement via xgrammar.
"""

import json
import logging
import os
from typing import Optional

from openai import OpenAI

from schemas import (
    SectionPreferences,
    TailoredResume,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NVIDIA NIM client (OpenAI-compatible)
# ---------------------------------------------------------------------------

_client: Optional[OpenAI] = None

MODEL = "nvidia/nemotron-3-super-120b-a12b"
BASE_URL = "https://integrate.api.nvidia.com/v1"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("NVIDIA_API_KEY", "")
        _client = OpenAI(base_url=BASE_URL, api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_preference_instructions(prefs: SectionPreferences) -> str:
    """Convert SectionPreferences into clear natural-language constraints."""
    lines: list[str] = []

    def _count_instruction(name: str, count: Optional[int]) -> None:
        if count is None:
            lines.append(f"- {name}: Select the most relevant items (you decide how many).")
        elif count == 0:
            lines.append(f"- {name}: Do NOT include any. Return an empty list.")
        else:
            lines.append(f"- {name}: Select exactly the {count} most relevant items.")

    _count_instruction("Experience", prefs.experience_count)
    _count_instruction("Projects", prefs.project_count)
    _count_instruction("Achievements", prefs.achievement_count)
    _count_instruction("Certifications", prefs.certification_count)

    if not prefs.include_summary:
        lines.append("- Summary: Do NOT include a summary. Set tailored_summary to null.")
    else:
        lines.append(
            "- Summary: You MUST write a compelling 2-4 sentence professional summary "
            "TAILORED to the JD. Do NOT leave tailored_summary as null or empty. "
            "This field is REQUIRED."
        )

    if not prefs.include_skills:
        lines.append("- Skills: Do NOT include skills. Return an empty dict.")
    else:
        lines.append("- Skills: Select and group the most relevant skills for this JD.")

    if prefs.custom_instructions:
        lines.append(f"\nAdditional instructions from the user:\n{prefs.custom_instructions}")

    return "\n".join(lines)


SYSTEM_PROMPT = """\
You are an expert resume tailoring engine. You receive:
1. A comprehensive "Master Profile" containing ALL of a candidate's experience, projects, skills, etc.
2. A target "Job Description" (JD).
3. Section-level constraints on how many items to include.

Your task:
- Select the most relevant items from the Master Profile for this specific JD.
- Rewrite bullet points to highlight skills/technologies mentioned in the JD.
- Use strong action verbs and quantify impact where possible.
- If a bullet point should be highlighted, wrap the key phrase in **double asterisks** like **this**.
- Do NOT fabricate any experience or skills not present in the Master Profile.
- Output MUST strictly follow the JSON schema provided.

## CRITICAL: Required Sections
- **tailored_summary**: You MUST provide a 2-4 sentence professional summary unless
  explicitly told to skip it. This is one of the most important parts of the resume.
  Never leave it as null or empty.
- **skills**: You MUST provide a categorized skills section (e.g. "Languages": ["Python", ...],
  "Cloud & DevOps": ["Kubernetes", ...]) unless explicitly told to skip it.
  Select and group skills from the Master Profile that are most relevant to the JD.
  Never leave it as an empty dict unless instructed.

## Section Constraints
{preference_instructions}
"""


def _build_user_message(master_profile: dict, job_description: str) -> str:
    return (
        "## Master Profile\n"
        f"```json\n{json.dumps(master_profile, indent=2)}\n```\n\n"
        "## Target Job Description\n"
        f"{job_description}\n\n"
        "Now produce the tailored resume JSON."
    )


# ---------------------------------------------------------------------------
# Post-validation: enforce count constraints
# ---------------------------------------------------------------------------

def _enforce_counts(resume: TailoredResume, prefs: SectionPreferences) -> TailoredResume:
    """Truncate lists if the model returned more items than the user requested."""
    data = resume.model_dump()

    if prefs.experience_count is not None:
        data["experience"] = data["experience"][: prefs.experience_count]
    if prefs.project_count is not None:
        data["projects"] = data["projects"][: prefs.project_count]
    if prefs.achievement_count is not None:
        data["achievements"] = data["achievements"][: prefs.achievement_count]
    if prefs.certification_count is not None:
        data["certifications"] = data["certifications"][: prefs.certification_count]
    if not prefs.include_summary:
        data["tailored_summary"] = None
    if not prefs.include_skills:
        data["skills"] = {}

    return TailoredResume(**data)


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------

def generate_tailored_resume(
    master_profile: dict,
    job_description: str,
    preferences: Optional[SectionPreferences] = None,
) -> TailoredResume:
    """
    Generate a tailored resume using Nemotron-3-Super with guided_json.

    Args:
        master_profile: The user's comprehensive profile as a dict.
        job_description: The target job description text.
        preferences: Optional section preferences from the frontend UI.

    Returns:
        A validated TailoredResume Pydantic object.
    """
    if preferences is None:
        preferences = SectionPreferences()

    client = _get_client()

    pref_instructions = _build_preference_instructions(preferences)
    system_message = SYSTEM_PROMPT.format(preference_instructions=pref_instructions)
    user_message = _build_user_message(master_profile, job_description)

    # Get the JSON schema from the Pydantic model for guided_json
    json_schema = TailoredResume.model_json_schema()

    logger.info("Calling Nemotron-3-Super for structured resume generation...")

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
        max_tokens=4096,
        extra_body={
            "guided_json": json_schema,
        },
    )

    raw_content = response.choices[0].message.content
    logger.info("Nemotron-3-Super response received. Parsing...")

    # Parse the JSON response into the Pydantic model
    parsed_data = json.loads(raw_content)
    resume = TailoredResume(**parsed_data)

    # Enforce user's count constraints (post-validation safety net)
    resume = _enforce_counts(resume, preferences)

    # Fallback: if include_summary is true but model returned null, build one
    if preferences.include_summary and not resume.tailored_summary:
        logger.warning("Model returned null tailored_summary despite include_summary=True. Building fallback.")
        name = master_profile.get("name", "Experienced professional")
        title = ""
        if master_profile.get("experience"):
            title = master_profile["experience"][0].get("title", "")
        summary_parts = []
        if title:
            summary_parts.append(f"{title}")
        summary_text = master_profile.get("summary", "")
        if summary_text:
            # Use the original summary, trimmed to 3 sentences max
            sentences = [s.strip() for s in summary_text.replace("\n", " ").split(".") if s.strip()]
            summary_parts.append(". ".join(sentences[:3]) + ".")
        resume.tailored_summary = " ".join(summary_parts) if summary_parts else f"{name} with extensive professional experience."

    logger.info(
        "Tailored resume generated: %d exp, %d projects, %d achievements, %d skill categories",
        len(resume.experience),
        len(resume.projects),
        len(resume.achievements),
        len(resume.skills),
    )

    return resume
