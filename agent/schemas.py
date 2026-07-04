"""
schemas.py — Pydantic models for the Resume Agent v2 pipeline.

Architecture (token-optimized):
  - StaticProfile:    Immutable identity data. Loaded from SQLite, NEVER sent to the LLM.
  - TailoredSections: LLM-generated resume content ONLY (summary, experience, projects, skills).
  - FullResume:       Compositor that merges StaticProfile + TailoredSections at render time.
  - SectionPreferences: User-controlled constraints on what to include / how many items.
  - JDAnalysis:       Structured output from the JD analysis tool.

Token savings vs v1:
  - ~800 tokens/run saved by not passing name, email, education, certifications to the LLM.
  - Context pruning in get_all_context further reduces based on active preferences.
"""

from __future__ import annotations

import re
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bullet quality pattern
# ---------------------------------------------------------------------------

# A bullet MUST start with **Bold Phrase:** or **Bold Phrase** followed by content.
# This enforces the pattern: "**Action Phrase:** Describe what was done, achieving [impact]."
# Supports colons inside or outside the bold brackets, and en/em dashes.
_BULLET_BOLD_RE = re.compile(r"^\*\*[A-Za-z\s\-&:\u2013\u2014]{2,50}\*\*[:\s]?")


def _validate_bullet(bullet: str, field_name: str = "bullet") -> str:
    """
    Validate that a bullet point follows the required quality pattern.

    Rules:
      1. Must start with a **Bold Action Phrase** (2-40 chars, letters/spaces/hyphens/&).
      2. Must contain at least one digit (quantified impact) OR the word 'improving',
         'reducing', 'increasing', 'enabling', 'achieving', 'resulting' (qualitative impact).

    Does NOT raise — logs a warning so the LLM output is never silently rejected.
    Returns the bullet unchanged.
    """
    stripped = bullet.strip()

    if not _BULLET_BOLD_RE.match(stripped):
        logger.warning(
            "[bullet_validator] %s does not start with a **Bold Action Phrase**: %.80s",
            field_name, stripped,
        )

    _IMPACT_KEYWORDS = {
        "improving", "reduced", "reducing", "increased", "increasing",
        "enabling", "achieved", "achieving", "resulting", "saved",
        "accelerated", "eliminated", "automated", "scaled",
    }
    has_digit = bool(re.search(r"\d", stripped))
    has_impact_word = any(kw in stripped.lower() for kw in _IMPACT_KEYWORDS)

    if not (has_digit or has_impact_word):
        logger.warning(
            "[bullet_validator] %s lacks quantified or qualitative impact: %.80s",
            field_name, stripped,
        )

    return stripped


# ---------------------------------------------------------------------------
# Section Preferences — user-controlled constraints from CLI / GUI / API
# ---------------------------------------------------------------------------

class SectionPreferences(BaseModel):
    """
    User-specified constraints on what to include in the tailored resume.

    None  = let the AI decide.
    0     = exclude this section entirely (section is omitted from context payload,
            saving tokens).
    N > 0 = include exactly N items.
    """

    experience_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of experiences to include. None=AI decides, 0=skip, N=pick best N.",
    )
    project_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of projects to include. None=AI decides, 0=skip, N=pick best N.",
    )
    achievement_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of achievements to include. None=AI decides, 0=skip.",
    )
    certification_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of certifications to include. None=AI decides, 0=skip.",
    )
    include_summary: bool = Field(
        default=True,
        description="Whether to include a tailored summary.",
    )
    include_skills: bool = Field(
        default=True,
        description="Whether to include the skills section.",
    )
    bullets_per_experience: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of bullets to include per experience entry. None=AI decides.",
    )
    bullets_per_project: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of bullets to include per project entry. None=AI decides.",
    )
    output_file_name: str = Field(
        default="resume",
        description="Name of the output LaTeX/PDF file (without extension).",
    )
    custom_instructions: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Free-text tailoring instructions, e.g. 'emphasize backend work'.",
    )


# ---------------------------------------------------------------------------
# Sub-models shared between StaticProfile and TailoredSections
# ---------------------------------------------------------------------------

class Education(BaseModel):
    """Education entry — part of StaticProfile, never sent to LLM."""

    institution: str
    degree: str
    field: Optional[str] = None
    start_date: str
    end_date: str
    gpa: str = ""
    highlights: list[str] = Field(default_factory=list)


class TailoredExperience(BaseModel):
    """A single work experience entry produced by the tailoring agent."""

    company: str
    title: str
    location: str = ""
    start_date: str
    end_date: str
    bullets: list[str] = Field(
        min_length=1,
        description=(
            "Rewritten bullet points tailored to the JD. Each bullet MUST start with "
            "**Bold Action Phrase:** and contain a quantified or qualitative impact."
        ),
    )

    @field_validator("bullets", mode="after")
    @classmethod
    def validate_bullets(cls, bullets: list[str]) -> list[str]:
        return [_validate_bullet(b, "experience.bullet") for b in bullets]


class TailoredProject(BaseModel):
    """A single project entry produced by the tailoring agent."""

    name: str
    technologies: Optional[str] = None
    description: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)

    @field_validator("bullets", mode="after")
    @classmethod
    def validate_bullets(cls, bullets: list[str]) -> list[str]:
        return [_validate_bullet(b, "project.bullet") for b in bullets]


# ---------------------------------------------------------------------------
# StaticProfile — NEVER sent to the LLM
# ---------------------------------------------------------------------------

