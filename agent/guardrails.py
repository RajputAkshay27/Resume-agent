"""
guardrails.py — Input/output security guardrails for the Resume Agent v2.

Provides:
  - sanitize_text_input():        Strip control characters, enforce length limits.
  - validate_json_depth():        Prevent deeply nested JSON payloads (DoS protection).
  - detect_prompt_injection():    Heuristic detection of injection patterns in user text.
  - verify_no_fabrication():      Check tailored output against master profile for invented data.
  - sanitize_latex():             Strip dangerous LaTeX commands before pdflatex compilation.

All functions are stateless, synchronous, and raise ValueError or return bool.
They do NOT make network calls or touch the filesystem.
"""

from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Dangerous LaTeX commands that allow arbitrary code execution or file access
_DANGEROUS_LATEX_COMMANDS = [
    r"\\write18",
    r"\\immediate\\write18",
    r"\\input\b",
    r"\\include\b",
    r"\\openout\b",
    r"\\openin\b",
    r"\\catcode",
    r"\\directlua",
    r"\\executeiffilenewer",
    r"\\ShellEscape",
    r"\\typeout",
    r"\\IfFileExists",
]
_LATEX_INJECTION_RE = re.compile(
    "|".join(_DANGEROUS_LATEX_COMMANDS),
    re.IGNORECASE,
)

# Prompt injection patterns — phrases that attempt to override system behavior
_INJECTION_PATTERNS = [
    r"ignore (all\s+|previous\s+|above\s+|prior\s+)*instructions?",
    r"disregard (all\s+|previous\s+|above\s+|prior\s+)*instructions?",
    r"forget (all\s+|previous\s+|above\s+|what\s+)*you('ve| have)? (been\s+|)told",
    r"you are now (a |an )?",
    r"act as (a |an )?(?!candidate|applicant)",  # allow "act as a candidate"
    r"new system prompt",
    r"override (your |the )?(instructions?|system|prompt|rules?)",
    r"do not follow",
    r"jailbreak",
    r"DAN mode",
    r"developer mode",
    r"pretend (you are|to be)",
]
_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS),
    re.IGNORECASE,
)

# Control characters (except common whitespace: tab, newline, CR)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


# ---------------------------------------------------------------------------
# 1. Text input sanitization
# ---------------------------------------------------------------------------

def sanitize_text_input(
    text: str,
    max_length: int = 50_000,
    field_name: str = "input",
) -> str:
    """
    Sanitize a text string for safe use as agent context.

    - Strips leading/trailing whitespace.
    - Removes ASCII control characters (except \\t, \\n, \\r).
    - Enforces a maximum character length.

    Args:
        text:       The raw input string.
        max_length: Maximum allowed character count (default 50,000).
        field_name: Name used in log/error messages.

    Returns:
        The sanitized string.

    Raises:
        TypeError:  If `text` is not a string.
        ValueError: If the string exceeds max_length after sanitization.
    """
    if not isinstance(text, str):
        raise TypeError(f"{field_name} must be a string, got {type(text).__name__}.")

    cleaned = _CONTROL_CHAR_RE.sub("", text).strip()

    if len(cleaned) > max_length:
        raise ValueError(
            f"{field_name} exceeds maximum length of {max_length:,} characters "
            f"(got {len(cleaned):,})."
        )

    return cleaned


# ---------------------------------------------------------------------------
# 2. JSON depth validation
# ---------------------------------------------------------------------------

def validate_json_depth(data: Any, max_depth: int = 10, _current: int = 0) -> None:
    """
    Validate that a JSON-like data structure does not exceed max_depth levels.

    Prevents deeply nested payloads that could cause stack overflows or
    excessive memory usage during parsing.

    Args:
        data:      The data structure to validate.
        max_depth: Maximum allowed nesting depth (default 10).

    Raises:
        ValueError: If the nesting depth exceeds max_depth.
    """
    if _current > max_depth:
        raise ValueError(
            f"Payload nesting depth exceeds the allowed maximum of {max_depth}."
        )
    if isinstance(data, dict):
        for v in data.values():
            validate_json_depth(v, max_depth, _current + 1)
    elif isinstance(data, list):
        for item in data:
            validate_json_depth(item, max_depth, _current + 1)


