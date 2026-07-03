"""
tools_analysis.py — Resume intelligence tools for the Resume Agent v2.

Provides three agent tools:
  - analyze_jd():       Parse a JD into structured requirements. Token-efficient
                        summary replaces raw JD prose sent to the tailoring agent.
  - score_ats_match():  Keyword match scoring between a tailored resume and the JD.
                        Used in the ATS feedback loop to trigger targeted re-tailoring.
  - diff_resume():      Compare two tailored outputs and return a human-readable diff.

These are registered with the orchestrator agent in agent.py.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from schemas import ATSScore, JDAnalysis, SectionScore, TailoredSections
from guardrails import sanitize_text_input

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword extraction helpers
# ---------------------------------------------------------------------------

# Common English stop words to exclude from keyword extraction
_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "we", "our", "you", "your", "they", "their", "it", "its", "that",
    "this", "these", "those", "which", "who", "whom", "what", "when",
    "where", "how", "why", "if", "then", "so", "also", "such", "other",
    "strong", "experience", "ability", "excellent", "good", "must", "skills",
    "work", "working", "years", "team", "looking", "seeking", "role", "join",
    "responsibilities", "requirements", "qualifications", "required", "preferred",
}

# Technology and skill-related terms that are always high-value keywords
_TECH_BOOST_RE = re.compile(
    r"\b(python|go|golang|java|typescript|javascript|rust|c\+\+|scala|"
    r"fastapi|django|flask|react|node\.?js|next\.?js|"
    r"kubernetes|k8s|docker|helm|terraform|ansible|"
    r"aws|gcp|azure|s3|ec2|lambda|cloud|"
    r"postgresql|mysql|mongodb|redis|elasticsearch|sqlite|"
    r"kafka|rabbitmq|celery|grpc|graphql|rest|api|"
    r"opentelemetry|prometheus|grafana|datadog|"
    r"github|gitlab|ci/cd|devops|mlops|"
    r"machine learning|ml|llm|ai|nlp|deep learning|"
    r"microservice|distributed|scalab|observabilit|"
    r"pydantic|jinja2|fastapi|sqlalchemy)\b",
    re.IGNORECASE,
)


def _extract_keywords(text: str, top_n: int = 40) -> list[str]:
    """
    Extract the most significant keywords from a text.

    Strategy:
      1. Tokenize on word boundaries.
      2. Remove stop words and very short tokens.
      3. Boost tech/skill terms.
      4. Return top-N by frequency.
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\.\+\#\-_/]{1,30}", text.lower())
    filtered = [t for t in tokens if t not in _STOP_WORDS and len(t) > 2]

    counts = Counter(filtered)

    # Boost tech terms (multiply their count × 3 so they surface first)
    for token in list(counts.keys()):
        if _TECH_BOOST_RE.match(token):
            counts[token] *= 3

    return [kw for kw, _ in counts.most_common(top_n)]


def _text_from_tailored(tailored: dict) -> str:
    """Flatten all text content of a TailoredSections dict into a single string."""
    parts: list[str] = []

    parts.append(tailored.get("summary", ""))

    for exp in tailored.get("experience", []):
        parts.append(exp.get("company", ""))
        parts.append(exp.get("title", ""))
        parts.extend(exp.get("bullets", []))

    for proj in tailored.get("projects", []):
        parts.append(proj.get("name", ""))
        parts.append(proj.get("technologies", "") or "")
        parts.append(proj.get("description", "") or "")
        parts.extend(proj.get("bullets", []))

    for category, skills in tailored.get("skills", {}).items():
        parts.append(category)
        parts.extend(skills)

    parts.extend(tailored.get("achievements", []))

    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Tool: analyze_jd
# ---------------------------------------------------------------------------

