"""CV parsing: extract text from PDF/DOCX and derive a structured profile.

Heuristic extraction (works offline, no API keys). If an AI provider is
configured the caller may enrich the result afterwards.
"""
import re
from pathlib import Path


def extract_text(path: str | Path) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(p)
    if suffix in (".docx", ".doc"):
        return _extract_docx(p)
    raise ValueError(f"Unsupported file type: {suffix}. Please upload PDF or DOCX.")


def _extract_pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("PDF is password-protected; please upload an unprotected copy.")
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as pdf_exc:
        # Fallback engine
        try:
            from pdfminer.high_level import extract_text as pdftext
            return pdftext(str(p)) or ""
        except Exception:
            raise ValueError(f"Could not read PDF: {pdf_exc}")


def _extract_docx(p: Path) -> str:
    try:
        import docx
        d = docx.Document(str(p))
        parts = [para.text for para in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    except Exception as exc:
        raise ValueError(f"Could not read DOCX: {exc}")


SECTION_KEYWORDS = {
    "summary": ["summary", "profile", "objective", "about me", "personal statement"],
    "education": ["education", "academic background", "qualifications", "academics"],
    "experience": ["experience", "employment", "work history", "professional experience",
                   "career history", "teaching experience"],
    "skills": ["skills", "technical skills", "competencies", "technologies", "core skills"],
    "certifications": ["certification", "certificate", "licenses", "courses completed",
                       "credential"],
    "languages": ["language", "languages"],
    "contact": ["contact"],
}

KNOWN_SKILLS = [
    "python", "javascript", "typescript", "java", "c++", "c#", "sql", "html", "css", "php",
    "django", "flask", "fastapi", "react", "node.js", "vue", "angular", "spring",
    "machine learning", "deep learning", "nlp", "computer vision", "data analysis",
    "data science", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "keras",
    "llm", "large language models", "openai", "langchain", "prompt engineering", "rag",
    "n8n", "zapier", "make", "automation", "workflow automation", "ai agents",
    "git", "github", "docker", "kubernetes", "aws", "azure", "gcp", "linux",
    "rest api", "graphql", "postgresql", "mysql", "mongodb", "redis", "excel", "power bi",
    "tableau", "jira", "agile", "scrum", "devops", "ci/cd", "testing", "qa",
    "communication", "leadership", "teaching", "curriculum development", "classroom management",
    "lesson planning", "student assessment", "mentoring", "public speaking", "problem solving",
    "project management", "teamwork", "time management",
]

DEGREE_PATTERNS = [
    r"(?:BS|B\.?S\.?|BA|B\.?A\.?|BSc|B\.?Sc\.?|BE|B\.?E\.?|BCA|B\.Tech)\b[^.\n]{0,80}",
    r"(?:MS|M\.?S\.?|MA|M\.?A\.?|MSc|M\.?Sc\.?|ME|M\.?Phil|MBA|Ph\.?D|Doctorate)\b[^.\n]{0,80}",
    r"(?:FSc|FA|HSSC|SSC|Intermediate|A-?Levels?)\b[^.\n]{0,60}",
    r"Bachelor(?:'?s)?\s+(?:of|in)[^.\n]{0,60}",
    r"Master(?:'?s)?\s+(?:of|in)[^.\n]{0,60}",
    r"Doctor(?:of|ate)[^.\n]{0,60}",
]

YEAR_RANGE_RE = re.compile(r"(19|20)\d{2}\s*[-–—to/]+\s*((?:19|20)\d{2}|present|current|now)",
                           re.IGNORECASE)


def _split_sections(text: str) -> dict[str, str]:
    """Split CV into named sections using heading-like lines."""
    lines = text.splitlines()
    sections: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for line in lines:
        stripped = line.strip()
        low = stripped.lower().rstrip(":")
        matched = None
        if 0 < len(stripped) <= 45 and (stripped.endswith(":") or stripped.isupper()
                                        or low in sum(SECTION_KEYWORDS.values(), [])):
            for sec, kws in SECTION_KEYWORDS.items():
                if any(low.startswith(k) or low == k for k in kws):
                    matched = sec
                    break
        if matched:
            current = matched
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _find_emails(text):
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)


def _find_phones(text):
    return re.findall(r"(?:\+?\d{1,3}[\s-]?)?(?:\(\d{2,5}\)[\s-]?)?\d{3,4}[\s-]?\d{3,4}(?:[\s-]?\d{2,4})?",
                      text)


