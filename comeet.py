"""Comeet reader for jobsweep's direct mode.

A Comeet company page (www.comeet.com/jobs/<slug>/<uid>) embeds its open
positions as JSON in `COMPANY_POSITIONS_DATA = [...]`. We read that, so no
API token is involved.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

PAGE = "https://www.comeet.com/jobs/{slug}/{uid}"
_MARKER = re.compile(r"COMPANY_POSITIONS_DATA\s*=\s*")
_COMPANY = re.compile(r"COMPANY_DATA\s*=\s*")
_TAGS = re.compile(r"<[^>]+>")

# Comeet's experience levels, mapped onto the bands score_job() understands.
_EXPERIENCE = {
    "internship": "0-2", "entry-level": "0-2", "entry level": "0-2",
    "senior": "5-10", "director": "10+", "executive": "10+",
}


def load_companies(path: str | Path) -> list[tuple[str, str]]:
    """(slug, uid) pairs from a tab-separated file. Blank lines and # skipped."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\t/ ]+", line)
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def _embedded(html: str, pattern: re.Pattern):
    m = pattern.search(html)
    if not m:
        return None
    obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    return obj


class Inactive(Exception):
    """The page is Comeet's generic shell: the company has left Comeet."""


def parse_page(html: str, slug: str) -> list[dict]:
    """The page's positions as plain dicts in jobsweep Job field names."""
    company_data = _embedded(html, _COMPANY)
    positions = _embedded(html, _MARKER)
    if company_data is None and positions is None:
        raise Inactive(slug)
    if positions is None:
        raise ValueError("company page has no COMPANY_POSITIONS_DATA")
    company_data = company_data or {}
    company_name = company_data.get("name") or slug

    out = []
    for p in positions:
        if not isinstance(p, dict) or p.get("is_internal"):
            continue
        loc = p.get("location") or {}
        location = loc.get("name") or ", ".join(
            x for x in (loc.get("city"), loc.get("country")) if x) or "—"
        arrangement = p.get("workplace_type") or (
            "Remote" if loc.get("is_remote") else "")
        details = (p.get("custom_fields") or {}).get("details") or []
        description = " ".join(
            _TAGS.sub(" ", d.get("value") or "") for d in details
            if isinstance(d, dict))
        description = re.sub(r"\s+", " ", description).strip()[:4000]
        out.append({
            "title": (p.get("name") or "").strip(),
            "company": p.get("company_name") or company_name,
            "location": location,
            "country": (loc.get("country") or "").upper(),
            "url": p.get("url_comeet_hosted_page") or p.get("url_active_page")
                   or PAGE.format(slug=slug, uid=""),
            "posted_at": p.get("time_updated") or "",
            "description": description,
            "arrangement": arrangement,
            "experience": _EXPERIENCE.get(
                (p.get("experience_level") or "").strip().lower(), ""),
        })
    return out


def fetch_company(slug: str, uid: str, headers: dict, timeout: int) -> list[dict]:
    r = requests.get(PAGE.format(slug=slug, uid=uid), headers=headers,
                     timeout=timeout)
    r.raise_for_status()
    return parse_page(r.text, slug)


def polite_pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