def analyze_jd(tool_context: ToolContext) -> str:
    """
    Analyze the current job description and extract structured requirements.

    This produces a compact JDAnalysis that replaces raw JD prose in the tailoring
    context, saving ~400 tokens per run. The analysis is cached in session state.

    Returns a JSON string with the JDAnalysis schema.
    """
    try:
        # Load JD from session state (put there by get_all_context) or storage
        scratchpad = tool_context.state.get("scratchpad") or {}
        jd_raw = scratchpad.get("job_description", "")

        if not jd_raw:
            from storage_client import storage
            jd_raw = storage().load_job_description() or ""

        if not jd_raw:
            return json.dumps({
                "error": "No job description found. Please set a job description first."
            })

        jd = sanitize_text_input(jd_raw, max_length=20_000, field_name="job_description")

        # ── Extract role title ────────────────────────────────────────────
        title_match = re.search(
            r"(?:position|role|job title|title)[:\s]+([A-Za-z\s\-/,]{5,60})",
            jd, re.IGNORECASE
        )
        role_title = title_match.group(1).strip() if title_match else "Software Engineer"

        # ── Extract company name ──────────────────────────────────────────
        company_match = re.search(
            r"(?:company|organization|employer|about us)[:\s]+([A-Za-z0-9\s\.\-&]{3,50})",
            jd, re.IGNORECASE
        )
        company_name = company_match.group(1).strip() if company_match else ""

        # ── Seniority level ───────────────────────────────────────────────
        seniority = ""
        for level in ["principal", "staff", "lead", "senior", "junior", "mid-level", "associate"]:
            if re.search(r"\b" + level + r"\b", jd, re.IGNORECASE):
                seniority = level.title()
                break

        # ── Experience years ─────────────────────────────────────────────
        years_match = re.search(r"(\d+)\+?\s*years?\s*(?:of\s+)?(?:experience|exp)", jd, re.IGNORECASE)
        experience_years: Optional[int] = int(years_match.group(1)) if years_match else None

        # ── Skills categorization ─────────────────────────────────────────
        language_pattern = r"\b(Python|Go|Golang|Java|TypeScript|JavaScript|Rust|C\+\+|Scala|Ruby|PHP|Swift|Kotlin)\b"
        framework_pattern = r"\b(FastAPI|Django|Flask|React|Next\.?js|Node\.?js|Spring|Rails|Express|Vue|Angular)\b"
        tools_pattern = r"\b(Docker|Kubernetes|K8s|Terraform|Helm|Ansible|Git|GitHub|GitLab|Jenkins|CI/CD|Prometheus|Grafana|OpenTelemetry)\b"
        cloud_pattern = r"\b(AWS|GCP|Azure|S3|EC2|Lambda|Cloud Run|GKE|EKS|AKS)\b"
        db_pattern = r"\b(PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|SQLite|Cassandra|DynamoDB)\b"

        required_skills: dict[str, list[str]] = {}
        for category, pattern in [
            ("Languages", language_pattern),
            ("Frameworks", framework_pattern),
            ("Tools & DevOps", tools_pattern),
            ("Cloud", cloud_pattern),
            ("Databases", db_pattern),
        ]:
            found = list(dict.fromkeys(re.findall(pattern, jd, re.IGNORECASE)))
            if found:
                required_skills[category] = found

        # ── Key responsibilities ─────────────────────────────────────────
        resp_section = re.search(
            r"(?:responsibilities|what you.ll do|key responsibilities|your role)[:\s]*\n(.*?)(?:\n\n|\Z)",
            jd, re.IGNORECASE | re.DOTALL
        )
        responsibilities: list[str] = []
        if resp_section:
            lines = resp_section.group(1).split("\n")
            for line in lines:
                line = re.sub(r"^[\s\-\*\•]+", "", line).strip()
                if 10 < len(line) < 200:
                    responsibilities.append(line)
                if len(responsibilities) >= 6:
                    break

        # ── All keywords ─────────────────────────────────────────────────
        keywords = _extract_keywords(jd, top_n=30)

        analysis = JDAnalysis(
            role_title=role_title,
            company_name=company_name,
            seniority_level=seniority,
            required_skills=required_skills,
            key_responsibilities=responsibilities,
            experience_years=experience_years,
            keywords=keywords,
        )

        # Cache in session state for use by tailoring agent
        scratchpad["jd_analysis"] = analysis.model_dump()
        tool_context.state["scratchpad"] = scratchpad

        logger.info(
            "[analyze_jd] Extracted %d required skills, %d responsibilities, %d keywords.",
            sum(len(v) for v in required_skills.values()),
            len(responsibilities),
            len(keywords),
        )

        return json.dumps(analysis.model_dump(), indent=2)

    except Exception as e:
        logger.error("[analyze_jd] Error: %s", e, exc_info=True)
        return json.dumps({"error": f"JD analysis failed: {e}"})


