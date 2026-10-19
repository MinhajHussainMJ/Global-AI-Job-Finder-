"""AI layer.

- If an OpenAI-compatible provider is configured (AI_API_KEY), use it for
  matching/cover letters/tailored CV guidance.
- Otherwise fall back to a transparent, deterministic heuristic engine so the
  product still works with zero API spend. Every output states which engine
  produced it — no hidden fakery.
"""
import json
import re

import requests

from .. import config


def ai_available() -> bool:
    return bool(config.AI_API_KEY)


def _chat(system: str, user: str, max_tokens=900, temperature=0.3) -> str | None:
    if not ai_available():
        return None
    try:
        resp = requests.post(
            f"{config.AI_API_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.AI_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.AI_MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": temperature, "max_tokens": max_tokens},
            timeout=60)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        # Log-and-degrade: caller falls back to heuristics.
        print(f"[ai] provider error: {exc}")
        return None


TRUTH_RULES = ("You may ONLY use facts present in the candidate profile. Never invent "
               "degrees, employers, dates, skills, references or eligibility. If something "
               "is unknown, say it is unknown.")


# --------------------------------------------------------------------------
SKILL_SYNONYMS = {
    "python": ["python", "python3"],
    "js": ["javascript", "js", "typescript", "react", "node"],
    "sql": ["sql", "mysql", "postgresql", "postgres", "database"],
    "ml": ["machine learning", "ml", "deep learning", "tensorflow", "pytorch", "scikit"],
    "llm": ["llm", "large language model", "openai", "gpt", "langchain", "prompt engineering", "rag"],
    "automation": ["n8n", "zapier", "make", "workflow automation", "rpa", "ai agents"],
    "web": ["html", "css", "flask", "django", "fastapi", "rest api", "web development"],
    "teaching": ["teaching", "curriculum", "lesson planning", "classroom", "student assessment",
                 "mentoring", "tutoring"],
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z+#.]{2,}", (text or "").lower()))


def _skill_hits(job_text: str, candidate_skills: list[str]) -> tuple[list[str], list[str]]:
    jt = job_text.lower()
    matched, wanted = [], []
    seen_group = set()
    for sk in candidate_skills:
        low = sk.lower()
        for grp, variants in SKILL_SYNONYMS.items():
            if low in [v.lower() for v in variants]:
                if grp in seen_group:
                    break
                if any(v.lower() in jt for v in variants):
                    matched.append(sk)
                    seen_group.add(grp)
                break
        else:
            if low in jt and sk not in matched:
                matched.append(sk)
    # required-but-missing: common tech terms in JD absent from candidate skills
    cand_low = " | ".join(s.lower() for s in candidate_skills)
    for term in ("python", "sql", "aws", "docker", "kubernetes", "react", "java", "c++",
                 "machine learning", "tensorflow", "pytorch", "excel", "power bi",
                 "teaching qualification", "bct", "bed", "pgce", "tesol", "celta", "tefl"):
        if term in jt and term not in cand_low:
            wanted.append(term)
    return list(dict.fromkeys(matched)), list(dict.fromkeys(wanted))[:8]


def extract_requirements(text: str) -> dict:
    """Pull experience/education requirements mentioned in a JD."""
    t = text or ""
    years_req = None
    m = re.search(r"(\d{1,2})\s*(?:\+|or more|years?|yrs?)\s*(?:of\s*)?(?:professional\s*|relevant\s*)?"
                  r"(?:experience|teaching)", t, re.I)
    if m:
        years_req = float(m.group(1))
    degree_req = ""
    for pat, label in ((r"\b(?:BS|BSc|BA|BE|B\.Tech|Bachelor(?:'?s)?)\b[^.;\n]{0,60}", "Bachelor's"),
                       (r"\b(?:MS|MSc|MA|ME|Master(?:'?s)?|MPhil)\b[^.;\n]{0,60}", "Master's"),
                       (r"\bPh\.?D\b[^.;\n]{0,40}", "PhD")):
        if re.search(pat, t, re.I):
            degree_req = label
            break
    eng_req = bool(re.search(r"(fluent|proficient|native|advanced)\s+english|english\s+(?:language\s+)?"
                             r"(proficiency|fluency)", t, re.I))
    loc_req = re.search(r"(must (?:be located|have).{0,40}(?:work authorization|right to work|citizen)|"
                        r"only (?:applicants )?(?:located|residing)|not available outside)", t, re.I)
    return {"years_required": years_req, "degree_required": degree_req,
            "english_required": eng_req,
            "local_authorization_required": bool(loc_req)}


def heuristic_match(profile: dict, prefs: dict, job_title: str, job_desc: str,
                    job_location: str, job_country: str, remote: bool,
                    salary_str: str, visa_note: str) -> dict:
    """Deterministic 0-100 scoring with explainable reasons."""
    blob = f"{job_title}\n{job_desc}"
    reasons, missing, elig = [], [], []
    score = 30  # base

    skills = profile.get("skills", []) or []
    matched_sk, missing_sk = _skill_hits(blob, skills)
    if matched_sk:
        pts = min(25, 4 * len(matched_sk))
        score += pts
        reasons.append(f"Skills overlap: {', '.join(matched_sk[:8])}")
    if missing_sk:
        missing.extend(missing_sk)
        score -= min(10, 2 * len(missing_sk))

    titles = [t.lower() for t in prefs.get("job_titles", []) if t]
    fields = [f.lower() for f in prefs.get("fields", []) if f]
    tl = job_title.lower() + " " + blob[:400].lower()
    title_hit = next((t for t in titles if t and t in tl), "")
    field_hit = next((f for f in fields if f and f.split()[0] in tl), "")
    if title_hit:
        score += 15
        reasons.append(f"Matches your preferred role “{title_hit}”.")
    elif field_hit:
        score += 8
        reasons.append(f"Falls within your field of interest “{field_hit}”.")
    else:
        missing.append("Role is outside your explicitly listed target titles")
        score -= 5

    reqs = extract_requirements(blob)
    py = float(profile.get("years_experience", 0) or 0)
    if reqs["years_required"] is not None:
        if py >= reqs["years_required"]:
            score += 10
            reasons.append(f"{py:.0f} yrs experience meets the stated {reqs['years_required']:.0f}-yr requirement.")
        else:
            score -= 12
            missing.append(f"{reqs['years_required']:.0f} years' experience requested (profile shows ~{py:.0f})")
    if reqs["degree_required"]:
        edu_blob = " ".join(profile.get("education", []) or []).lower()
        if any(k in edu_blob for k in ("bs", "bachelor", "bsc", "be ", "b.e", "b.tech", "ba")):
            score += 8
            reasons.append(f"Bachelor's-level education on file vs. requested {reqs['degree_required']}.")
        else:
            missing.append(f"{reqs['degree_required']} indicated as required; verify your degree matches")
            score -= 6

    countries = [c.lower() for c in prefs.get("countries", ["Worldwide"])]
    worldwide = prefs.get("worldwide", True) or "worldwide" in countries
    jc = (job_country or "").lower()
    if worldwide or remote or any(c in (jc + " " + job_location.lower()) for c in countries if c != "worldwide"):
        score += 8
        reasons.append("Location fits your target countries / remote preference.")
    else:
        score -= 10
        elig.append(f"Location “{job_location or job_country or 'unspecified'}” is outside your selected countries")

    modes = [m.lower() for m in prefs.get("work_modes", [])]
    if remote and "remote" not in modes and modes:
        score -= 5
        elig.append("Job is remote but Remote was not among your work modes")

    etypes = [e.lower() for e in prefs.get("employment_types", [])]
    jtype = ("" ).lower()
    for e in etypes:
        if e.split()[0] in blob.lower():
            jtype = e
            break
    if etypes and jtype:
        score += 4
        reasons.append(f"Employment type aligns ({etypes[0]}… accepted list).")

    sal_num = None
    m = re.search(r"(\d{2,3}[,.]?\d{3})", salary_str or "")
    if m:
        sal_num = float(m.group(1).replace(",", ""))
    min_sal = float(prefs.get("min_salary", 0) or 0)
    if min_sal and sal_num:
        if sal_num >= min_sal:
            score += 6
            reasons.append(f"Listed salary ({salary_str}) meets your minimum.")
        else:
            score -= 8
            elig.append(f"Listed salary ({salary_str}) is below your stated minimum {prefs.get('currency','USD')} {min_sal:,.0f}")
    elif min_sal and not sal_num:
        elig.append("No salary disclosed — cannot verify against your minimum")

    if prefs.get("visa_sponsorship_required"):
        if visa_note.startswith("Sponsorship mentioned"):
            score += 8
            reasons.append("Posting mentions visa sponsorship/relocation support.")
        elif visa_note == "No sponsorship mentioned":
            score -= 15
            elig.append("Posting says no visa sponsorship — you require sponsorship")
        else:
            score -= 4
            elig.append("Visa sponsorship status not stated — confirm before applying")

    if reqs["english_required"]:
        langs = [l.lower() for l in profile.get("languages", []) or []]
        if "english" in langs:
            score += 4
            reasons.append("English-language requirement met per your profile.")
        else:
            missing.append("English proficiency requested; not evidenced in profile")

    if reqs["local_authorization_required"]:
        score -= 12
        elig.append("Posting requires local work authorization/right to work — check your status")

    score = max(0, min(100, int(round(score))))
    return {"score": score, "reasons": reasons, "missing": missing, "eligibility": elig,
            "engine": "heuristic"}


def match_job(profile: dict, prefs: dict, job: dict) -> dict:
    """Score one job. Uses AI when configured, else heuristic. Always returns
    a heuristic baseline so AI results can be sanity-checked."""
    base = heuristic_match(profile, prefs, job.get("title", ""), job.get("description", ""),
                           job.get("location", ""), job.get("country", ""),
                           job.get("remote", False), job.get("salary", ""),
                           job.get("visa_note", "unknown"))
    if not ai_available():
        return base
    sys_prompt = ("You are a meticulous job-match analyst. " + TRUTH_RULES +
                  " Respond ONLY with JSON: {\"score\":0-100,\"reasons\":[str],\"missing\":[str],"
                  "\"eligibility\":[str]} . Prioritize realistic fit over title similarity.")
    user_prompt = json.dumps({
        "candidate_profile": {k: profile.get(k) for k in
                              ("headline", "skills", "experience", "education",
                               "certifications", "languages", "years_experience", "location")},
        "preferences": {k: prefs.get(k) for k in
                        ("job_titles", "fields", "countries", "work_modes", "employment_types",
                         "min_salary", "currency", "visa_sponsorship_required")},
        "job": {k: job.get(k) for k in ("title", "company", "location", "country", "remote",
                                        "salary", "visa_note")} | {"description": job.get("description", "")[:4000]},
        "heuristic_baseline": base,
    }, ensure_ascii=False, default=str)
    out = _chat(sys_prompt, user_prompt, max_tokens=700)
    if not out:
        return base
    try:
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip())
        data["engine"] = "ai+heuristic"
        data.setdefault("reasons", base["reasons"])
        data.setdefault("missing", base["missing"])
        data.setdefault("eligibility", base["eligibility"])
        data["score"] = max(0, min(100, int(data.get("score", base["score"]))))
        return data
    except (ValueError, TypeError):
        return base