class StaticProfile(BaseModel):
    """
    Immutable identity and credential data.

    Loaded once from SQLite and passed DIRECTLY to the LaTeX renderer.
    The LLM NEVER sees or modifies these fields — this prevents hallucinations
    on contact information, education, and certifications, and saves ~800 tokens/run.
    """

    name: str
    email: str
    phone: str
    linkedin: str = ""
    github: str = ""
    website: str = ""
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def unwrap_list(cls, data: Any) -> Any:
        """Resilience: unwrap single-element list if the LLM accidentally returns one."""
        if isinstance(data, list):
            if data:
                return data[0]
            raise ValueError("Empty list provided for StaticProfile.")
        return data


# ---------------------------------------------------------------------------
# TailoredSections — the ONLY output the LLM produces
# ---------------------------------------------------------------------------

class TailoredSections(BaseModel):
    """
    LLM-generated resume content.

    This is the ONLY schema the tailoring agent produces. It does not contain
    name, email, phone, education, or certifications — those live in StaticProfile.

    Token optimization: context sent to the LLM is pruned based on SectionPreferences
    (e.g. if project_count=0, no project data is sent to the LLM either).
    """

    @model_validator(mode="before")
    @classmethod
    def unwrap_list(cls, data: Any) -> Any:
        """Resilience: unwrap single-element list if the LLM wraps JSON in a list."""
        if isinstance(data, list):
            if len(data) > 0:
                if len(data) > 1:
                    logger.warning("LLM returned multiple objects; using only the first.")
                return data[0]
            raise ValueError("LLM returned an empty list.")
        return data

    summary: str = Field(
        default="",
        description=(
            "3-5 sentence professional summary tailored to the JD. "
            "Highlight the most relevant skills, impact, and domain expertise."
        ),
    )
    experience: list[TailoredExperience] = Field(
        default_factory=list,
        description="Professional experiences tailored to the job description.",
    )
    projects: list[TailoredProject] = Field(
        default_factory=list,
        description="Projects tailored to the job description.",
    )
    skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Skills grouped by category, e.g. {'Languages': ['Python', 'Go']}.",
    )
    achievements: list[str] = Field(
        default_factory=list,
        description="Notable achievements relevant to the JD.",
    )


# ---------------------------------------------------------------------------
# FullResume — compositor model (never sent to LLM, used only at render time)
# ---------------------------------------------------------------------------

class FullResume(BaseModel):
    """
    Merges StaticProfile + TailoredSections for LaTeX rendering.

    This model is constructed by the render_latex tool AFTER the tailoring agent
    has produced TailoredSections. It is never sent to any LLM.

    The LaTeX bridge flattens this into a single dict for Jinja2 rendering.
    """

    static: StaticProfile
    tailored: TailoredSections

    def flatten(self) -> dict:
        """
        Flatten static + tailored data into a single dict for Jinja2 rendering.

        Static fields are top-level. Tailored sections are merged in.
        If a key exists in both (shouldn't happen given schema separation), tailored wins.
        """
        flat: dict = {}

        # Static fields
        flat.update(self.static.model_dump(exclude_none=True))

        # Tailored sections — these overwrite if there's any key collision
        flat.update(self.tailored.model_dump(exclude_none=True))

        return flat


# ---------------------------------------------------------------------------
# JDAnalysis — structured output from the analyze_jd tool (Phase 7)
# ---------------------------------------------------------------------------

class JDAnalysis(BaseModel):
    """
    Structured analysis of a job description.

    Used by the analyze_jd tool to produce a compressed, structured summary
    of a JD for more token-efficient tailoring (vs. passing raw JD prose).

    Token savings: a JD analysis object is typically ~300 tokens vs ~700 tokens
    for the raw JD text.
    """

    role_title: str = Field(description="Inferred job title from the JD.")
    company_name: str = Field(default="", description="Company name if mentioned.")
    seniority_level: str = Field(
        default="",
        description="e.g. 'Senior', 'Lead', 'Junior', 'Mid-level'.",
    )
    required_skills: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Skills categorized by type: "
            "{'Languages': [...], 'Frameworks': [...], 'Tools': [...], 'Soft Skills': [...]}."
        ),
    )
    key_responsibilities: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Top 3-8 key responsibilities extracted from the JD.",
    )
    experience_years: Optional[int] = Field(
        default=None,
        description="Minimum years of experience requested.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="All significant keywords/technologies mentioned in the JD.",
    )


# ---------------------------------------------------------------------------
# ATSScore — output from the score_ats_match tool (Phase 7)
# ---------------------------------------------------------------------------

class SectionScore(BaseModel):
    """ATS keyword coverage score for a single resume section."""

    section_name: str
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    coverage_pct: float = Field(ge=0.0, le=100.0)


class ATSScore(BaseModel):
    """
    ATS keyword match score for a tailored resume vs a job description.

    Used in the feedback loop: if overall_score < threshold, the orchestrator
    re-tailors specific low-coverage sections instead of regenerating the full resume.
    """

    overall_score: float = Field(
        ge=0.0, le=100.0,
        description="Overall keyword match percentage.",
    )
    section_scores: list[SectionScore] = Field(default_factory=list)
    missing_keywords: list[str] = Field(
        default_factory=list,
        description="Top keywords from the JD not found anywhere in the tailored resume.",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Prioritized suggestions to improve ATS coverage.",
    )
    passed_threshold: bool = Field(
        default=False,
        description="True if overall_score >= the configured passing threshold.",
    )


# ---------------------------------------------------------------------------
# Backward-compat alias — allows existing code that imports TailoredResume to
# continue working during the migration. Will be removed in a future version.
# ---------------------------------------------------------------------------

class TailoredResume(TailoredSections):
    """
    Deprecated alias for TailoredSections.

    Kept for backward compatibility only. New code should use TailoredSections.
    This class adds the static identity fields that v1 used to include in the
    LLM output — maintained only so existing session state can be deserialized.

    DO NOT use this class in new code paths.
    """

    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