# ---------------------------------------------------------------------------
# Tool: score_ats_match
# ---------------------------------------------------------------------------

ATS_PASS_THRESHOLD = float(
    __import__("os").getenv("ATS_PASS_THRESHOLD", "75")
)


def score_ats_match(tool_context: ToolContext) -> str:
    """
    Score the current tailored resume against the job description for ATS keyword match.

    Used in the feedback loop: if overall_score < threshold, the orchestrator
    should re-tailor specific low-coverage sections rather than regenerating the full resume.

    Returns a JSON string with the ATSScore schema.
    """
    try:
        tailored = tool_context.state.get("tailored_sections") or tool_context.state.get("tailored_resume")
        if not tailored:
            return json.dumps({
                "error": "No tailored resume in state. Run tailoring first."
            })

        scratchpad = tool_context.state.get("scratchpad") or {}
        jd_raw = scratchpad.get("job_description", "")

        if not jd_raw:
            from storage_client import storage
            jd_raw = storage().load_job_description() or ""

        if not jd_raw:
            return json.dumps({"error": "No job description available for scoring."})

        # Get JD keywords — prefer cached analysis
        jd_analysis = scratchpad.get("jd_analysis")
        if jd_analysis:
            jd_keywords = set(jd_analysis.get("keywords", []))
            required_skills_flat = [
                skill.lower()
                for skills in jd_analysis.get("required_skills", {}).values()
                for skill in skills
            ]
            jd_keywords.update(required_skills_flat)
        else:
            jd_keywords = set(_extract_keywords(jd_raw, top_n=40))

        # Score per section
        section_scores: list[SectionScore] = []

        def _score_section(name: str, text: str) -> SectionScore:
            section_keywords = set(_extract_keywords(text, top_n=30))
            matched = sorted(jd_keywords & section_keywords)
            missing = sorted(jd_keywords - section_keywords)
            pct = (len(matched) / len(jd_keywords) * 100) if jd_keywords else 0.0
            return SectionScore(
                section_name=name,
                matched_keywords=matched,
                missing_keywords=missing[:10],  # top 10 missing per section
                coverage_pct=round(pct, 1),
            )

        # Summary section
        summary_text = tailored.get("summary", "")
        if summary_text:
            section_scores.append(_score_section("Summary", summary_text))

        # Experience section
        exp_text = " ".join(
            " ".join(e.get("bullets", [])) + " " + e.get("title", "")
            for e in tailored.get("experience", [])
        )
        if exp_text.strip():
            section_scores.append(_score_section("Experience", exp_text))

        # Projects section
        proj_text = " ".join(
            (p.get("description", "") or "") + " " +
            " ".join(p.get("bullets", [])) + " " +
            (p.get("technologies", "") or "")
            for p in tailored.get("projects", [])
        )
        if proj_text.strip():
            section_scores.append(_score_section("Projects", proj_text))

        # Skills section
        skills_text = " ".join(
            cat + " " + " ".join(items)
            for cat, items in tailored.get("skills", {}).items()
        )
        if skills_text.strip():
            section_scores.append(_score_section("Skills", skills_text))

        # Overall score: weighted average (experience + skills weighted higher)
        weights = {"Summary": 1.0, "Experience": 2.0, "Projects": 1.0, "Skills": 2.0}
        total_weight = 0.0
        weighted_sum = 0.0
        for ss in section_scores:
            w = weights.get(ss.section_name, 1.0)
            weighted_sum += ss.coverage_pct * w
            total_weight += w
        overall = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0

        # Global missing keywords (in JD but not in full resume)
        full_resume_text = _text_from_tailored(tailored)
        full_keywords = set(_extract_keywords(full_resume_text, top_n=60))
        global_missing = sorted(jd_keywords - full_keywords)[:15]

        # Suggestions
        suggestions: list[str] = []
        weak_sections = [s for s in section_scores if s.coverage_pct < 50]
        for ws in weak_sections:
            top_missing = ws.missing_keywords[:3]
            if top_missing:
                suggestions.append(
                    f"Improve {ws.section_name} coverage: add keywords {top_missing}."
                )
        if global_missing[:5]:
            suggestions.append(
                f"Consider adding these high-value JD keywords anywhere in the resume: "
                f"{global_missing[:5]}."
            )

        passed = overall >= ATS_PASS_THRESHOLD

        score = ATSScore(
            overall_score=overall,
            section_scores=section_scores,
            missing_keywords=global_missing,
            suggestions=suggestions,
            passed_threshold=passed,
        )

        logger.info(
            "[score_ats_match] Overall ATS score: %.1f%% (threshold=%.1f%%, passed=%s).",
            overall, ATS_PASS_THRESHOLD, passed,
        )

        return json.dumps(score.model_dump(), indent=2)

    except Exception as e:
        logger.error("[score_ats_match] Error: %s", e, exc_info=True)
        return json.dumps({"error": f"ATS scoring failed: {e}"})


