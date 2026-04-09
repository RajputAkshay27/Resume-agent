"""
Pydantic models for the Resume Agent structured generation pipeline.

These schemas define:
1. The Master Profile — a user's comprehensive career data
2. The Tailored Resume — the output schema for a job-specific resume
3. Section Preferences — user controls for what to include/exclude
"""

from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section Preferences — user-controlled constraints from the Frontend UI
# ---------------------------------------------------------------------------

class SectionPreferences(BaseModel):
    """User-specified constraints on what to include in the tailored resume.
    None = let the AI decide.  0 = exclude entirely.  N = include exactly N items."""

    experience_count: Optional[int] = Field(
        default=None,
        description="Number of experiences to include. None=AI decides, 0=skip, N=pick best N.",
    )
    project_count: Optional[int] = Field(
        default=None,
        description="Number of projects to include. None=AI decides, 0=skip, N=pick best N.",
    )
    achievement_count: Optional[int] = Field(
        default=None,
        description="Number of achievements to include. None=AI decides, 0=skip.",
    )
    certification_count: Optional[int] = Field(
        default=None,
        description="Number of certifications to include. None=AI decides, 0=skip.",
    )
    include_summary: bool = Field(default=True, description="Whether to include a tailored summary.")
    include_skills: bool = Field(default=True, description="Whether to include the skills section.")
    custom_instructions: Optional[str] = Field(
        default=None,
        description="Free-text instructions for the tailoring, e.g. 'emphasize backend work'.",
    )


# ---------------------------------------------------------------------------
# Tailored Resume — the Pydantic output schema forced by guided_json
# ---------------------------------------------------------------------------

class TailoredExperience(BaseModel):
    company: str
    title: str
    location: str
    start_date: str
    end_date: str
    bullets: list[str] = Field(
        description="Rewritten bullet points tailored to the JD that highlight how and what was done.",
    )


class TailoredProject(BaseModel):
    name: str
    technologies: Optional[str] = None
    description: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    field: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    gpa: Optional[str] = None
    highlights: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    """The complete output schema for a job-tailored resume."""

    @model_validator(mode='before')
    @classmethod
    def unwrap_list(cls, data: Any) -> Any:
        """Resilience: If the LLM wraps the JSON in a list (e.g. [{...}]), unwrap it."""
        if isinstance(data, list):
            if len(data) > 0:
                if len(data) > 1:
                    logger.warning("LLM returned multiple objects in a list. Using only the first one.")
                return data[0]
            else:
                raise ValueError("LLM returned an empty list.")
        return data

    name: str
    email: str
    phone: str
    linkedin: str
    github: str
    website: str

    summary: str = Field(
        description="A 3-5 sentence professional summary tailored to the JD. Highlighting important workdone",
    )

    experience: list[TailoredExperience] = Field(
        description="Sequence of professional experiences tailored to the job description."
    )
    education: list[Education] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(
        description="Skills grouped by category, e.g. {'Languages': ['Python', 'Go']}.",
    )
    projects: list[TailoredProject] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
