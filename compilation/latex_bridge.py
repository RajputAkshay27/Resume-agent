"""
latex_bridge.py — Jinja2 LaTeX Template Rendering + PDF Compilation
for the Resume Compilation Service.

Mirrors agent/latex_bridge.py, but without the agent-specific imports.
"""

import os
import re
import subprocess
import tempfile
import logging
from typing import Any

import jinja2

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

_LATEX_ESCAPE_RE = re.compile(
    "|".join(re.escape(c) for c in _LATEX_SPECIAL_CHARS)
)


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in a string."""
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
    """Auto-detect whether the template uses standard or LaTeX-safe delimiters."""
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
    """Renders as empty string instead of raising."""
    def __str__(self):
        return ""
    def __iter__(self):
        return iter([])
    def __bool__(self):
        return False


def create_jinja_env(style: str) -> jinja2.Environment:
    """Create a Jinja2 Environment with the appropriate delimiters."""
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
# Data preparation
# ---------------------------------------------------------------------------

def _escape_value(value: Any, escape_keys: bool = False) -> Any:
    """Recursively escape LaTeX special characters in all string values."""
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
    """Strip https:// or http:// prefix from a URL."""
    if not url:
        return url
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


def _prepare_template_data(raw: dict) -> dict:
    """Convert a raw dictionary to a dict suitable for Jinja2 rendering."""
    # Ensure raw is a dict and make a shallow copy at least to avoid mutating the original
    raw = dict(raw)
    
    for link_field in ("github", "linkedin", "website"):
        if raw.get(link_field):
            raw[link_field] = _strip_url_protocol(raw[link_field])

    escaped = {}
    for k, v in raw.items():
        if k == "skills" and isinstance(v, dict):
            clean_skills = {cat.replace("_", " "): items for cat, items in v.items()}
            escaped[k] = _escape_value(clean_skills, escape_keys=True)
        else:
            escaped[k] = _escape_value(v, escape_keys=False)

    for key, value in escaped.items():
        if value is None:
            escaped[key] = ""

    return escaped


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_resume(template_content: str, tailored_data: dict) -> str:
    """Render a Jinja2 LaTeX template with tailored resume data.

    Returns:
        The final .tex string ready for pdflatex compilation.
    """
    style = detect_delimiter_style(template_content)
    env = create_jinja_env(style)
    template = env.from_string(template_content)
    data = _prepare_template_data(tailored_data)

    debug_info = {k: (len(v) if isinstance(v, (str, list, dict)) else v) for k, v in data.items()}
    logger.info("RENDER_RESUME: Passing data to Jinja2: %s", debug_info)

    return template.render(**data)


# ---------------------------------------------------------------------------
# PDF compilation
# ---------------------------------------------------------------------------

def compile_pdf(tex_content: str, output_dir: str | None = None) -> str:
    """Compile a .tex string to PDF using pdflatex.

    Returns:
        Absolute path to the generated PDF file.

    Raises:
        RuntimeError: If pdflatex fails to produce a PDF.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="resume_")

    tex_path = os.path.join(output_dir, "resume.tex")
    pdf_path = os.path.join(output_dir, "resume.pdf")

    with open(tex_path, "w", encoding="utf-8", newline="") as f:
        f.write(tex_content)

    process = None
    for _ in range(3):
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
