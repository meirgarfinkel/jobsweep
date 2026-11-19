#!/usr/bin/env python3
"""
jobsweep — sweep company career pages (not aggregators) for relevant roles.

Two sources behind one interface:
  indexed  paid ATS index covering ~275k company career sites / 50+ ATS
  direct   your own slug list, hitting each company's public board API

Both return the same normalized Job records, so switching is a config change.

    pip install requests pyyaml
    export APIFY_TOKEN=...
    python jobsweep.py            # normal run
    python jobsweep.py --dry-run  # fetch + score, write nothing
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

import store

TIMEOUT = 45
UA = {"User-Agent": "jobsweep/1.0 (personal job search)"}


# ──────────────────────────────── model ────────────────────────────────


@dataclass
class Job:
    uid: str
    title: str
    company: str
    location: str
    url: str
    ats: str
    posted_at: str = ""
    description: str = ""
    arrangement: str = ""
    experience: str = ""
    salary: str = ""
    track: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)


def make_uid(company: str, title: str, url: str) -> str:
    """Stable across runs even if the vendor's own id churns."""
    basis = f"{company.lower().strip()}|{title.lower().strip()}|{url.strip()}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


# ─────────────────────────────── sources ───────────────────────────────


class IndexedSource:
    """Nationwide sweep via a hosted ATS index."""

    def __init__(self, cfg: dict):
        self.actor = cfg["actor"]
        # Either one `input` (legacy) or several named `queries` (tracks).
        q = cfg.get("queries")
        if q is None:
            q = [{"name": "default", "input": cfg.get("input", {})}]
        if not q:
            # An empty list would POST input={} — unbounded, and billed per row.
            sys.exit("source.indexed.queries is empty: nothing to sweep. "
                     "Remove the key to use the legacy single `input` block.")
        self.queries = q
        self.token = os.environ.get(cfg.get("token_env", "APIFY_TOKEN"), "")
        if not self.token:
            var = cfg.get("token_env", "APIFY_TOKEN")
            sys.exit(
                f"No API token found in ${var}.\n\n"
                f"  1. Create a free account at https://apify.com\n"
                f"  2. Settings -> API & Integrations -> copy your token\n"
                f"  3. macOS/Linux:  export {var}=apify_api_...\n"
                f"     Windows PS :  $env:{var} = \"apify_api_...\"\n\n"
                f"The free tier allows 500 runs/month; a daily two-track sweep uses ~60."
            )

    def fetch(self) -> list[Job]:
        # The token goes in a header, never the URL: requests embeds the full
        # URL in its exception text, so a timeout would print it to the console
        # and into any log or pasted traceback.
        url = (f"https://api.apify.com/v2/acts/{self.actor}"
               f"/run-sync-get-dataset-items")
        headers = {**UA, "Authorization": f"Bearer {self.token}"}
        out: list[Job] = []
        self.failed: list[str] = []
        for q in self.queries:
            name = q.get("name", "?")
            try:
                r = requests.post(url, json=q.get("input", {}), headers=headers,
                                  timeout=300)
            except requests.RequestException as e:
                print(f"  ! track {name}: request failed: {e}", file=sys.stderr)
                self.failed.append(name)
                continue
            if r.status_code >= 400:
                print(f"  ! track {name}: HTTP {r.status_code} {r.text[:200]}",
                      file=sys.stderr)
                self.failed.append(name)
                continue
            try:
                rows = r.json()
            except ValueError:
                # A 200 carrying HTML: proxy, captive portal, status page.
                print(f"  ! track {name}: response was not JSON "
                      f"({r.text[:120]!r})", file=sys.stderr)
                self.failed.append(name)
                continue
            if not isinstance(rows, list):
                print(f"  ! track {name}: unexpected shape", file=sys.stderr)
                self.failed.append(name)
                continue
            print(f"  track {name}: {len(rows)} rows")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    j = self._normalize(row)
                except Exception as e:
                    # One malformed row must not cost the whole paid run.
                    print(f"  ! track {name}: skipped a row ({e})",
                          file=sys.stderr)
                    continue
                j.track = name
                out.append(j)
        return out

    @staticmethod
    def _normalize(row: dict) -> Job:
        """Field names verified against a live run of the index."""
        title = str(row.get("title") or "")
        company = str(row.get("organization") or "")
        url = str(row.get("url") or "")

        locs = row.get("locations_derived") or row.get("locations") or []
        if isinstance(locs, str):
            locs = [locs]
        if not isinstance(locs, list):
            locs = []
        names = [str(x) for x in locs[:2] if x]
        location = "; ".join(names) if names else "—"

        # Salary values arrive as ints, floats, or strings like "150,000".
        lo, hi = row.get("ai_salary_min_value"), row.get("ai_salary_max_value")
        cur = row.get("ai_salary_currency") or ""
        salary = ""
        try:
            if lo and hi:
                lo_i = int(float(str(lo).replace(",", "")))
                hi_i = int(float(str(hi).replace(",", "")))
                salary = f"{lo_i:,}–{hi_i:,} {cur}".strip()
        except (TypeError, ValueError):
            salary = ""

        return Job(
            uid=make_uid(company, title, url),
            title=title,
            company=company,
            location=location,
            url=url,
            ats=str(row.get("source") or "indexed"),
            posted_at=str(row.get("date_posted") or ""),
            description=str(row.get("description_text") or "")[:4000],
            arrangement=str(row.get("ai_work_arrangement") or ""),
            experience=str(row.get("ai_experience_level") or ""),
            salary=salary,
        )


def parse_workday_url(career_url: str):
    """(tenant, pod, domain, locale, site) from a Workday careers URL, or None.

    Kept at module level so tests exercise this regex rather than a copy of it.
    """
    m = re.match(
        r"https://(?:www\.)?([^.]+)\.(wd\d+)\."
        r"(myworkdayjobs|myworkdaysite)\.com/"
        r"(?:([A-Za-z]{2}-[A-Za-z]{2})/)?([^/?#]+)",
        (career_url or "").strip(),
    )
    return m.groups() if m else None


