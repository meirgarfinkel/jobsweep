#!/usr/bin/env python3
"""Build or refresh comeet_companies.tsv, the list of Comeet companies that
direct mode sweeps.

Comeet has no public directory, so this finds company pages in two places:

  1. Common Crawl's URL index (free, public): every www.comeet.com/jobs/...
     address seen in the most recent monthly archives.
  2. Optionally, a text file of extra Comeet URLs you collected yourself,
     e.g. from a web search for  site:comeet.com/jobs "Tel Aviv".

It then opens each company page once, keeps the ones still on Comeet, and
records how many open jobs each has (total, and in --country).

    python comeet_discover.py                      # 10 archives, then check
    python comeet_discover.py --archives 4         # quicker, fewer companies
    python comeet_discover.py --extra urls.txt     # add your own finds
    python comeet_discover.py --no-check           # skip the liveness pass

Common Crawl's index often answers 504 when busy; each archive is retried a
few times and skipped if it keeps failing. Run it again later to fill gaps:
existing entries in the output file are kept and merged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

import comeet

COLLINFO = "https://index.commoncrawl.org/collinfo.json"
UA = {"User-Agent": "jobsweep/1.0 (personal job search)"}
URL_RE = re.compile(r"https?://(?:www\.)?comeet\.com/jobs/([^/?#\s]+)/"
                    r"([0-9A-Fa-f]{2}\.[0-9A-Fa-f]{3})")


def crawl_ids(n: int) -> list[str]:
    r = requests.get(COLLINFO, headers=UA, timeout=60)
    r.raise_for_status()
    return [c["id"] for c in r.json()][:n]


def crawl_urls(crawl: str, tries: int = 3) -> list[str] | None:
    url = (f"https://index.commoncrawl.org/{crawl}-index"
           f"?url=www.comeet.com/jobs/*&output=json&fl=url")
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=200)
            if r.status_code == 200:
                return [json.loads(line)["url"]
                        for line in r.text.splitlines() if line.strip()]
            if r.status_code == 404:      # no captures in this archive
                return []
        except requests.RequestException:
            pass
        time.sleep(15 * (attempt + 1))
    return None


def add(found: dict, url: str) -> None:
    m = URL_RE.search(url)
    if m:
        found.setdefault(m.group(2).upper(), (m.group(1), m.group(2)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="comeet_companies.tsv")
    ap.add_argument("--archives", type=int, default=10,
                    help="how many recent Common Crawl archives to read")
    ap.add_argument("--extra", help="text file with more Comeet URLs")
    ap.add_argument("--country", default="IL",
                    help="ISO code whose job count is recorded (default IL)")
    ap.add_argument("--no-check", action="store_true",
                    help="skip opening each page (keeps dead companies)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between company pages in the check")
    args = ap.parse_args()

    found: dict[str, tuple[str, str]] = {}
    out = Path(args.out)
    if out.exists():
        for slug, uid in comeet.load_companies(out):
            found.setdefault(uid.upper(), (slug, uid))
        print(f"{len(found)} companies already in {out}")

    for crawl in crawl_ids(args.archives):
        urls = crawl_urls(crawl)
        before = len(found)
        for u in urls or []:
            add(found, u)
        status = "skipped (index busy)" if urls is None else f"{len(urls)} urls"
        print(f"  {crawl}: {status}, +{len(found) - before} companies")
        time.sleep(5)

    if args.extra:
        before = len(found)
        for line in Path(args.extra).read_text(encoding="utf-8").splitlines():
            add(found, line)
        print(f"  {args.extra}: +{len(found) - before} companies")

    rows = []
    pairs = sorted(found.values(), key=lambda p: p[0].lower())
    if args.no_check:
        rows = [(s, u, "", "") for s, u in pairs]
    else:
        print(f"Checking {len(pairs)} company pages "
              f"(~{len(pairs) * (args.delay + 0.5) / 60:.0f} min)…")
        gone = 0
        for i, (slug, uid) in enumerate(pairs, 1):
            try:
                jobs = comeet.fetch_company(slug, uid, {**UA, "Accept": "text/html"}, 45)
                local = sum(1 for j in jobs if j["country"] == args.country.upper())
                rows.append((slug, uid, len(jobs), local))
            except comeet.Inactive:
                gone += 1
            except Exception as e:          # keep it; may be a transient error
                print(f"  ! {slug}/{uid}: {e}", file=sys.stderr)
                rows.append((slug, uid, "", ""))
            if i % 50 == 0:
                print(f"  {i}/{len(pairs)} checked")
            time.sleep(args.delay)
        print(f"  {gone} no longer on Comeet, dropped")

    with out.open("w", encoding="utf-8") as f:
        f.write(f"# slug\tuid\tjobs_total\tjobs_{args.country.lower()}"
                f"\t(checked {date.today().isoformat()})\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"Wrote {len(rows)} companies to {out}")


if __name__ == "__main__":
    main()