def generate_cover_letter(profile: dict, job: dict, prefs: dict) -> tuple[str, str]:
    """Return (letter, engine). Truthfulness enforced by prompt/rules."""
    name = profile.get("full_name") or "the candidate"
    title = job.get("title", "the advertised role")
    company = job.get("company", "your organization")
    skills = ", ".join((profile.get("skills") or [])[:8]) or "your listed skills"
    exp = profile.get("experience") or []
    first_exp = exp[0][:220] if exp else ""
    fallback = (
        f"Dear Hiring Team at {company},\n\n"
        f"I am writing to apply for {title}. I am {name}"
        + (f", {profile.get('headline')}." if profile.get("headline") else ".") + "\n\n"
        + (f"Relevant background: {first_exp}\n\n" if first_exp else "")
        + f"My skill set includes {skills}. I would welcome the opportunity to contribute to "
          f"{company} and am happy to provide further detail about my qualifications.\n\n"
        f"Kind regards,\n{name}\n{profile.get('email','')}  {profile.get('phone','')}"
    )
    if not ai_available():
        return fallback, "template"
    sys_prompt = ("You write concise, honest job cover letters. " + TRUTH_RULES +
                  " Do not exaggerate or add unverifiable claims. Max 280 words, plain text.")
    user_prompt = json.dumps({"candidate": {k: profile.get(k) for k in
                                            ("full_name", "headline", "summary", "skills",
                                             "experience", "education", "languages", "location")},
                              "job": {"title": title, "company": company,
                                      "description": job.get("description", "")[:3500],
                                      "location": job.get("location", "")}},
                             ensure_ascii=False, default=str)
    out = _chat(sys_prompt, user_prompt, max_tokens=800, temperature=0.5)
    return (out or fallback), ("ai" if out else "template-fallback")


