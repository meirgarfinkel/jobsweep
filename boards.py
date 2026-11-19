"""The growing list of company boards that direct mode reads for free.

Every posting the paid index returns carries a link. When that link reveals
the company's board on a platform jobsweep can read itself (Greenhouse,
Lever, Ashby, Workday, SmartRecruiters, Comeet), the board is added here, so
from then on every open job there is read on each run, at no cost.

boards.tsv columns: platform, board id, company, date first seen.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "smartrecruiters")
_UA = {"User-Agent": "jobsweep/1.0 (personal job search)"}

_ASHBY = re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", re.I)
_LEVER = re.compile(r"https?://jobs\.(?:eu\.)?lever\.co/([^/?#]+)", re.I)
_GH = re.compile(r"https?://(?:job-)?boards(?:\.eu)?\.greenhouse\.io/"
                 r"(?:embed/job_app\?for=)?([^/?#&]+)", re.I)
_SR = re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)", re.I)
_COMEET = re.compile(r"https?://(?:www\.)?comeet\.com/jobs/([^/?#]+)/"
                     r"([0-9A-Fa-f]{2}\.[0-9A-Fa-f]{3})", re.I)
_WORKDAY = re.compile(r"(https://[^./]+\.wd\d+\.myworkday(?:jobs|site)\.com/"
                      r"(?:[a-z]{2}-[A-Z]{2}/)?[^/?#]+)")


def load(path: str | Path) -> list[tuple[str, str, str]]:
    """(platform, board id, company) rows from boards.tsv; [] if missing."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] in PLATFORMS:
            out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return out


def _guess_greenhouse(company: str, job_id: str) -> str | None:
    """A Greenhouse job on the company's own site (…?gh_jid=123) hides the
    board name. Try names built from the company name and keep the one whose
    board actually lists that job."""
    base = re.sub(r"\b(inc|ltd|llc|corp|corporation|technologies|software)\b\.?",
                  "", company.lower())
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return None
    for cand in dict.fromkeys(["".join(words), words[0], "-".join(words)]):
        try:
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{cand}/jobs",
                             headers=_UA, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code == 200 and any(
                str(j.get("id")) == job_id for j in r.json().get("jobs", [])):
            return cand
    return None


def board_of(url: str, ats: str, company: str):
    """('platform', id) for a posting link, ('comeet', 'slug/uid'), or None."""
    url = url or ""
    ats = (ats or "").lower()
    if m := _COMEET.search(url):
        return "comeet", f"{m.group(1)}/{m.group(2)}"
    if m := _ASHBY.search(url):
        return "ashby", m.group(1)
    if m := _LEVER.search(url):
        return "lever", m.group(1)
    if m := _SR.search(url):
        return "smartrecruiters", m.group(1)
    if m := _WORKDAY.search(url):
        return "workday", m.group(1)
    if m := _GH.search(url):
        if m.group(1) not in ("embed", "v1"):
            return "greenhouse", m.group(1)
    if "greenhouse" in ats:
        jid = (parse_qs(urlsplit(url).query).get("gh_jid") or [""])[0]
        if jid and (slug := _guess_greenhouse(company, jid)):
            return "greenhouse", slug
    return None


def harvest(jobs, keep, boards_path: str | Path,
            comeet_path: str | Path | None = None) -> list[str]:
    """Add the boards behind `jobs` (only those for which keep(job) is true)
    to boards.tsv, and Comeet companies to the Comeet list. Returns a line per
    new board, for the run's output."""
    rows = load(boards_path)
    known = {(p, i.lower()) for p, i, _ in rows}
    # Companies already listed are skipped before any lookup, so a Greenhouse
    # company on its own domain isn't re-guessed on every run.
    known_names = {c.lower() for _, _, c in rows if c}
    comeet_known = set()
    if comeet_path and Path(comeet_path).exists():
        import comeet
        comeet_known = {uid.upper() for _, uid in comeet.load_companies(comeet_path)}

    new_boards, new_comeet, added, tried = [], [], [], set()
    for j in jobs:
        if not keep(j):
            continue
        key = (j.ats, j.company.lower())
        if key in tried:            # one lookup per company and platform
            continue
        tried.add(key)
        if j.ats != "comeet" and j.company.lower() in known_names:
            continue
        found = board_of(j.url, j.ats, j.company)
        if not found:
            continue
        platform, bid = found
        if platform == "comeet":
            uid = bid.split("/", 1)[1].upper()
            if comeet_path and uid not in comeet_known:
                comeet_known.add(uid)
                new_comeet.append(bid.replace("/", "\t"))
                added.append(f"comeet {bid} ({j.company})")
            continue
        if (platform, bid.lower()) in known:
            continue
        known.add((platform, bid.lower()))
        new_boards.append(f"{platform}\t{bid}\t{j.company}\t{date.today().isoformat()}")
        added.append(f"{platform} {bid} ({j.company})")

    if new_boards:
        p = Path(boards_path)
        header = "" if p.exists() else (
            "# platform\tboard\tcompany\tfirst seen  (added by jobsweep harvest)\n")
        with p.open("a", encoding="utf-8") as f:
            f.write(header + "".join(line + "\n" for line in new_boards))
    if new_comeet:
        with Path(comeet_path).open("a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in new_comeet))
    return added