def parse_cv(text: str) -> dict:
    """Return a structured profile dict from raw CV text."""
    sections = _split_sections(text)
    emails = _find_emails(text)
    phones = [p for p in _find_phones(sections.get("_header", "") or text[:400]) if sum(c.isdigit() for c in p) >= 9]

    # Name: first non-empty header line that looks like a person name
    full_name = ""
    for line in sections.get("_header", "").splitlines():
        s = line.strip()
        if not s or "@" in s or any(ch.isdigit() for ch in s):
            continue
        words = s.split()
        if 1 < len(words) <= 5 and all(w[0].isupper() or w.isupper() for w in words if w):
            full_name = s.title() if s.isupper() else s
            break

    headline = ""
    for line in sections.get("_header", "").splitlines():
        s = line.strip()
        if s and s != full_name and "@" not in s and len(s) > 6:
            headline = s
            break

    skills_lower = (sections.get("skills", "") + "\n" + sections.get("_header", "")).lower()
    found_skills = []
    for sk in KNOWN_SKILLS:
        if re.search(r"(?<![a-z])" + re.escape(sk) + r"(?![a-z])", skills_lower):
            found_skills.append(sk)
    # also scan whole document lightly for tech tokens
    body_low = text.lower()
    for sk in list(KNOWN_SKILLS):
        if sk not in found_skills and re.search(r"(?<![a-z])" + re.escape(sk) + r"(?![a-z])", body_low):
            if sk in ("python", "sql", "git", "excel", "linux", "aws", "docker", "react"):
                found_skills.append(sk)
    found_skills = list(dict.fromkeys(found_skills))[:40]

    education = []
    for pat in DEGREE_PATTERNS:
        for m in re.finditer(pat, sections.get("education", "") or text, re.IGNORECASE):
            chunk = re.sub(r"\s+", " ", m.group(0)).strip(" .,-")
            if chunk and chunk.lower() not in [e.lower() for e in education]:
                education.append(chunk[:200])
    edu_lines = [l.strip() for l in sections.get("education", "").splitlines() if len(l.strip()) > 6]
    for l in edu_lines[:6]:
        norm = re.sub(r"\s+", " ", l)[:200]
        if norm.lower() not in [e.lower() for e in education]:
            education.append(norm)

    experience = []
    exp_text = sections.get("experience", "")
    for block in re.split(r"\n(?=\S)", exp_text):
        b = block.strip()
        if len(b) > 20:
            experience.append(re.sub(r"\s+", " ", b)[:600])
    if not experience:
        for line in text.splitlines():
            if YEAR_RANGE_RE.search(line) and len(line.strip()) > 15:
                experience.append(re.sub(r"\s+", " ", line.strip())[:400])

    certifications = []
    for line in sections.get("certifications", "").splitlines():
        s = line.strip()
        if len(s) > 4 and not s.endswith(":"):
            certifications.append(re.sub(r"\s+", " ", s)[:200])

    languages = []
    lang_text = sections.get("languages", "") or ""
    for lang in ("English", "Urdu", "Arabic", "French", "German", "Spanish", "Chinese",
                 "Punjabi", "Pashto", "Sindhi", "Turkish", "Russian", "Hindi", "Persian", "Farsi"):
        if re.search(lang.lower(), lang_text.lower() or "", ):
            languages.append(lang)
    if not languages:
        for lang in ("English", "Urdu", "Arabic", "French", "German", "Spanish"):
            if re.search(r"(?i)\b" + lang.lower() + r"\b", text):
                languages.append(lang)

    # Years of experience: earliest start year found in date ranges → now
    years = 0.0
    starts = []
    for m in YEAR_RANGE_RE.finditer(exp_text or text):
        g = m.groups()          # (century, last-two-digits-or-None-if-present)
        rest = g[1]
        if rest and rest.isdigit():
            starts.append(int(g[0] + rest))
        else:
            # "2018 – present": grab the start year directly preceding the separator
            sm = re.search(r"((?:19|20)\d{2})\s*[-–—to/]+\s*(?:present|current|now)",
                           m.group(0), re.IGNORECASE)
            if sm:
                starts.append(int(sm.group(1)))
    now_year = __import__("datetime").datetime.now().year
    if starts:
        years = float(max(0, min(now_year - min(starts), 40)))
    y_match = re.search(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", text, re.IGNORECASE)
    if y_match:
        years = max(years, float(y_match.group(1)))

    summary = sections.get("summary", "") or ""

    location = ""
    loc_m = re.search(r"(?im)^\s*(?:location|address)\s*:?\s*(.+)$", text)
    if loc_m:
        location = loc_m.group(1).strip()[:120]
    elif re.search(r"(?i)pakistan", sections.get("_header", "")):
        location = "Pakistan"

    return {
        "full_name": full_name,
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "location": location,
        "headline": headline,
        "summary": [p.strip() for p in summary.split("\n\n") if p.strip()][:6],
        "education": education[:10],
        "experience": experience[:15],
        "skills": found_skills,
        "certifications": certifications[:10],
        "languages": languages,
        "years_experience": years,
    }