def generate_tailored_cv_notes(profile: dict, job: dict) -> tuple[str, str]:
    desc = job.get("description", "")[:3000]
    matched, missing = _skill_hits(desc, profile.get("skills", []) or [])
    notes = [
        f"Lead with these proven skills that appear in this posting: {', '.join(matched) if matched else '(none directly matched)'}.",
        f"De-emphasize or move lower: skills not relevant to this posting.",
        f"Gaps to address honestly in the letter rather than the CV: {', '.join(missing) if missing else 'none detected'}.",
        "Keep all dates, employers and degrees exactly as verified in your profile — do not alter facts.",
    ]
    if ai_available():
        out = _chat("You adapt CV presentation order/wording for a job WITHOUT changing any facts. "
                    + TRUTH_RULES + " Give 5 short bullet instructions.",
                    json.dumps({"profile_skills": profile.get("skills"),
                                "experience": (profile.get("experience") or [])[:4],
                                "job_description": desc}, ensure_ascii=False, default=str),
                    max_tokens=500)
        if out:
            return out, "ai"
    return "\n".join("• " + n for n in notes), "heuristic"


def answer_application_questions(profile: dict, job: dict) -> list[dict]:
    """Prepare truthful answers to commonly asked application questions."""
    qs = []
    desc = job.get("description", "")
    if re.search(r"why|motivation|interest", desc, re.I):
        qs.append({"question": "Why are you interested in this position?",
                   "answer_source": "cover_letter"})
    if re.search(r"experience|years", desc, re.I):
        qs.append({"question": "Do you have relevant experience?",
                   "answer": (f"Yes — approximately {profile.get('years_experience', 0):.0f} years. "
                              + ((profile.get('experience') or [''])[0][:200]))})
    qs.append({"question": "Are you legally authorized to work in the job's location?",
               "answer": "UNKNOWN — requires the candidate's confirmation. The agent will not guess."})
    qs.append({"question": "Do you now or in the future require visa sponsorship?",
               "answer": "Depends on candidate's saved preference; confirm before submitting."})
    if ai_available():
        out = _chat("Draft short honest answers to standard job application questions using ONLY "
                    "the supplied profile facts. Mark anything not in the profile as 'TO CONFIRM'. "
                    "Return JSON array [{question, answer}].",
                    json.dumps({"profile": {k: profile.get(k) for k in
                                            ("full_name", "skills", "experience", "education",
                                             "languages", "years_experience")},
                                "job": {"title": job.get("title"),
                                        "description": desc[:2500]}}, ensure_ascii=False, default=str),
                    max_tokens=600)
        if out:
            try:
                arr = json.loads(re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip())
                if isinstance(arr, list) and arr:
                    return arr[:10]
            except ValueError:
                pass
    return qs