def _workday_date(posted_on: str | None) -> str:
    """Workday's list endpoint has no date field — `postedOn` is display prose
    like "Posted Today" or "Posted 30+ Days Ago". Translate what we can into a
    real date and return "" rather than letting prose sit in a date column."""
    if not posted_on:
        return ""
    txt = str(posted_on).lower()
    today = datetime.now(timezone.utc).date()
    if "today" in txt:
        return today.isoformat()
    if "yesterday" in txt:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*\+?\s*day", txt)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*\+?\s*month", txt)
    if m:
        return (today - timedelta(days=30 * int(m.group(1)))).isoformat()
    return ""


def _strip_html(html: str, cap: int = 4000) -> str:
    """Plain text from a posting's HTML: tags removed, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip()[:cap]


def _workday_skip(job: "Job", known_described: set) -> bool:
    """True when this req is already stored with a description, matched the
    way main() matches stored rows: by company + title key."""
    return store.job_key(job.company, job.title) in known_described


def _workday_country_facet(facets: list, country: str):
    """(facetParameter, [ids]) selecting `country` in a Workday facets tree,
    or None. Tenants name things differently: some have a country value
    ("Israel"), others only offices ("Office - Israel - Tel Aviv"). An exact
    country value wins; otherwise every location value naming the country."""
    want = country.strip().lower()
    word = re.compile(rf"(?<!\w){re.escape(want)}(?!\w)")
    exact, partial = None, {}

    def walk(nodes):
        nonlocal exact
        for f in nodes:
            param = f.get("facetParameter")
            for v in f.get("values") or []:
                if "values" in v:       # a facet group, e.g. locationMainGroup
                    walk([v])
                    continue
                d = str(v.get("descriptor", "")).strip().lower()
                if not param or not v.get("id"):
                    continue
                if d == want and exact is None:
                    exact = (param, [v["id"]])
                elif word.search(d) and "location" in param.lower():
                    partial.setdefault(param, []).append(v["id"])
    walk(facets)
    if exact:
        return exact
    if partial:
        # The facet with the most matching values is the location list.
        param = max(partial, key=lambda p: len(partial[p]))
        return param, partial[param]
    return None


class DirectSource:
    """Free mode: hit each company's own public board endpoint."""

    # Boards that take many requests each; --light skips them.
    HEAVY = ("workday", "smartrecruiters")

    def __init__(self, cfg: dict, light: bool = False):
        self.cfg = cfg
        self.light = light
        # Workday tenants can hold thousands of reqs; stopping silently at a
        # fixed number makes a partial sweep look complete.
        self.max_per_board = int(cfg.get("max_per_board", 400))
        # board_locations: keep only jobs whose location names one of these
        # (whole-word, like locations_ok). Applies to the boards below, not
        # Comeet, which has comeet_countries. [] keeps everything.
        self.locs = [str(x) for x in (cfg.get("board_locations") or [])]

    def _boards(self) -> list[tuple[str, str, str]]:
        """(platform, id, company name) from the config lists and boards_file."""
        import boards
        out = [(p, str(x), "") for p in boards.PLATFORMS
               for x in (self.cfg.get(p) or [])]
        if self.cfg.get("boards_file"):
            out += boards.load(self.cfg["boards_file"])
        seen, uniq = set(), []
        for p, bid, name in out:
            if (p, bid.lower()) not in seen:
                seen.add((p, bid.lower()))
                uniq.append((p, bid, name))
        return uniq

    def fetch(self) -> list[Job]:
        self.failed: list[str] = []
        jobs: list[Job] = []
        readers = {"greenhouse": self._greenhouse, "lever": self._lever,
                   "ashby": self._ashby, "workday": self._workday,
                   "smartrecruiters": self._smartrecruiters}
        counts: dict[str, int] = {}
        for platform, bid, name in self._boards():
            if self.light and platform in self.HEAVY:
                continue
            self._name = name
            got = self._guard(readers[platform], bid)
            if self.locs:
                got = [j for j in got
                       if any(_has(k, (j.location or "").lower()) for k in self.locs)]
            counts[platform] = counts.get(platform, 0) + len(got)
            jobs += got
        if counts:
            print("  boards: " + ", ".join(f"{p} {n}" for p, n in counts.items())
                  + (" (light run: Workday/SmartRecruiters skipped)" if self.light else ""))
        jobs += self._comeet_all()
        return jobs

    def _comeet_all(self) -> list[Job]:
        """Comeet companies from `comeet` (list of "slug/uid") and/or
        `comeet_file` (tab-separated slug, uid). Optional `comeet_countries`
        keeps only those ISO codes, e.g. [IL]; `comeet_delay` is the pause
        between companies in seconds."""
        import comeet
        pairs = [tuple(x.split("/", 1)) for x in (self.cfg.get("comeet") or [])]
        if self.cfg.get("comeet_file"):
            pairs += comeet.load_companies(self.cfg["comeet_file"])
        limit = self.cfg.get("comeet_limit")
        if limit:
            pairs = pairs[:int(limit)]
        if not pairs:
            return []
        countries = {c.upper() for c in (self.cfg.get("comeet_countries") or [])}
        delay = float(self.cfg.get("comeet_delay", 1.0))
        out: list[Job] = []
        before = len(self.failed)
        self.comeet_inactive: list[str] = []
        for i, (slug, uid) in enumerate(pairs):
            if i:
                comeet.polite_pause(delay)
            rows = self._guard(self._comeet, f"{slug}/{uid}")
            for r in rows:
                if countries and r["country"] not in countries:
                    continue
                out.append(Job(
                    uid=make_uid(r["company"], r["title"], r["url"]),
                    title=r["title"], company=r["company"],
                    location=r["location"], url=r["url"], ats="comeet",
                    posted_at=r["posted_at"], description=r["description"],
                    arrangement=r["arrangement"], experience=r["experience"],
                    track="comeet",
                ))
        print(f"  comeet: {len(pairs)} companies, {len(out)} postings kept, "
              f"{len(self.comeet_inactive)} no longer on Comeet, "
              f"{len(self.failed) - before} failed")
        return out

    def _comeet(self, pair: str) -> list[dict]:
        import comeet
        slug, uid = pair.split("/", 1)
        try:
            return comeet.fetch_company(slug, uid,
                                        {**UA, "Accept": "text/html"}, TIMEOUT)
        except comeet.Inactive:
            self.comeet_inactive.append(pair)
            return []

    def _guard(self, fn, arg) -> list[Job]:
        try:
            return fn(arg)
        except Exception as e:  # one dead board must not kill the run
            print(f"  ! {fn.__name__} {arg}: {e}", file=sys.stderr)
            self.failed.append(f"{fn.__name__.lstrip('_')}:{arg}")
            return []

    def _greenhouse(self, slug: str) -> list[Job]:
        # content=true so descriptions arrive: without a body the scorer's
        # domain_exclusions check has nothing to read and cannot fire.
        u = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        r = requests.get(u, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for j in data.get("jobs", []):
            title = (j.get("title") or "").strip()
            url = j.get("absolute_url") or ""
            company = getattr(self, "_name", "") or j.get("company_name") or slug
            out.append(Job(
                uid=make_uid(company, title, url), title=title, company=company,
                location=((j.get("location") or {}).get("name") or "—"),
                url=url, ats="greenhouse",
                posted_at=j.get("first_published", "") or j.get("updated_at", ""),
                description=re.sub(r"<[^>]+>", " ",
                                   j.get("content") or "")[:4000],
            ))
        return out

    def _lever(self, slug: str) -> list[Job]:
        u = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        r = requests.get(u, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        out = []
        company = getattr(self, "_name", "") or slug
        for j in r.json():
            title, url = j.get("text", ""), j.get("hostedUrl", "")
            cats = j.get("categories") or {}
            ts = j.get("createdAt")
            out.append(Job(
                uid=make_uid(company, title, url), title=title, company=company,
                location=cats.get("location", "—"), url=url, ats="lever",
                posted_at=(
                    datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                    if ts else ""
                ),
                description=(j.get("descriptionPlain") or "")[:4000],
            ))
        return out

    def _ashby(self, slug: str) -> list[Job]:
        u = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        r = requests.get(u, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        company = getattr(self, "_name", "") or slug
        for j in data.get("jobs", []):
            # Ashby titles routinely carry leading whitespace.
            title = (j.get("title") or "").strip()
            url = j.get("jobUrl", "")
            remote = "Remote" if j.get("isRemote") else ""
            out.append(Job(
                uid=make_uid(company, title, url), title=title, company=company,
                location=j.get("location", "—"), url=url, ats="ashby",
                posted_at=j.get("publishedAt", ""),
                description=(j.get("descriptionPlain") or "")[:4000],
                arrangement=remote,
            ))
        return out

    def _smartrecruiters(self, company_id: str) -> list[Job]:
        """Public postings API. The list has no description, so each posting
        that passes board_locations gets one detail request."""
        base = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
        rows, offset = [], 0
        while offset < self.max_per_board:
            r = requests.get(base, params={"limit": 100, "offset": offset},
                             headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            page = data.get("content") or []
            rows += page
            offset += len(page)
            if not page or offset >= int(data.get("totalFound") or 0):
                break
        out = []
        for p in rows:
            loc = p.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("country")) if x) or "—"
            if self.locs and not any(_has(k, location.lower()) for k in self.locs):
                continue            # skip the detail request for far-away jobs
            company = (getattr(self, "_name", "")
                       or (p.get("company") or {}).get("name") or company_id)
            title = (p.get("name") or "").strip()
            url = f"https://jobs.smartrecruiters.com/{company_id}/{p.get('id')}"
            desc = ""
            try:
                d = requests.get(p["ref"], headers=UA, timeout=TIMEOUT).json()
                secs = ((d.get("jobAd") or {}).get("sections") or {})
                desc = " ".join(re.sub(r"<[^>]+>", " ", (s or {}).get("text") or "")
                                for s in secs.values())
                desc = re.sub(r"\s+", " ", desc).strip()[:4000]
                time.sleep(0.3)
            except Exception:
                pass                # keep the posting; scoring just has less
            arrangement = ("Remote" if loc.get("remote")
                           else "Hybrid" if loc.get("hybrid") else "On-site")
            level = ((p.get("experienceLevel") or {}).get("id") or "")
            out.append(Job(
                uid=make_uid(company, title, url), title=title, company=company,
                location=location, url=url, ats="smartrecruiters",
                posted_at=p.get("releasedDate") or "", description=desc,
                arrangement=arrangement,
                experience="0-2" if level in ("entry_level", "internship") else "",
            ))
        return out

    def _workday(self, career_url: str) -> list[Job]:
        """Workday's CxS endpoint. Needs a POST, so it never worked from a
        sandbox with an egress allowlist — test this one on a real machine."""
        parsed = parse_workday_url(career_url)
        if not parsed:
            raise ValueError(f"unparseable Workday URL: {career_url!r}")
        tenant, pod, domain, locale, site = parsed
        api = (f"https://{tenant}.{pod}.{domain}.com/wday/cxs/"
               f"{tenant}/{site}/jobs")
        # Keep the locale segment: tenants that use one 404 without it.
        prefix = f"https://{tenant}.{pod}.{domain}.com/"
        if locale:
            prefix += f"{locale}/"
        prefix += site

        headers = {**UA, "Content-Type": "application/json",
                   "Accept": "application/json"}
        company = getattr(self, "_name", "") or tenant
        # workday_country: ask Workday for that country's jobs only, using the
        # board's own location facet, instead of reading every req worldwide.
        facets, country = {}, (self.cfg.get("workday_country") or "").strip()

        def call(method, url, body=None, pause=1.0):
            # Workday answers 429 to quick bursts: pause between requests,
            # and wait and retry when it asks us to slow down.
            for attempt in range(4):
                time.sleep(pause if attempt == 0 else 10 * attempt)
                r = requests.request(method, url, json=body, headers=headers,
                                     timeout=TIMEOUT)
                if r.status_code != 429:
                    r.raise_for_status()
                    return r.json()
                wait = r.headers.get("Retry-After", "")
                if wait.isdigit():
                    time.sleep(min(int(wait), 60))
            r.raise_for_status()

        def post(body):
            return call("POST", api, body)

        if country:
            first = post({"limit": 1, "offset": 0, "searchText": "",
                          "appliedFacets": {}})
            hit = _workday_country_facet(first.get("facets") or [], country)
            if hit:
                facets = {hit[0]: hit[1]}
            else:
                print(f"  ! workday {tenant}: no '{country}' location filter; "
                      f"reading all reqs and filtering locally", file=sys.stderr)

        out, offset, page, total = [], 0, 20, None
        while offset < self.max_per_board:
            payload = post({"limit": page, "offset": offset, "searchText": "",
                            "appliedFacets": facets})
            if total is None:
                total = payload.get("total")
            posts = payload.get("jobPostings", [])
            if not posts:
                break
            for j in posts:
                title = (j.get("title") or "").strip()
                path = j.get("externalPath", "")
                loc = j.get("locationsText") or "—"
                # Multi-location reqs read "2 Locations"; name the country we
                # filtered on so location scoring still sees it.
                if facets and country.lower() not in loc.lower():
                    loc = f"{loc}, {country}"
                job = Job(
                    uid=make_uid(company, title, prefix + path), title=title,
                    company=company, location=loc,
                    url=prefix + path, ats="workday",
                    posted_at=_workday_date(j.get("postedOn")),
                )
                job._wd_path = path
                out.append(job)
            offset += page
            if len(posts) < page:
                break
        if total and len(out) < total:
            print(f"  ! workday {tenant}: returned {len(out)} of {total} "
                  f"postings (max_per_board={self.max_per_board})",
                  file=sys.stderr)

        # The list has no descriptions. Fetch each one once: for jobs already
        # stored with a description, reuse that text instead of a request,
        # so this run scores them on it too (the years cap needs it).
        base = f"https://{tenant}.{pod}.{domain}.com/wday/cxs/{tenant}/{site}"
        skip = getattr(self, "known_described", None) or {}
        fetched = failed = 0
        for job in out:
            path = getattr(job, "_wd_path", "")
            if _workday_skip(job, skip):
                job.description = skip[store.job_key(job.company, job.title)]
                continue
            if not path:
                continue
            try:
                info = (call("GET", base + path, pause=0.5)
                        or {}).get("jobPostingInfo") or {}
                # 8000, the store's limit: Workday puts requirements late,
                # often past 4000 characters of company introduction.
                job.description = _strip_html(info.get("jobDescription") or "",
                                              cap=8000)
                start = str(info.get("startDate") or "")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and not job.posted_at:
                    job.posted_at = start
                fetched += 1
            except Exception:
                failed += 1     # keep the posting; scoring just has less
        if fetched or failed:
            print(f"  workday {tenant}: fetched {fetched} descriptions"
                  + (f", {failed} failed" if failed else ""))
        return out


class CombinedSource:
    """Runs several sources in one sweep and pools their results."""

    def __init__(self, sources: list):
        self.sources = sources

    def fetch(self) -> list[Job]:
        out: list[Job] = []
        self.failed: list[str] = []
        for s in self.sources:
            out += s.fetch()
            self.failed += getattr(s, "failed", [])
        return out


def make_source(cfg: dict, light: bool = False):
    src = cfg.get("source") or {}
    mode = src.get("mode")
    if isinstance(mode, list):
        # e.g. [indexed, direct]: the paid index plus your own boards.
        return CombinedSource([make_source({"source": {**src, "mode": m}}, light)
                               for m in mode])
    if mode not in ("indexed", "direct"):
        sys.exit(f"source.mode must be 'indexed' or 'direct', got {mode!r}")
    block = src.get(mode)
    if not block:
        sys.exit(f"source.mode is {mode!r} but there is no source.{mode} "
                 f"block in the config.")
    return IndexedSource(block) if mode == "indexed" else DirectSource(block, light)


# ─────────────────────────────── scoring ───────────────────────────────


# Populated by score_job so a sweep can report what its own filters removed.
EXCLUDED_BY: dict[str, int] = {}
EXCLUDED_SAMPLE: dict[str, list[str]] = {}


@functools.lru_cache(maxsize=512)
def _pattern(term) -> "re.Pattern | None":
    """Compiled matcher for one term, or None when the term is unusable."""
    # YAML reads a bare number as an int; "2025" in a term list must not
    # crash the sweep on the first posting.
    term = str(term).strip().lower()
    if not term:
        return None
    if term.endswith("*"):
        stem = term[:-1].strip()
        if not stem or "*" in stem:
            return None
        return re.compile(rf"(?<!\w){re.escape(stem)}")
    if "*" in term:
        return None
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")


def _has(term: str, text: str) -> bool:
    """Whole-word containment, with an opt-in trailing-"*" prefix match.

    Plain substring matching silently destroys good roles: "intern" is inside
    "International", so an exclude list containing it drops
    "Director, International SEO". A trailing "*" opts back in to prefix
    matching ("engineer*" also matches "engineering"). Terms are lowercased,
    because the text they are matched against always is.
    """
    pat = _pattern(term)
    return bool(pat and pat.search(text))


def reset_exclusions() -> None:
    """Clear the per-run exclusion counters.

    They are module-level, so a second sweep in one process would otherwise
    report the first sweep's numbers too.
    """
    EXCLUDED_BY.clear()
    EXCLUDED_SAMPLE.clear()


from extract import years_required  # noqa: E402


def score_job(job: Job, s: dict) -> Job:
    t = (job.title or "").lower()
    # A posting can arrive with location null; keep one safe string and use it
    # everywhere below rather than slicing job.location directly.
    loc_text = job.location or "—"
    loc = loc_text.lower()
    pts, why = 0, []

    for bad in s.get("exclude", []):
        bad = str(bad)
        if bad.strip().endswith("*"):
            # Prefix matching in an exclude list is how "intern" came to eat
            # "International SEO". Refuse it rather than silently dropping
            # good roles; put the wildcard in a positive list instead.
            print(f"  ! ignoring exclude term {bad!r}: '*' is not allowed in "
                  f"`exclude` because it silently drops near-matches",
                  file=sys.stderr)
            continue
        if _has(bad, t):
            job.score, job.reasons = -99, [f"excluded: {bad}"]
            EXCLUDED_BY[bad] = EXCLUDED_BY.get(bad, 0) + 1
            sample = EXCLUDED_SAMPLE.setdefault(bad, [])
            if len(sample) < 3:        # only the first is ever displayed
                sample.append(job.title)
            return job

    # scoring.max_years_required: drop roles whose description asks for more
    # years than this. Postings that state no number are kept.
    cap = s.get("max_years_required")
    if cap is not None:
        need = years_required(job.description)
        if need is not None and need > int(cap):
            key = f"over {cap} years"
            job.score, job.reasons = -99, [f"excluded: asks {need}+ years"]
            EXCLUDED_BY[key] = EXCLUDED_BY.get(key, 0) + 1
            sample = EXCLUDED_SAMPLE.setdefault(key, [])
            if len(sample) < 3:
                sample.append(job.title)
            return job

    # Core terms stand on their own. Conditional terms (genai, geo, llm,
    # marketing engineer) are domain-ambiguous: the same word means drug
    # discovery in pharma and chip marketing in semiconductors, so they only
    # count as strong when nothing marks the role as another industry.
    core_terms = s.get("title_strong_core") or s.get("title_strong", [])
    core = [k for k in core_terms if _has(k, t)]
    cond = [k for k in s.get("title_strong_conditional", []) if _has(k, t)]
    if core:
        pts += 3
        why.append(f"core match: {', '.join(core[:3])}")
    elif cond:
        blob_all = f"{t} {job.description.lower()}"
        off = [d for d in s.get("domain_exclusions", []) if _has(d, blob_all)]
        if off:
            why.append(f"{cond[0]} but {off[0]} domain")
        else:
            pts += 3
            why.append(f"core match: {', '.join(cond[:3])}")

    scored_strong = pts > 0
    weak = [k for k in s.get("title_weak", []) if _has(k, t)]
    if weak:
        pts += 1
        if not scored_strong:
            why.append(f"adjacent: {weak[0]}")

    if any(_has(k, t) for k in s.get("seniority", [])):
        pts += 1
        why.append("right level")

    # Work arrangement: the index classifies this, so trust it over the string.
    arr = (job.arrangement or "").lower()
    if "remote" in arr:
        pts += 2
        why.append(job.arrangement)
    elif arr in ("on-site", "hybrid"):
        if any(_has(k, loc) for k in s.get("locations_ok", [])):
            pts += 1
            why.append(f"{job.arrangement}, reachable")
        else:
            # Waive the relocation penalty when the role is senior enough or
            # pays enough to be worth the conversation.
            top_title = any(_has(k, t) for k in s.get("waive_location_titles", []))
            big_money = False
            m = re.findall(r"[\d,]{4,}", job.salary or "")
            if m:
                try:
                    big_money = max(int(x.replace(",", "")) for x in m) >= int(
                        s.get("waive_location_salary", 150000))
                except ValueError:
                    pass
            if top_title or big_money:
                why.append(f"{job.arrangement} {loc_text[:28]} — "
                           f"{'senior' if top_title else 'comp'} waiver")
            else:
                pts -= 2
                why.append(f"{job.arrangement} in {loc_text[:36]}")
    elif any(_has(k, loc) for k in s.get("locations_ok", [])):
        pts += 1
        why.append("location works")

    # scoring.preferred_locations / preferred_bonus: rank one region above
    # the others, whatever the work arrangement. Only for title matches, so
    # location alone never lifts an unrelated role over the threshold.
    pref = s.get("preferred_locations") or []
    if pref and scored_strong and any(_has(k, loc) for k in pref):
        pts += int(s.get("preferred_bonus", 2))
        why.append("preferred location")

    # Experience band, as classified by the index.
    if job.experience in ("10+", "5-10"):
        pts += 1
        why.append(f"{job.experience} yrs")
    elif job.experience in ("0-2",) and int(s.get("junior_penalty", 3)):
        # scoring.junior_penalty: 0 keeps junior roles on an equal footing.
        pts -= int(s.get("junior_penalty", 3))
        why.append("too junior")

    # Optional +1 when the posting mentions whatever differentiates you.
    # Configured, not hardcoded — see scoring.bonus_terms.
    bonus = s.get("bonus_terms") or {}
    terms = bonus.get("terms") or []
    if terms:
        blob = f"{t} {job.description.lower()}"
        if any(_has(k, blob) for k in terms):
            pts += 1
            why.append(bonus.get("label", "bonus"))

    job.score, job.reasons = pts, why
    return job


# ──────────────────────────────── digest ───────────────────────────────


def _digest_entries(jobs: list[Job]) -> list[str]:
    lines: list[str] = []
    for j in jobs:
        lines += [
            f"## {j.title}",
            f"**{j.company}** · {j.location} · `{j.ats}` · score {j.score}"
            + (f" · {j.track}" if j.track else "")
            + (f" · {j.salary}" if j.salary else ""),
            "",
            (f"Posted {j.posted_at[:10]}. " if j.posted_at else "")
            + ("; ".join(j.reasons) if j.reasons else ""),
            "",
            f"[Apply]({j.url})" if j.url else "_no link_",
            "",
            "---",
            "",
        ]
    return lines


def write_digest(jobs: list[Job], outdir: Path, swept: int) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = outdir / f"{today}.md"

    # A later run on the same day can find postings the morning run did not.
    # Those are added to the existing digest; rewriting it from scratch threw
    # the morning's entries away.
    if path.exists() and not jobs:
        # main() already guards this; guard here too so no caller can replace
        # a real digest with "nothing new".
        return path
    if jobs and path.exists():
        prior = path.read_text(encoding="utf-8")
        m = re.search(r"\*\*(\d+) new\*\*", prior)
        if m:
            n = int(m.group(1)) + len(jobs)
            prior = prior[:m.start()] + f"**{n} new**" + prior[m.end():]
            stamp = datetime.now().strftime("%H:%M")
            extra = [f"_Added by a later run at {stamp}:_", ""]
            path.write_text(prior.rstrip("\n") + "\n\n"
                            + "\n".join(extra + _digest_entries(jobs)),
                            encoding="utf-8")
            return path
        # Otherwise the existing file says nothing cleared the bar — replace it.

    lines = [f"# New roles — {today}", ""]
    if not jobs:
        lines += [f"Nothing new cleared the bar. Swept {swept} postings.", ""]
    else:
        lines += [f"**{len(jobs)} new** out of {swept} postings swept.", ""]
        lines += _digest_entries(jobs)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_evaluate(cfg: dict, db: str, limit: int) -> None:
    """Fit-score cleared postings with an LLM, caching every result."""
    import evaluate

    key = evaluate.api_key_or_none()
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY to use --evaluate.")

    prof_path = Path(cfg.get("profile_file", "profile.local.md"))
    if not prof_path.exists():
        sys.exit(f"Write your background to {prof_path} first — the fit score "
                 f"is only as good as the profile it compares against.")
    profile = prof_path.read_text(encoding="utf-8")

    with store.connect(db) as conn:
        rows = store.unscored(conn, cfg["scoring"]["threshold"], limit)
    print(f"{len(rows)} postings to assess")

    done = 0
    for r in rows:
        fit = evaluate.score_row(r, profile, key)
        if not fit:
            continue
        done += 1
        # Each assessment is a paid API call, so commit it immediately. Holding
        # them all in one transaction meant a Ctrl-C after ten results had
        # printed discarded all ten and they would be billed again.
        with store.connect(db) as conn:
            store.save_fit(conn, r["uid"], fit)
        print(f"  {fit['fit_score']:>3} {fit['recommendation']:<9}"
              f"{r['title'][:44]} — {r['company'][:22]}")

    # A bad key fails every row identically. Exiting 0 there would read as
    # "nothing needed scoring" rather than "the key is wrong".
    if rows and not done:
        sys.exit(f"All {len(rows)} assessments failed — see the errors "
                 f"above. Check ANTHROPIC_API_KEY and your network.")


def run_explain(cfg: dict, db: str) -> None:
    """Audit every term against the postings already swept.

    Editing term lists is the main way to break this tool, and a term that
    matches nothing — or silently eats everything — is otherwise invisible.
    Run this after any edit to config.yaml or config.local.yaml.
    """
    sc = cfg["scoring"]
    with store.connect(db) as conn:
        rows = store.corpus(conn)
    if not rows:
        sys.exit("No postings stored yet — run a sweep first, then --explain.")
    titles = [(r["title"] or "").lower() for r in rows]
    bodies = [f"{(r['title'] or '').lower()} {(r['description'] or '').lower()}"
              for r in rows]
    locs = [(r["location"] or "").lower() for r in rows]
    n = len(rows)
    print(f"Auditing term lists against {n} stored postings.\n")

    lists = [("title_strong_core", titles, "scores +3 on its own", True),
             ("title_strong_conditional", titles,
              "scores +3 unless a domain word appears", True),
             ("title_weak", titles, "scores +1", True),
             ("seniority", titles, "scores +1", True),
             ("exclude", titles, "DROPS the posting entirely", False),
             ("waive_location_titles", titles,
              "waives the relocation penalty", False),
             ("domain_exclusions", bodies, "demotes a conditional match", False),
             ("locations_ok", locs, "counts as commutable", False)]
    # score_job falls back to the legacy `title_strong` key, and separately
    # scores bonus_terms — a typo in either was invisible to the audit.
    if not sc.get("title_strong_core") and sc.get("title_strong"):
        lists.insert(0, ("title_strong", titles, "scores +3 on its own", True))
    bonus = (sc.get("bonus_terms") or {}).get("terms") or []
    if bonus:
        lists.append(("bonus_terms.terms", bodies,
                      f"scores +1 ({(sc.get('bonus_terms') or {}).get('label','bonus')})",
                      True))
    warned = []
    for name, corpus, effect, expect_hits in lists:
        terms = ((sc.get("bonus_terms") or {}).get("terms")
                 if name == "bonus_terms.terms" else sc.get(name)) or []
        if not terms:
            continue
        print(f"{name}  ({effect})")
        for term in terms:
            pat = _pattern(str(term))
            hits = ([i for i, text in enumerate(corpus) if pat.search(text)]
                    if pat else [])
            pct = 100 * len(hits) // n if n else 0
            flag = ""
            if not hits and expect_hits:
                flag = "  <- no match in this sample"
                warned.append((name, term, "never matched"))
            elif name == "exclude" and str(term).strip().endswith("*"):
                flag = "  <- '*' is ignored in exclude (drops near-matches)"
                warned.append((name, term, "'*' not allowed in exclude"))
            elif name == "exclude" and pct > 25:
                flag = "  <- dropping a lot, check it"
                warned.append((name, term, f"drops {pct}% of postings"))
            elif pct > 60 and expect_hits:
                flag = "  <- matches almost everything"
                warned.append((name, term, f"matches {pct}%"))
            eg = f'  e.g. "{rows[hits[0]]["title"][:42]}"' if hits else ""
            print(f"  {str(term):<28} {len(hits):>4} ({pct:>2}%){eg}{flag}")
        print()

    if warned:
        print("Worth a look:")
        for name, term, why in warned:
            print(f"  {name}.{term}: {why}")
        print(f"\nThis is {n} postings, so a term that never matched may simply\n"
              f"not have come up yet. If it should have: check for a typo, a\n"
              f"plural, or a word needing a '*' suffix — \"engineer*\" also\n"
              f"matches \"engineering\", while plain \"engineer\" does not.")
    else:
        print("Nothing looks wrong: every search term matched something, and\n"
              "no exclusion is removing an unusual share of postings.")


def run_harvest(cfg: dict, jobs: list[Job]) -> None:
    """Grow boards.tsv (and the Comeet list) from index postings, so the
    free readers cover those companies from the next run on. Only postings
    in board_locations count, so the list stays local to your search."""
    direct = (cfg.get("source") or {}).get("direct") or {}
    path = direct.get("boards_file")
    if not path:
        return
    import boards
    locs = [str(x) for x in (direct.get("board_locations") or [])]
    # Index postings carry the query's track name; free readers leave it
    # empty or "comeet", and their boards are already known.
    queries = ((cfg.get("source") or {}).get("indexed") or {}).get("queries") or []
    tracks = {q.get("name") for q in queries} | {"default"}

    def keep(j):
        return j.track in tracks and (
            not locs or any(_has(k, (j.location or "").lower()) for k in locs))
    try:
        added = boards.harvest(jobs, keep, path, direct.get("comeet_file"))
    except Exception as e:      # a failed harvest must not cost the sweep
        print(f"  ! harvest failed: {e}", file=sys.stderr)
        return
    if added:
        print(f"  harvest: {len(added)} new board(s) for the free readers: "
              + "; ".join(added[:8]) + (" …" if len(added) > 8 else ""))


def run_profile_from_cv(cfg: dict) -> Path:
    """Draft the background Markdown from the CV; never overwrite an existing one."""
    import applicant
    import llm
    ac = cfg.get("apply") or {}
    here = Path(__file__).parent
    profile = applicant.load(here / (ac.get("applicant_file") or "applicant.local.yaml"))
    cv = profile["cv_path"]
    prompt = (
        f"Read the CV PDF at {cv} and write a Markdown background for the applicant. "
        "Include: roles with dates, skills, education, languages, notable projects. "
        "Use a Markdown heading for each section. "
        "Facts only, taken from the CV; invent nothing. Reply with only the Markdown.")
    timeout = max(int(ac.get("timeout") or 120), 300)
    # The CLI runs from an empty temp dir, so it may read only the CV's folder.
    text = llm.ask(prompt, ac.get("backend") or "claude_cli", timeout=timeout,
                   tools="Read", pdf_path=cv, add_dirs=[str(Path(cv).parent)])
    if not any(line.lstrip().startswith("#") for line in (text or "").splitlines()):
        # A refusal ("I can't read that file") is not a background; don't save it.
        raise llm.LLMError("Claude didn't return a Markdown background; nothing saved. "
                           f"Its reply began: {(text or '').strip()[:200]!r}")
    out = here / (ac.get("profile_file") or "profile.local.md")
    if out.exists():
        out = out.with_name(out.name + ".new")
        print(f"{out.with_name(out.name[:-4])} already exists; writing {out} instead.")
    out.write_text(text.strip() + "\n", encoding="utf-8")
    return out


def run_rescore(s: dict, db: str) -> None:
    """Score stored postings again after the rules change. Only the score and
    reasons columns are written."""
    reset_exclusions()
    with store.connect(db) as conn:
        rows = list(conn.execute("SELECT * FROM jobs"))
        changed = 0
        for r in rows:
            job = Job(uid=r["uid"], title=r["title"], company=r["company"],
                      location=r["location"] or "", url=r["url"] or "",
                      ats=r["ats"] or "", description=r["description"] or "",
                      arrangement=r["arrangement"] or "",
                      experience=r["experience"] or "",
                      salary=r["salary"] or "")
            score_job(job, s)
            reasons = json.dumps(job.reasons)
            if job.score != r["score"] or reasons != (r["reasons"] or "[]"):
                conn.execute("UPDATE jobs SET score=?, reasons=? WHERE uid=?",
                             (job.score, reasons, job.uid))
                changed += 1
        conn.commit()
    print(f"Rescored {len(rows)} stored postings; {changed} changed.")
    if EXCLUDED_BY:
        print("  now excluded: " + ", ".join(
            f"{k} x{v}" for k, v in sorted(EXCLUDED_BY.items(),
                                           key=lambda kv: -kv[1])))


def deep_merge(base: dict, over: dict) -> dict:
    """Recursively merge `over` into `base`. Non-dict values are replaced."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ───────────────────────────────── main ────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and score, but write neither the database nor "
                         "the digest")
    ap.add_argument("--local", help="override file merged over --config "
                                    "(default: config.local.yaml if present)")
    ap.add_argument("--serve", action="store_true",
                    help="open the local review board instead of sweeping")
    ap.add_argument("--port", type=int, default=8765, help="port for --serve")
    ap.add_argument("--light", action="store_true",
                    help="skip boards that take many requests each "
                         "(Workday, SmartRecruiters) for a quick extra run")
    ap.add_argument("--harvest", action="store_true",
                    help="add the boards behind stored index postings to "
                         "boards.tsv, then exit")
    ap.add_argument("--source", choices=("indexed", "direct"),
                    help="sweep only this source this run, overriding "
                         "source.mode (e.g. direct = your own boards, free)")
    ap.add_argument("--evaluate", action="store_true",
                    help="LLM fit-score the postings that cleared the keyword "
                         "bar (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--explain", action="store_true",
                    help="audit your term lists against the postings already "
                         "swept; run this after editing any term list")
    ap.add_argument("--rescore", action="store_true",
                    help="re-apply the current scoring rules to postings "
                         "already stored; statuses and notes are untouched")
    ap.add_argument("--profile-from-cv", action="store_true",
                    help="draft profile.local.md (background for Apply for me) from your CV")
    ap.add_argument("--evaluate-limit", type=int, default=15,
                    help="max postings to fit-score in one pass")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    # config.yaml is the shareable template. Anything personal — the cities
    # you can commute to, your comp floor — goes in config.local.yaml, which
    # merges on top and is never meant to be shared or committed.
    # Resolve the overlay next to the config file, NOT the working directory:
    # a cron entry or a run from another folder would otherwise silently fall
    # back to the shipped example config and score against the wrong field.
    cfg_path = Path(args.config)
    local = Path(args.local) if args.local else (
        cfg_path.parent / (cfg_path.stem + ".local.yaml"))
    if local.exists():
        overlay = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        cfg = deep_merge(cfg, overlay)
        print(f"merged overrides from {local}")

    if args.source:
        cfg["source"] = {**cfg["source"], "mode": args.source}

    db = (cfg.get("output") or {}).get("db", "jobsweep.sqlite3")

    if args.harvest:
        # Seed boards.tsv from every index posting already stored.
        with store.connect(db) as conn:
            rows = list(conn.execute("SELECT * FROM jobs"))
        jobs = [Job(uid=r["uid"], title=r["title"], company=r["company"],
                    location=r["location"] or "", url=r["url"] or "",
                    ats=r["ats"] or "", track=r["track"] or "") for r in rows]
        run_harvest(cfg, jobs)
        return

    if args.profile_from_cv:
        import applicant
        import llm
        try:
            out = run_profile_from_cv(cfg)
        except (applicant.ProfileError, llm.LLMError) as e:
            print(e)
            sys.exit(1)
        print(f"Wrote {out}")
        print("Read it and correct anything wrong; fills use it as written.")
        return

    if args.serve:
        import dashboard
        dashboard.serve(db, args.port,
                        threshold=(cfg.get("scoring") or {}).get("threshold"),
                        max_age_days=(cfg.get("board") or {}).get("max_age_days"),
                        apply_cfg=cfg.get("apply"))
        return

    s = cfg["scoring"]

    if args.explain:
        run_explain(cfg, db)
        return

    if args.evaluate:
        run_evaluate(cfg, db, args.evaluate_limit)
        return

    if args.rescore:
        run_rescore(s, db)
        return

    source = make_source(cfg, light=args.light)
    # Workday descriptions cost a request each; skip reqs already stored
    # with one.
    directs = [x for x in getattr(source, "sources", [source])
               if isinstance(x, DirectSource)]
    if directs and Path(db).exists():
        with store.connect(db) as conn:
            described = store.keys_with_description(conn)
        for d in directs:
            d.known_described = described
    print(f"Sweeping via {cfg['source']['mode']} source…")
    raw = source.fetch()
    swept_total = len(raw)      # before filtering/dedupe, for the digest header
    print(f"  {swept_total} postings returned")
    if not args.dry_run:
        run_harvest(cfg, raw)

    # A failed sweep and a quiet day both return zero rows. Without this, an
    # expired token on a schedule reports "0 new" every morning forever.
    failed = getattr(source, "failed", [])
    if failed and not raw:
        sys.exit(f"Every track failed ({', '.join(failed)}) — no results are "
                 f"not the same as no new jobs. Check the token and network.")
    if failed:
        print(f"  ! {len(failed)} track(s) failed: {', '.join(failed)}",
              file=sys.stderr)

    # The source mix is the evidence for the premise, so print it every run.
    mix = {}
    for j in raw:
        mix[j.ats or "?"] = mix.get(j.ats or "?", 0) + 1
    if mix:
        top = sorted(mix.items(), key=lambda kv: -kv[1])
        print("  sources: " + ", ".join(f"{k} {v}" for k, v in top[:8])
              + (f" (+{len(top) - 8} more)" if len(top) > 8 else ""))

    # Same req posted under several locations arrives as several rows.
    by_role, deduped = {}, []
    for j in raw:
        key = (j.company.lower().strip(), j.title.lower().strip())
        if key in by_role:
            # Same req under a different location — keep the extra location,
            # but only when it actually differs.
            prior = by_role[key]
            # location can be null from Lever/Ashby; the first row of a
            # duplicated req arriving that way must not crash the sweep.
            parts = [x.strip() for x in (prior.location or "").split(" / ")]
            if j.location and j.location not in parts:
                prior.location = (f"{prior.location} / {j.location}"
                                  if prior.location else j.location)
            continue
        by_role[key] = j
        deduped.append(j)
    if len(deduped) < len(raw):
        print(f"  {len(raw) - len(deduped)} merged as duplicate reqs")
    raw = deduped

    reset_exclusions()
    for j in raw:
        score_job(j, s)

    with store.connect(db) as conn:
        known = store.known_uids(conn)
        # The same req can come back under a different link (the index one
        # day, the company's board the next). Reuse the stored row's uid when
        # company and title match, so it updates that card instead of
        # appearing twice.
        by_key = store.uids_by_key(conn)
        for j in raw:
            if j.uid not in known:
                prior = by_key.get(store.job_key(j.company, j.title))
                if prior:
                    j.uid = prior
        fresh = [j for j in raw if j.uid not in known]
        print(f"  {len(fresh)} not seen before")
        if not args.dry_run:
            for j in raw:
                store.upsert(conn, j)

    scored = sorted((j for j in fresh if j.score >= s["threshold"]),
                    key=lambda j: -j.score)
    print(f"  {len(scored)} cleared threshold {s['threshold']}")

    # Show what the exclude list removed. A term quietly eating good roles is
    # the single easiest way to break this tool, and it is otherwise invisible.
    if EXCLUDED_BY:
        parts = ", ".join(f"{k} x{v}" for k, v in
                          sorted(EXCLUDED_BY.items(), key=lambda kv: -kv[1]))
        print(f"  excluded by your local `exclude` list "
              f"(all {len(raw)} swept, not just new): {parts}")
        for term, titles in sorted(EXCLUDED_SAMPLE.items(),
                                   key=lambda kv: -len(kv[1]))[:3]:
            print(f"    {term!r} dropped e.g. {titles[0][:58]!r}")

    if args.dry_run:
        for j in scored[:20]:
            print(f"    [{j.score}] {j.title} — {j.company} ({j.location})")
        print("\nDry run: nothing written.")
        return

    # A second run on the same day that finds nothing must not replace the
    # morning's digest with "nothing new cleared the bar". One that finds
    # something appends to it (see write_digest).
    digest_dir = Path(cfg["output"].get("digest_dir", "digests"))
    today_file = digest_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    if not scored and today_file.exists():
        print(f"\nNothing new — kept {today_file} as it was.")
        return
    digest = write_digest(scored, digest_dir, swept_total)
    print(f"\nDigest: {digest}")


if __name__ == "__main__":
    main()
