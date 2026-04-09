"""
Jinja2 LaTeX Template Bridge for the Resume Agent.

Handles:
- LaTeX special character escaping
- Bold marker conversion (**text** → \\textbf{text})
- Dual delimiter support (standard {{ }} and LaTeX-safe \\VAR{})
- Template validation
- Template rendering with Pydantic data
- PDF compilation via pdflatex
"""

import os
import re
import subprocess
import tempfile
import logging
from typing import Any

import jinja2

from schemas import TailoredResume

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LaTeX escaping
# ---------------------------------------------------------------------------

_LATEX_SPECIAL_CHARS = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}

# Regex: match any single LaTeX special char
_LATEX_ESCAPE_RE = re.compile(
    "|".join(re.escape(c) for c in _LATEX_SPECIAL_CHARS)
)


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in a string.

    Handles: & % $ # _ { } ~ ^
    Backslash is NOT escaped here because the Jinja2 template itself
    may contain intentional LaTeX commands.
    """
    if not text:
        return text
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_SPECIAL_CHARS[m.group()], text)


# ---------------------------------------------------------------------------
# Bold marker conversion
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def process_bold_markers(text: str) -> str:
    """Convert **TEXT** markers to \\textbf{TEXT}."""
    if not text:
        return text
    return _BOLD_RE.sub(r"\\textbf{\1}", text)


# ---------------------------------------------------------------------------
# Delimiter detection & Jinja2 environment
# ---------------------------------------------------------------------------

_LATEX_SAFE_VAR_RE = re.compile(r"\\VAR\{")
_LATEX_SAFE_BLOCK_RE = re.compile(r"\\BLOCK\{")
_STANDARD_VAR_RE = re.compile(r"\{\{")
_STANDARD_BLOCK_RE = re.compile(r"\{%")


def detect_delimiter_style(template_content: str) -> str:
    """Auto-detect whether the template uses standard or LaTeX-safe delimiters.

    Returns:
        'latex_safe' if \\VAR{} / \\BLOCK{} are found, else 'standard'.
    """
    latex_safe_count = (
        len(_LATEX_SAFE_VAR_RE.findall(template_content))
        + len(_LATEX_SAFE_BLOCK_RE.findall(template_content))
    )
    standard_count = (
        len(_STANDARD_VAR_RE.findall(template_content))
        + len(_STANDARD_BLOCK_RE.findall(template_content))
    )

    if latex_safe_count > standard_count:
        return "latex_safe"
    return "standard"


class _SilentUndefined(jinja2.Undefined):
    """An Undefined that renders as empty string instead of raising.
    This prevents crashes when an optional field is missing from the data."""
    def __str__(self):
        return ""
    def __iter__(self):
        return iter([])
    def __bool__(self):
        return False


def create_jinja_env(style: str) -> jinja2.Environment:
    """Create a Jinja2 Environment with the appropriate delimiters.

    Args:
        style: 'standard' for {{ }}/{% %}, 'latex_safe' for \\VAR{}/\\BLOCK{}.
    """
    if style == "latex_safe":
        return jinja2.Environment(
            block_start_string=r"\BLOCK{",
            block_end_string=r"}",
            variable_start_string=r"\VAR{",
            variable_end_string=r"}",
            comment_start_string=r"\#{",
            comment_end_string=r"}",
            line_statement_prefix="%%",
            line_comment_prefix="%#",
            trim_blocks=True,
            autoescape=False,
            undefined=_SilentUndefined,
        )
    else:
        return jinja2.Environment(
            trim_blocks=True,
            autoescape=False,
            undefined=_SilentUndefined,
        )


# ---------------------------------------------------------------------------
# Template validation
# ---------------------------------------------------------------------------

# Known fields from the TailoredResume schema
_KNOWN_VARIABLES = {
    "name", "email", "phone", "linkedin", "github", "website",
    "summary", "experience", "education", "skills",
    "projects", "achievements", "certifications",
}


def validate_template(template_content: str) -> dict:
    """Validate a Jinja2 LaTeX template.

    Returns a dict with:
        valid (bool): Whether the template is usable.
        errors (list[str]): Critical problems.
        warnings (list[str]): Non-critical observations.
        style (str): Detected delimiter style.
        variables_found (list[str]): Variable names referenced in the template.
    """
    result: dict[str, Any] = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "style": "unknown",
        "variables_found": [],
    }

    # 1. Detect delimiter style
    style = detect_delimiter_style(template_content)
    result["style"] = style

    # 2. Try to parse
    env = create_jinja_env(style)
    try:
        parsed = env.parse(template_content)
    except jinja2.TemplateSyntaxError as exc:
        result["valid"] = False
        result["errors"].append(f"Jinja2 syntax error at line {exc.lineno}: {exc.message}")
        return result

    # 3. Extract referenced variable names
    referenced_vars: set[str] = set()
    for node in parsed.find_all(jinja2.nodes.Name):
        referenced_vars.add(node.name)

    result["variables_found"] = sorted(referenced_vars)

    # 4. Cross-reference against known schema
    missing = _KNOWN_VARIABLES - referenced_vars
    unknown = referenced_vars - _KNOWN_VARIABLES
    # Filter out common Jinja2 loop variables
    loop_vars = {"loop", "item", "exp", "proj", "edu", "cat", "skill_list",
                 "ach", "cert", "bullet", "highlight", "category", "skills_list"}
    unknown = unknown - loop_vars

    if "name" not in referenced_vars:
        result["errors"].append("Template does not reference 'name' — this is required.")
        result["valid"] = False

    if missing:
        result["warnings"].append(
            f"Template does not use these schema fields: {', '.join(sorted(missing))}. "
            f"Those sections will be ignored."
        )

    if unknown:
        result["warnings"].append(
            f"Template references unknown variables: {', '.join(sorted(unknown))}. "
            f"These will cause errors at render time."
        )

    return result


# ---------------------------------------------------------------------------
# Data preparation helpers
# ---------------------------------------------------------------------------

def _escape_value(value: Any, escape_keys: bool = False) -> Any:
    """Recursively escape LaTeX special characters in all string values.
    
    Args:
        value: The data to escape.
        escape_keys: If True, dictionary keys will also be escaped.
    """
    if isinstance(value, str):
        escaped = escape_latex(value)
        return process_bold_markers(escaped)
    elif isinstance(value, list):
        return [_escape_value(v, escape_keys) for v in value]
    elif isinstance(value, dict):
        return {
            (_escape_value(k, True) if escape_keys else k): _escape_value(v, escape_keys)
            for k, v in value.items()
        }
    return value


def _strip_url_protocol(url: str) -> str:
    """Strip https:// or http:// prefix from a URL.

    The LaTeX template already wraps links in \\href{https://...},
    so the data must be a bare host+path like 'github.com/user'.
    """
    if not url:
        return url
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


def _prepare_template_data(tailored_data: TailoredResume) -> dict:
    """Convert a TailoredResume to a dict suitable for Jinja2 rendering.
    """
    raw = tailored_data.model_dump()

    # Normalize link fields: strip protocol prefix to avoid double https://
    for link_field in ("github", "linkedin", "website"):
        if raw.get(link_field):
            raw[link_field] = _strip_url_protocol(raw[link_field])

    # Escape all values, but only escape keys inside the 'skills' dictionary
    # because those are rendered as text in the LaTeX table.
    escaped = {}
    for k, v in raw.items():
        if k == "skills":
            escaped[k] = _escape_value(v, escape_keys=True)
        else:
            escaped[k] = _escape_value(v, escape_keys=False)

    # Set safe defaults for None values so the template never crashes
    for key, value in escaped.items():
        if value is None:
            escaped[key] = ""   # \BLOCK{if field} will be falsy for ""

    return escaped


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_resume(template_content: str, tailored_data: TailoredResume) -> str:
    """Render a Jinja2 LaTeX template with tailored resume data.

    Args:
        template_content: The raw Jinja2 .tex template string.
        tailored_data: The validated TailoredResume Pydantic object.

    Returns:
        The final .tex string ready for pdflatex compilation.
    """
    style = detect_delimiter_style(template_content)
    env = create_jinja_env(style)

    template = env.from_string(template_content)
    data = _prepare_template_data(tailored_data)

    # DEBUG: Print exact keys and value types being passed to Jinja2
    debug_info = {k: (len(v) if isinstance(v, (str, list, dict)) else v) for k, v in data.items()}
    logger.info("RENDER_RESUME: Passing data to Jinja2: %s", debug_info)

    rendered = template.render(**data)
    return rendered


# ---------------------------------------------------------------------------
# PDF compilation
# ---------------------------------------------------------------------------

def compile_pdf(tex_content: str, output_dir: str | None = None) -> str:
    """Compile a .tex string to PDF using pdflatex.

    Args:
        tex_content: The complete LaTeX source.
        output_dir: Directory to write files in. If None, a temp dir is created.

    Returns:
        Absolute path to the generated PDF file.

    Raises:
        RuntimeError: If pdflatex fails to produce a PDF.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="resume_")

    tex_path = os.path.join(output_dir, "resume.tex")
    pdf_path = os.path.join(output_dir, "resume.pdf")

    # Write .tex file (use newline="" to prevent CRLF issues on Windows)
    with open(tex_path, "w", encoding="utf-8", newline="") as f:
        f.write(tex_content)

    # Run pdflatex 3 times for full convergence
    for i in range(3):
        process = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "resume.tex",
            ],
            cwd=output_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    if not os.path.exists(pdf_path):
        logger.error("pdflatex failed.\nSTDOUT: %s\nSTDERR: %s", process.stdout, process.stderr)
        raise RuntimeError(
            f"LaTeX compilation failed. Last pdflatex exit code: {process.returncode}"
        )

    logger.info("PDF compiled successfully: %s", pdf_path)
    return pdf_path