# ---------------------------------------------------------------------------
# Tool: diff_resume
# ---------------------------------------------------------------------------

def diff_resume(tool_context: ToolContext) -> str:
    """
    Compare the current tailored resume with the previous version in history.

    Returns a human-readable diff showing what changed between the last two
    tailored outputs. Useful for the interactive CLI and ATS feedback loop iterations.

    Returns a JSON string with {added, removed, changed_sections}.
    """
    try:
        from storage_client import storage
        history = storage().load_tailored_history()

        if len(history) < 2:
            return json.dumps({
                "message": "Not enough history to diff. Need at least 2 tailored outputs."
            })

        previous = history[-2]["tailored"]
        current = history[-1]["tailored"]

        diff_result: dict = {"changed_sections": []}

        # Summary diff
        prev_summary = previous.get("summary", "")
        curr_summary = current.get("summary", "")
        if prev_summary != curr_summary:
            diff_result["changed_sections"].append("summary")
            diff_result["summary"] = {
                "previous": prev_summary[:200] + ("..." if len(prev_summary) > 200 else ""),
                "current": curr_summary[:200] + ("..." if len(curr_summary) > 200 else ""),
            }

        # Experience diff — compare by company + title
        prev_exp_keys = {(e.get("company"), e.get("title")) for e in previous.get("experience", [])}
        curr_exp_keys = {(e.get("company"), e.get("title")) for e in current.get("experience", [])}
        added_exp = curr_exp_keys - prev_exp_keys
        removed_exp = prev_exp_keys - curr_exp_keys
        if added_exp or removed_exp:
            diff_result["changed_sections"].append("experience")
            diff_result["experience"] = {
                "added": [f"{c} @ {t}" for c, t in sorted(added_exp)],
                "removed": [f"{c} @ {t}" for c, t in sorted(removed_exp)],
            }

        # Projects diff — compare by name
        prev_proj_names = {p.get("name") for p in previous.get("projects", [])}
        curr_proj_names = {p.get("name") for p in current.get("projects", [])}
        added_proj = curr_proj_names - prev_proj_names
        removed_proj = prev_proj_names - curr_proj_names
        if added_proj or removed_proj:
            diff_result["changed_sections"].append("projects")
            diff_result["projects"] = {
                "added": sorted(added_proj),
                "removed": sorted(removed_proj),
            }

        # Skills diff
        prev_skills_flat = set(
            s for lst in previous.get("skills", {}).values() for s in lst
        )
        curr_skills_flat = set(
            s for lst in current.get("skills", {}).values() for s in lst
        )
        added_skills = sorted(curr_skills_flat - prev_skills_flat)
        removed_skills = sorted(prev_skills_flat - curr_skills_flat)
        if added_skills or removed_skills:
            diff_result["changed_sections"].append("skills")
            diff_result["skills"] = {
                "added": added_skills,
                "removed": removed_skills,
            }

        if not diff_result["changed_sections"]:
            diff_result["message"] = "No changes detected between the last two versions."

        return json.dumps(diff_result, indent=2)

    except Exception as e:
        logger.error("[diff_resume] Error: %s", e, exc_info=True)
        return json.dumps({"error": f"Resume diff failed: {e}"})