# ---------------------------------------------------------------------------
# 3. Prompt injection detection
# ---------------------------------------------------------------------------

def detect_prompt_injection(text: str, field_name: str = "text") -> bool:
    """
    Heuristic detection of prompt injection patterns.

    Checks for common phrases used to override LLM system instructions.

    Args:
        text:       The text to inspect (e.g. job description, custom instructions).
        field_name: Name used in log messages.

    Returns:
        True  if a potential injection pattern is detected (caller should warn/reject).
        False if the text appears safe.
    """
    if not text:
        return False

    match = _INJECTION_RE.search(text)
    if match:
        logger.warning(
            "[guardrails] Potential prompt injection detected in %s at position %d: %r",
            field_name, match.start(), match.group(),
        )
        return True
    return False


# ---------------------------------------------------------------------------
# 4. Fabrication verification
# ---------------------------------------------------------------------------

def verify_no_fabrication(tailored: dict, master_profile: dict) -> list[str]:
    """
    Verify that the tailored output does not contain companies or technologies
    not present in the master profile.

    This is a best-effort check — it cannot detect all forms of fabrication,
    but catches the most common case of invented company names.

    Args:
        tailored:       The TailoredSections data dict.
        master_profile: The original master profile dict.

    Returns:
        A list of warning strings. Empty list means no fabrications detected.
    """
    warnings: list[str] = []

    # Collect all known company names from the master profile
    known_companies: set[str] = set()
    for exp in master_profile.get("experience", []):
        company = exp.get("company", "").strip().lower()
        if company:
            known_companies.add(company)

    # Collect all known project names from the master profile
    known_projects: set[str] = set()
    for proj in master_profile.get("projects", []):
        name = proj.get("name", "").strip().lower()
        if name:
            known_projects.add(name)

    # Check tailored experience companies
    for exp in tailored.get("experience", []):
        company = exp.get("company", "").strip().lower()
        if company and known_companies and company not in known_companies:
            warnings.append(
                f"Fabricated company detected in tailored experience: {exp.get('company')!r}. "
                f"Known companies: {[c.title() for c in sorted(known_companies)]}."
            )

    # Check tailored project names
    for proj in tailored.get("projects", []):
        name = proj.get("name", "").strip().lower()
        if name and known_projects and name not in known_projects:
            warnings.append(
                f"Fabricated project detected in tailored output: {proj.get('name')!r}. "
                f"Known projects: {[p.title() for p in sorted(known_projects)]}."
            )

    if warnings:
        for w in warnings:
            logger.error("[guardrails] %s", w)
    else:
        logger.debug("[guardrails] No fabrication detected in tailored output.")

    return warnings


# ---------------------------------------------------------------------------
# 5. LaTeX sanitization
# ---------------------------------------------------------------------------

def sanitize_latex(tex_content: str) -> str:
    """
    Remove dangerous LaTeX commands from a .tex string before pdflatex compilation.

    Strips commands that allow arbitrary code execution or filesystem access,
    such as \\write18, \\input, \\openout, \\directlua.

    Args:
        tex_content: The raw LaTeX source string.

    Returns:
        The sanitized LaTeX string with dangerous commands commented out.

    Note:
        Legitimate \\input usage in the resume template is handled by the Jinja2
        renderer BEFORE this function is called — this sanitizes user-provided
        data that has been interpolated into the template.
    """
    def _comment_out(match: re.Match) -> str:
        logger.warning(
            "[guardrails] Dangerous LaTeX command removed from .tex output: %r",
            match.group(),
        )
        return f"% [REMOVED: {match.group()}]"

    return _LATEX_INJECTION_RE.sub(_comment_out, tex_content)
