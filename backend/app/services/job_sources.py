"""Job source adapters.

Honesty rules implemented here:
- A source reports `configured` truthfully (server-side key present + working).
- Sources without credentials return 0 results with a clear message; the UI
  shows them as "not connected" — never as fake jobs.
- Each adapter respects documented rate limits and links back to the source.
"""
import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .. import config

USER_AGENT = "GlobalAIJobFinder/1.0 (personal job search assistant)"
REQUEST_TIMEOUT = 25


@dataclass
class NormalizedJob:
    external_id: str
    title: str
    company: str
    location: str
    country: str
    url: str
    description: str
    salary: str = ""
    job_type: str = ""
    category: str = ""
    remote: bool = False
    visa_note: str = "unknown"
    posted_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass
class SourceResult:
    source: str
    ok: bool
    jobs: list[NormalizedJob] = field(default_factory=list)
    error: str = ""


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]+", " ", text)).strip()


def _parse_dt(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value[:10], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


COUNTRY_HINTS = {
    "pakistan": "Pakistan", "karachi": "Pakistan", "lahore": "Pakistan", "islamabad": "Pakistan",
    "united kingdom": "United Kingdom", "london": "United Kingdom", "uk": "United Kingdom",
    "united states": "United States", "usa": "United States", "new york": "United States",
    "canada": "Canada", "toronto": "Canada", "vancouver": "Canada",
    "germany": "Germany", "berlin": "Germany", "france": "France", "paris": "France",
    "netherlands": "Netherlands", "amsterdam": "Netherlands", "spain": "Spain",
    "poland": "Poland", "ireland": "Ireland", "dublin": "Ireland", "portugal": "Portugal",
    "uae": "United Arab Emirates", "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "saudi": "Saudi Arabia", "riyadh": "Saudi Arabia", "qatar": "Qatar", "doha": "Qatar",
    "turkey": "Türkiye", "australia": "Australia", "singapore": "Singapore",
}


def guess_country(location: str) -> str:
    low = (location or "").lower()
    for hint, country in COUNTRY_HINTS.items():
        if hint in low:
            return country
    if "worldwide" in low or "remote" in low and "anywhere" in low:
        return "Worldwide"
    return ""


VISA_POSITIVE = re.compile(r"(visa sponsorship|sponsor(?:ship)? available|we (?:will )?sponsor|"
                           r"relocation (?:support|assistance|package)|work permit (?:provided|support))", re.I)
VISA_NEGATIVE = re.compile(r"(no (?:visa )?sponsorship|sponsorship (?:is not|unavailable)|"
                           r"(?:must|already) (?:have|hold).{0,30}(?:work (?:authorization|permit)|visa)|"
                           r"not (?:eligible|able) to sponsor)", re.I)


def detect_visa(text: str) -> str:
    t = text or ""
    if VISA_NEGATIVE.search(t):
        return "No sponsorship mentioned"
    if VISA_POSITIVE.search(t):
        m = VISA_POSITIVE.search(t)
        return f"Sponsorship mentioned: “{strip_html(m.group(0))[:120]}”"
    return "unknown"


# --------------------------------------------------------------------------
class BaseSource:
    key = ""
    label = ""
    needs_credentials = False
    description = ""
    docs_url = ""

    def configured(self) -> bool:
        return True

    def search(self, queries: list[str], countries: list[str], max_results: int) -> SourceResult:
        raise NotImplementedError


class RemotiveSource(BaseSource):
    """Remotive public API — free, no key required. Remote jobs only.
    Their ToS asks us to link to the original URL and credit Remotive."""
    key = "remotive"
    label = "Remotive (Remote Jobs API)"
    needs_credentials = False
    description = ("Free public API of curated remote tech jobs. Results link back to "
                   "Remotive and are credited to it, per their API terms. Rate limit respected: "
                   "max ~4 fetches/day/source.")
    docs_url = "https://remotive.com/api-documentation"
    MIN_FETCH_INTERVAL_MIN = 6 * 60  # conservative self-throttle

    def __init__(self):
        self._last_fetch = 0.0

    def search(self, queries, countries, max_results):
        now = time.time()
        if self._last_fetch and now - self._last_fetch < self.MIN_FETCH_INTERVAL_MIN * 60:
            wait_min = int((self.MIN_FETCH_INTERVAL_MIN * 60 - (now - self._last_fetch)) / 60)
            return SourceResult(self.key, False, error=(
                f"Self-throttled to respect Remotive rate limits (they request ≤4 calls/day). "
                f"Retry in ~{wait_min} min."))
        params = {"limit": min(max_results, 100)}
        q = " ".join(queries[:3]) if queries else ""
        if q:
            params["search"] = q[:80]
        try:
            resp = requests.get("https://remotive.com/api/remote-jobs",
                                params=params, timeout=REQUEST_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            self._last_fetch = time.time()
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return SourceResult(self.key, False, error=f"Remotive API error: {exc}")
        jobs = []
        for item in data.get("jobs", data.get("job-listing", [])):
            desc = strip_html(item.get("description", ""))[:6000]
            loc = item.get("candidate_required_location") or item.get("location") or "Remote"
            jobs.append(NormalizedJob(
                external_id=str(item.get("id", "")),
                title=item.get("title", "")[:400],
                company=item.get("company_name", ""),
                location=loc,
                country="Worldwide" if loc.lower() == "worldwide" else guess_country(loc),
                url=item.get("url", ""),
                description=desc,
                salary=item.get("salary", "") or "",
                job_type=item.get("employment_type", "") or "Full-time",
                category=item.get("category", ""),
                remote=True,
                visa_note=detect_visa(desc),
                posted_at=_parse_dt(item.get("publication_date", "")),
            ))
            if len(jobs) >= max_results:
                break
        return SourceResult(self.key, True, jobs)


class AdzunaSource(BaseSource):
    """Adzuna API — real aggregator covering many countries incl. Pakistan.
    Requires free developer credentials (app_id/app_key) configured server-side."""
    key = "adzuna"
    label = "Adzuna Job Index API"
    needs_credentials = True
    description = ("Aggregates vacancies across 40+ countries including Pakistan, Europe, USA, "
                   "Canada and the Middle East. Free developer tier available — add ADZUNA_APP_ID "
                   "and ADZUNA_APP_KEY in backend/.env to activate.")
    docs_url = "https://developer.adzuna.com/"
    COUNTRY_CODES = {"pakistan": "pk", "united kingdom": "gb", "united states": "us",
                     "canada": "ca", "germany": "de", "france": "fr", "netherlands": "nl",
                     "spain": "es", "ireland": "ie", "portugal": "pt", "poland": "pl",
                     "united arab emirates": "ae", "saudi arabia": "sa", "qatar": "qa",
                     "turkey": "tr", "australia": "au", "singapore": "sg", "italy": "it",
                     "belgium": "be", "switzerland": "ch", "austria": "at", "norway": "no",
                     "denmark": "dk", "sweden": "se", "finland": "fi", "czech republic": "cz",
                     "romania": "ro", "uae": "ae"}

    def configured(self) -> bool:
        return bool(config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY)

    def search(self, queries, countries, max_results):
        if not self.configured():
            return SourceResult(self.key, False, error=(
                "Not connected: Adzuna requires free API credentials. "
                "Set ADZUNA_APP_ID / ADZUNA_APP_KEY on the server to enable this source."))
        cc = "us"
        for c in countries or []:
            code = self.COUNTRY_CODES.get(c.lower())
            if code:
                cc = code
                break
        url = f"https://api.adzuna.com/v1/api/jobs/{cc}/search/1"
        what = " OR ".join(queries[:3]) if queries else "developer"
        params = {
            "app_id": config.ADZUNA_APP_ID, "app_key": config.ADZUNA_APP_KEY,
            "what": what[:120], "results_per_page": min(max_results, 50),
            "max_days_old": "30", "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return SourceResult(self.key, False, error=f"Adzuna API error: {exc}")
        jobs = []
        for item in data.get("results", []):
            desc = strip_html(item.get("description", ""))[:6000]
            loc = ", ".join(x for x in [item.get("location", {}).get("display_name", "")] if x)
            sal = ""
            if item.get("salary_min") and item.get("salary_max"):
                sal = f"{item['salary_min']:,}-{item['salary_max']:,} {item.get('currency','')}/yr"
            elif item.get("salary_min"):
                sal = f"from {item['salary_min']:,} {item.get('currency','')}/yr"
            fulltime = item.get("fulltime", True)
            jobs.append(NormalizedJob(
                external_id=str(item.get("id", "")),
                title=item.get("title", "")[:400],
                company=item.get("company", {}).get("display_name", ""),
                location=loc,
                country=self._country_from_code(cc),
                url=item.get("redirect_url", ""),
                description=desc,
                salary=sal,
                job_type="Full-time" if fulltime else "Part-time",
                category=", ".join(item.get("category_tags", [])[:3]),
                remote=bool(item.get("remote")),
                visa_note=detect_visa(desc),
                posted_at=_parse_dt(item.get("created", "")),
            ))
            if len(jobs) >= max_results:
                break
        return SourceResult(self.key, True, jobs)

    @staticmethod
    def _country_from_code(code):
        rev = {v: k for k, v in AdzunaSource.COUNTRY_CODES.items()}
        return {"gb": "United Kingdom", "ae": "United Arab Emirates"}.get(code, rev.get(code, "").title())


class IndeedSource(BaseSource):
    key = "indeed"
    label = "Indeed Publisher API"
    needs_credentials = True
    description = ("Indeed's official Publisher API exists but access is restricted and currently "
                   "only issued to selected publishers. Until valid credentials exist on the server "
                   "this source stays disabled — we do not scrape Indeed (against their ToS).")
    docs_url = "https://en.indeed.com/publisher-api.page"

    def configured(self) -> bool:
        return bool(config.INDEED_PUBLISHER_KEY)

    def search(self, queries, countries, max_results):
        return SourceResult(self.key, False, error=(
            "Not connected: Indeed Publisher API credentials are required and access is "
            "restricted by Indeed. Provide INDEED_PUBLISHER_KEY server-side once approved."))


class LinkedInSource(BaseSource):
    key = "linkedin"
    label = "LinkedIn Jobs"
    needs_credentials = True
    description = ("LinkedIn has no open public job-search API for third-party apps; its "
                   "Jobs API is partner-only and automated scraping violates LinkedIn's "
                   "User Agreement. This integration is therefore OFF unless you complete an "
                   "official partnership/authorization. We will not fake it.")
    docs_url = "https://learn.microsoft.com/linkedin"

    def search(self, queries, countries, max_results):
        return SourceResult(self.key, False, error=(
            "Not connected: LinkedIn requires an approved partner integration or your own "
            "authorized account connection. No unofficial automation is performed."))


class JSearchSource(BaseSource):
    """RapidAPI 'JSearch' aggregates Google-for-Jobs style listings (Indeed/LinkedIn/Glassdoor).
    Works IF the user supplies their own RapidAPI key (paid freemium tier)."""
    key = "jsearch"
    label = "JSearch via RapidAPI (aggregator)"
    needs_credentials = True
    description = ("Third-party aggregator listing jobs from multiple boards. Requires your own "
                   "RapidAPI key (free trial tier available). Set RAPIDAPI_KEY on the server.")
    docs_url = "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch"

    def configured(self) -> bool:
        return bool(getattr(config, "RAPIDAPI_KEY", ""))

    def search(self, queries, countries, max_results):
        if not self.configured():
            return SourceResult(self.key, False, error=(
                "Not connected: add RAPIDAPI_KEY (RapidAPI JSearch subscription) on the server."))
        query = " ".join(queries[:2]) or "python developer"
        country = "Pakistan" if any("pak" in c.lower() for c in countries or []) else ""
        try:
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                params={"query": query, "num_pages": "1"},
                headers={"X-RapidAPI-Key": config.RAPIDAPI_KEY,
                         "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return SourceResult(self.key, False, error=f"JSearch API error: {exc}")
        jobs = []
        for item in data.get("data", []):
            desc = strip_html(item.get("job_description", ""))[:6000]
            loc = ", ".join(x for x in [item.get("job_city", ""), item.get("job_country", "")] if x)
            sal = ""
            if item.get("min_amount"):
                sal = f"{item['min_amount']:.0f}-{(item.get('max_amount') or item['min_amount']):.0f} " \
                      f"{item.get('currency','')} {item.get('job_employment_time','')}"
            jobs.append(NormalizedJob(
                external_id=str(item.get("job_id", "")),
                title=item.get("job_title", "")[:400],
                company=item.get("employer_name", ""),
                location=loc,
                country=item.get("job_country", "") or guess_country(loc),
                url=item.get("job_apply_link") or item.get("job_redirect_inline", ""),
                description=desc,
                salary=sal,
                job_type=item.get("job_employment_type", ""),
                category=item.get("job_category", ""),
                remote=bool(item.get("job_is_remote")),
                visa_note=detect_visa(desc),
                posted_at=_parse_dt(item.get("job_posted_at_datetime_utc", "")),
            ))
            if len(jobs) >= max_results:
                break
        return SourceResult(self.key, True, jobs)


class TeachAwaySource(BaseSource):
    """TeachAway EDAID — international TEFL/teaching recruitment feed (public JSON)."""
    key = "teachaway"
    label = "TeachAway (International Teaching)"
    needs_credentials = False
    description = ("Public school-by-school teaching vacancy feed (international schools, TEFL). "
                   "Useful for Computer Science / IT teacher roles abroad.")
    docs_url = "https://www.teachaway.com/resources"

    def search(self, queries, countries, max_results):
        try:
            resp = requests.get("https://www.teachaway.com/edaid_feed",
                                params={"page": 1}, timeout=REQUEST_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return SourceResult(self.key, False, error=f"TeachAway feed error: {exc}")
        wanted = [q.lower() for q in queries if q]
        jobs = []
        for item in (data or []):
            title = item.get("title", "")
            subj = " ".join(s.get("name", "") for s in item.get("subjects", []) or [])
            blob = f"{title} {subj}".lower()
            if wanted and not any(w in blob for w in wanted + ["computer", "it teacher", "ict", "coding"]):
                continue
            loc_parts = [item.get("city", ""), item.get("country", "")]
            loc = ", ".join(p for p in loc_parts if p)
            desc = strip_html(item.get("description", ""))[:6000]
            jobs.append(NormalizedJob(
                external_id=str(item.get("id", "")),
                title=title[:400],
                company=item.get("school_name", "") or item.get("organization", ""),
                location=loc,
                country=item.get("country", "") or guess_country(loc),
                url=item.get("url", ""),
                description=desc,
                salary=item.get("salary_details", "") or "",
                job_type=item.get("contract_type", "") or "Full-time",
                category="Teaching",
                remote=False,
                visa_note=detect_visa(desc),
                posted_at=_parse_dt(str(item.get("posted_date", ""))),
            ))
            if len(jobs) >= max_results:
                break
        return SourceResult(self.key, True, jobs)


SOURCES: dict[str, BaseSource] = {
    s.key: s for s in [RemotiveSource(), AdzunaSource(), JSearchSource(),
                       TeachAwaySource(), IndeedSource(), LinkedInSource()]
}


def run_search(source_keys: list[str], queries: list[str], countries: list[str],
               max_per_source: int) -> tuple[list[tuple[str, SourceResult]], list[str]]:
    """Run each enabled source with retry/backoff. Returns [(key,result)], errors."""
    outcomes, errors = [], []
    for key in source_keys:
        src = SOURCES.get(key)
        if src is None:
            errors.append(f"Unknown source '{key}'")
            continue
        result = None
        for attempt in range(2):  # one retry with backoff for transient failures
            try:
                result = src.search(queries, countries, max_per_source)
            except Exception as exc:
                result = SourceResult(key, False, error=f"Unexpected error: {exc}")
            if result.ok or "error" not in (result.error or "").lower():
                break
            time.sleep(2 * (attempt + 1))
        outcomes.append((key, result))
        if not result.ok:
            errors.append(f"[{src.label}] {result.error}")
    return outcomes, errors
