"""Local review board: `python jobsweep.py --serve`.

A single-page view of everything swept, with the status and notes you set
written straight back to the SQLite store. Standard library only — no Node,
no framework, no build step.
"""

from __future__ import annotations

import html
import json
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import applicant
import extract
import fill
import llm
import store

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jobsweep</title>
<style>
  :root {{
    --bg:#f7f7f5; --card:#fff; --ink:#1a1a18; --muted:#6b6b66;
    --line:#e3e3df; --accent:#2f6f4f; --warn:#8a5a1a; --bad:#8a2a2a;
    --company:#1a1a18; --fresh:#1f7a3f; --stale:#8a6a00;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#17181a; --card:#1f2124; --ink:#e9e9e6; --muted:#9a9a94;
      --line:#2e3034; --accent:#6fbf8f; --warn:#d9a44a; --bad:#e06a6a;
      --company:#f3ecd8; --fresh:#6fd08c; --stale:#e5c04a;
    }}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
  header{{padding:20px 16px 8px;max-width:1100px;margin:0 auto}}
  h1{{margin:0 0 2px;font-size:19px;letter-spacing:-.01em}}
  .sub{{color:var(--muted);font-size:13px}}
  .bar{{display:flex;gap:6px;flex-wrap:wrap;max-width:1100px;margin:14px auto 0;
        padding:0 16px}}
  .chip{{border:1px solid var(--line);background:var(--card);color:var(--ink);
         border-radius:999px;padding:5px 11px;font-size:12.5px;cursor:pointer;
         text-decoration:none;display:inline-block}}
  .chip.on{{background:var(--ink);color:var(--bg);border-color:var(--ink)}}
  main{{max-width:1100px;margin:0 auto;padding:14px 16px 56px}}
  .job{{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:13px 15px;margin-bottom:9px}}
  .top{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}}
  .t{{font-weight:600;font-size:15px}}
  .t a{{color:inherit;text-decoration:none}} .t a:hover{{text-decoration:underline}}
  .applyme{{margin-left:8px;font-size:12px;padding:1px 8px;cursor:pointer}}
  .meta{{color:var(--muted);font-size:12.5px;margin-top:3px}}
  .co{{color:var(--company);font-weight:700}}
  .fresh{{color:var(--fresh)}} .stale{{color:var(--stale)}}
  .facts{{display:flex;gap:5px;flex-wrap:wrap;align-items:center;flex:1}}
  .tag{{font-size:11.5px;padding:2px 7px;border-radius:5px;
        background:var(--bg);border:1px solid var(--line);color:var(--ink)}}
  .yrs{{color:var(--stale);border-color:currentColor;font-weight:600}}
  .none{{color:var(--muted);font-size:12px;font-style:italic}}
  .why{{font-size:12.5px;color:var(--muted);margin-top:6px;font-style:italic}}
  .fit{{margin-left:auto;display:flex;gap:7px;align-items:center;flex-shrink:0}}
  .pill{{font-size:11.5px;padding:2px 8px;border-radius:999px;
         border:1px solid var(--line);white-space:nowrap}}
  .apply{{color:var(--accent);border-color:currentColor}}
  .consider{{color:var(--warn);border-color:currentColor}}
  .skip{{color:var(--bad);border-color:currentColor}}
  .ctl{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
  select,input[type=text]{{background:var(--bg);color:var(--ink);
    border:1px solid var(--line);border-radius:7px;padding:6px 9px;font:inherit;
    font-size:13px}}
  input[type=text]{{flex:1;min-width:180px}}
  .saved{{color:var(--accent);font-size:12px;align-self:center;opacity:0}}
  .empty{{color:var(--muted);padding:30px 0;text-align:center}}
  .deets{{font-size:12.5px;color:var(--muted);margin-top:7px}}
  .deets ul{{margin:3px 0 0;padding-left:18px}}
  summary{{cursor:pointer}}
</style></head><body>
<header>
  <h1>jobsweep</h1>
  <div class="sub">{total} postings · {swept}</div>
</header>
<div class="bar">{chips}</div>
<main>{body}</main>
<script>
async function save(uid, el) {{
  const row = el.closest('.job');
  const status = row.querySelector('select').value;
  const flag   = row.querySelector('.saved');
  const r = await fetch('/api/status', {{
    method:'POST', headers:{{'content-type':'application/json'}},
    body: JSON.stringify({{uid, status}})
  }});
  flag.textContent = r.ok ? 'saved' : 'failed';
  flag.style.opacity = 1;
  setTimeout(()=>{{flag.style.opacity=0}}, 1400);
}}

// Hand the job to the Firefox extension if it is installed; otherwise open
// the form in a new tab and let the extension pick it up from the hash.
// The extension's board.js marks <html> as soon as it runs; without the mark
// the board says so instead of failing silently.
const hasExt = () => document.documentElement.hasAttribute('data-jobsweep-ext');
function extWarning() {{
  if (hasExt() || document.getElementById('extwarn')) return;
  const d = document.createElement('div');
  d.id = 'extwarn';
  d.textContent = 'The jobsweep extension is not active on this page, so Apply for me ' +
    'can only open the job, not link it for filling. Load the extension, then reload this page.';
  d.style.cssText = 'background:#7a4b00;color:#fff;padding:8px 12px;font-size:13px';
  document.body.prepend(d);
}}
window.addEventListener('load', () => setTimeout(extWarning, 500));

document.addEventListener('click', (e) => {{
  const b = e.target.closest('.applyme');
  if (!b) return;
  const uid = b.dataset.uid, url = b.dataset.url;
  if (!hasExt()) {{
    extWarning();
    window.open(url + '#jobsweep=' + uid, '_blank', 'noopener');
    return;
  }}
  let opened = false;
  const on = (m) => {{ if (m.origin === location.origin && m.data && m.data.jobsweep === 'opened') opened = true; }};
  window.addEventListener('message', on);
  window.postMessage({{jobsweep: 'open', uid, url}}, location.origin);
  setTimeout(() => {{
    window.removeEventListener('message', on);
    if (!opened) window.open(url + '#jobsweep=' + uid, '_blank', 'noopener');
  }}, 300);
}});

// Same wording as the server's _ago(), computed against the viewer's clock.
function plural(n, unit) {{ return n + ' ' + unit + (n === 1 ? '' : 's'); }}
function ago(ts, hasTime) {{
  const secs = (Date.now() - ts) / 1000;
  if (!hasTime) {{
    const days = Math.floor(secs / 86400);
    return days <= 0 ? 'today' : plural(days, 'day') + ' ago';
  }}
  if (secs < 3600) {{
    const mins = Math.max(Math.floor(secs / 60), 0);
    return mins < 1 ? 'just now' : plural(mins, 'minute') + ' ago';
  }}
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  if (!days) return plural(hours, 'hour') + ' ago';
  return hours ? plural(days, 'day') + ' and ' + plural(hours, 'hour') + ' ago'
               : plural(days, 'day') + ' ago';
}}
// Cards still marked "new" disappear once older than this (0 = never), so
// a tab left open keeps matching what a reload would show.
const MAX_AGE_MS = {max_age_ms};
function tick() {{
  const now = Date.now();
  for (const el of document.querySelectorAll('.ago[data-ts]')) {{
    const ts = Number(el.dataset.ts);
    el.textContent = el.dataset.label + ' ' + ago(ts, el.dataset.hastime === '1');
    const fresh = now - ts < 86400000;
    el.classList.toggle('fresh', fresh);
    el.classList.toggle('stale', !fresh);
    if (MAX_AGE_MS) {{
      const card = el.closest('.job');
      const isNew = card.querySelector('select').value === 'new';
      card.style.display = (isNew && now - ts > MAX_AGE_MS) ? 'none' : '';
    }}
  }}
}}
tick();
setInterval(tick, 60000);
// Browsers slow timers in background tabs; catch up when the tab is shown.
document.addEventListener('visibilitychange', () => {{ if (!document.hidden) tick(); }});
</script>
</body></html>"""


def _parse_time(s: str | None):
    """(aware datetime, has_time) from the mixed formats sources send, or
    (None, False). A naive timestamp is taken as UTC."""
    s = (s or "").strip()
    if not s:
        return None, False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt, len(s) > 10


def _posted_time(r: sqlite3.Row):
    dt, has_time = _parse_time(r["posted_at"])
    if dt is None:
        dt, has_time = _parse_time(r["first_seen"])
    return dt, has_time


def _ago(dt, has_time: bool) -> str:
    """'2 hours ago', '3 days and 4 hours ago'; date-only sources get days."""
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if not has_time:
        days = int(secs // 86400)
        return "today" if days <= 0 else (
            "1 day ago" if days == 1 else f"{days} days ago")
    if secs < 3600:
        mins = max(int(secs // 60), 0)
        return "just now" if mins < 1 else (
            f"{mins} minute{'s' if mins != 1 else ''} ago")
    days, rem = divmod(int(secs), 86400)
    hours = rem // 3600
    h = f"{hours} hour{'s' if hours != 1 else ''}"
    if not days:
        return f"{h} ago"
    d = f"{days} day{'s' if days != 1 else ''}"
    return f"{d} ago" if not hours else f"{d} and {h} ago"


_FACTS: dict = {}


def _facts(uid: str, desc: str):
    """(years required, tech stack), cached per posting for this server run.
    Keyed on the description's hash too, so a re-swept edit is picked up."""
    key = (uid, hash(desc))
    if key not in _FACTS:
        _FACTS[key] = (extract.years_required(desc), extract.tech_stack(desc))
    return _FACTS[key]


def apply_url(url: str, ats: str) -> str:
    """The page that holds the application form, where the ATS splits it off."""
    u = url or ""
    base = u.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if u.startswith("https://jobs.lever.co/") and not base.endswith("/apply"):
        return base + "/apply"
    if u.startswith("https://jobs.ashbyhq.com/") and not base.endswith("/application"):
        return base + "/application"
    return u


def extension_origin(origin, pinned=None) -> bool:
    """True for a moz-extension:// origin; with `pinned`, only that one."""
    if not origin or not origin.startswith("moz-extension://"):
        return False
    return not pinned or origin.rstrip("/") == str(pinned).rstrip("/")


def _job_html(r: sqlite3.Row) -> str:
    esc = html.escape
    try:
        reasons = ", ".join(json.loads(r["reasons"] or "[]"))
    except Exception:
        reasons = ""
    fit = ""
    if r["fit_score"] is not None:
        verdict = (r["fit_verdict"] or "consider").lower()
        fit = (f'<span class="pill {esc(verdict)}">{r["fit_score"]} · '
               f'{esc(verdict)}</span>')
    opts = "".join(
        f'<option value="{s}"{" selected" if r["status"] == s else ""}>{s}</option>'
        for s in store.STATUSES)
    title = esc(r["title"] or "—")
    # Only http(s) becomes a link: a javascript: URL from a hostile posting
    # would otherwise run in the page's origin when clicked.
    safe_url = r["url"] if (r["url"] or "").startswith(("http://", "https://")) else ""
    link = (f'<a href="{esc(safe_url)}" target="_blank" rel="noopener">{title}</a>'
            if safe_url else title)
    if safe_url:
        link += (f' <button class="applyme" data-uid="{esc(r["uid"])}" '
                 f'data-url="{esc(apply_url(safe_url, r["ats"] or ""))}">'
                 f'Apply for me</button>')
    years, stack = _facts(r["uid"], r["description"] or "")
    facts = ""
    if years is not None:
        facts += f'<span class="pill yrs">{years}+ yrs</span>'
    if stack:
        facts += "".join(f'<span class="tag">{esc(t)}</span>' for t in stack)
    if not facts:
        facts = '<span class="none">no stack or years stated</span>'
    facts = f'<span class="facts">{facts}</span>'

    dt, has_time = _posted_time(r)
    posted = ""
    if dt is not None:
        # Comeet only exposes when a posting was last edited.
        label = ("first seen" if not r["posted_at"]
                 else "updated" if r["ats"] == "comeet" else "posted")
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        tone = "fresh" if age < 86400 else "stale"
        # The server's text is the fallback; the page script recomputes it
        # from data-ts on load and every minute, so an open tab stays right.
        posted = (f' · <span class="ago {tone}" title="{esc(dt.isoformat())}"'
                  f' data-ts="{int(dt.timestamp() * 1000)}"'
                  f' data-hastime="{1 if has_time else 0}"'
                  f' data-label="{esc(label)}">{label} '
                  f'{esc(_ago(dt, has_time))}</span>')

    deets = ""
    if r["fit_json"]:
        try:
            d = json.loads(r["fit_json"])
            parts = []
            if d.get("one_liner"):
                parts.append(f'<div>{esc(str(d["one_liner"]))}</div>')
            for key in ("strengths", "gaps", "risks"):
                vals = [v for v in (d.get(key) or []) if v]
                if vals:
                    lis = "".join(f"<li>{esc(str(v))}</li>" for v in vals)
                    parts.append(f"<b>{key}</b><ul>{lis}</ul>")
            if parts:
                deets = ('<details class="deets"><summary>assessment</summary>'
                         + "".join(parts) + "</details>")
        except Exception:
            pass

    return f"""<div class="job">
  <div class="top"><span class="t">{link}</span>
    <span class="fit">{fit}<span class="pill">kw {r["score"] if r["score"] is not None else "—"}</span></span></div>
  <div class="meta"><span class="co">{esc(r["company"] or "")}</span> ·{esc(r["location"] or "—")}
    · <code>{esc(r["ats"] or "")}</code>{" · " + esc(r["salary"]) if r["salary"] else ""}
    {posted}</div>
  {f'<div class="why">{esc(reasons)}</div>' if reasons else ""}
  {deets}
  <div class="ctl">
    <select onchange="save('{r["uid"]}', this)">{opts}</select>
    {facts}
    <span class="saved"></span>
  </div>
</div>"""


def _newest_first(rows: list, threshold) -> list:
    """Postings that cleared the bar first, then the rest; newest first in
    each group, undated ones last."""
    floor = datetime.min.replace(tzinfo=timezone.utc)

    def key(r):
        cleared = threshold is None or (r["score"] or 0) >= threshold
        dt, _ = _posted_time(r)
        return (not cleared, -(dt or floor).timestamp())
    return sorted(rows, key=key)


def _too_old(r: sqlite3.Row, max_age_days) -> bool:
    """A card still marked "new" whose posting is older than the limit.
    Anything you've acted on (shortlist, applied, ...) is never hidden."""
    if not max_age_days or r["status"] != "new":
        return False
    dt, _ = _posted_time(r)
    if dt is None:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() > max_age_days * 86400


def render(db: str | Path, status: str | None, threshold=None,
           sort: str = "newest", max_age_days=None,
           show_old: bool = False) -> str:
    with store.connect(db) as conn:
        rows = store.all_jobs(conn, status)
        counts = store.counts(conn)
        total = sum(counts.values())
        active = total - counts.get("skipped", 0)
    hidden = 0
    if max_age_days and not show_old:
        kept = [r for r in rows if not _too_old(r, max_age_days)]
        hidden = len(rows) - len(kept)
        rows = kept
    if sort != "score":
        rows = _newest_first(rows, threshold)
    sq = "" if sort != "score" else "sort=score"
    oq = "old=1" if show_old else ""

    def href(st=None, so=None, old=None):
        q = [f"status={st}"] if st else []
        so = sq if so is None else so
        old = oq if old is None else old
        q += [x for x in (so, old) if x]
        return "/?" + "&".join(q) if q else "/"
    chips = [f'<a class="chip{"" if status else " on"}" href="{href()}">'
             f'all {active}</a>']
    for s in store.STATUSES:
        n = counts.get(s, 0)
        if n:
            chips.append(f'<a class="chip{" on" if status == s else ""}"'
                         f' href="{href(s)}">{s} {n}</a>')
    chips.append(f'<span class="chip">sort:</span>'
                 f'<a class="chip{" on" if sort != "score" else ""}" '
                 f'href="{href(status, "")}">newest</a>'
                 f'<a class="chip{" on" if sort == "score" else ""}" '
                 f'href="{href(status, "sort=score")}">best score</a>')
    if max_age_days:
        if show_old:
            chips.append(f'<a class="chip" href="{href(status, None, "")}">'
                         f'hide new cards older than {max_age_days} days</a>')
        elif hidden:
            chips.append(f'<a class="chip" href="{href(status, None, "old=1")}">'
                         f'{hidden} older than {max_age_days} days hidden · show</a>')
    body = ("".join(_job_html(r) for r in rows) if rows
            else '<div class="empty">Nothing here yet — run a sweep.</div>')
    max_ms = int(max_age_days * 86400000) if (max_age_days and not show_old) else 0
    return PAGE.format(total=active, swept=f"{len(rows)} shown",
                       chips="".join(chips), body=body, max_age_ms=max_ms)


def make_server(db: str | Path, port: int = 8765, threshold=None,
                max_age_days=None, apply_cfg: dict | None = None):
    cfg = apply_cfg or {}
    repo = Path(__file__).resolve().parent

    def _path(key, default):
        p = Path(cfg.get(key) or default).expanduser()
        return p if p.is_absolute() else repo / p

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def _host_ok(self):
            # A DNS-rebinding page reaches 127.0.0.1 under its own hostname;
            # refusing any other Host keeps it off every route.
            port = self.server.server_address[1]
            return self.headers.get("host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _origin(self):
            """The extension's origin to echo in CORS, "" for an extension
            request that came without Origin, or None to refuse."""
            o = self.headers.get("origin")
            if o is None:
                # Firefox may leave Origin off the background page's GET. A
                # page can't add this header cross-origin (the preflight is
                # refused), and the Host check stops same-origin rebinding.
                return "" if self.headers.get("x-jobsweep-extension") == "1" else None
            return o if extension_origin(o, cfg.get("extension_origin")) else None

        def _cors(self, origin):
            if not origin:
                return
            self.send_header("access-control-allow-origin", origin)
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type, x-jobsweep-extension")
            self.send_header("vary", "Origin")

        def _refused(self):
            if not self._host_ok():
                self.send_error(403)
                return True
            return False

        def _json(self, code, obj, origin):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self._cors(origin)
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if self._refused():
                return
            if self.path.split("?", 1)[0] not in ("/api/fill", "/api/cv"):
                self.send_error(404)
                return
            origin = self._origin()
            if not origin:
                self.send_error(403)
                return
            self.send_response(204)
            self._cors(origin)
            self.end_headers()

        def _cv(self, origin):
            try:
                prof = applicant.load(_path("applicant_file", "applicant.local.yaml"))
                data = Path(prof["cv_path"]).read_bytes()
            except (applicant.ProfileError, OSError, KeyError) as e:
                self._json(404, {"error": str(e)}, origin)
                return
            self.send_response(200)
            self.send_header("content-type", "application/pdf")
            self.send_header("content-length", str(len(data)))
            # Headers are latin-1; percent-encode so a Hebrew name survives.
            self.send_header("x-filename", quote(Path(prof["cv_path"]).name, safe=""))
            self._cors(origin)
            self.send_header("access-control-expose-headers", "x-filename")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self._refused():
                return
            route = self.path.split("?", 1)[0]
            if route == "/api/cv":
                origin = self._origin()
                if origin is None:
                    self.send_error(403)
                    return
                self._cv(origin)
                return
            # Only the board itself. Browsers also ask for /favicon.ico on
            # every load, and answering that with a full render doubled the
            # database reads per page view.
            if route != "/":
                self.send_error(404)
                return
            q = parse_qs(urlsplit(self.path).query)
            status = (q.get("status") or [None])[0]
            if status not in store.STATUSES:
                status = None
            sort = (q.get("sort") or ["newest"])[0]
            show_old = (q.get("old") or [""])[0] == "1"
            page = render(db, status, threshold, sort, max_age_days,
                          show_old).encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def _fill(self, origin):
            try:
                n = int(self.headers.get("content-length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                uid, fields = data["uid"], data.get("fields") or []
                if not isinstance(fields, list):
                    raise ValueError("fields must be a list")
                if not all(isinstance(f, dict) and isinstance(f.get("id"), str) for f in fields):
                    raise ValueError("each field must be an object with a string id")
            except Exception as e:
                self._json(400, {"error": f"Bad request: {e}"}, origin)
                return
            with store.connect(db) as conn:
                row = conn.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone()
            if row is None:
                self._json(404, {"error": "This job isn't in the database."}, origin)
                return
            notes = []
            try:
                profile = applicant.load(_path("applicant_file", "applicant.local.yaml"))
                try:
                    background = _path("profile_file", "profile.local.md").read_text(
                        encoding="utf-8")
                except OSError:
                    background = ""
                    notes.append("No profile.local.md yet: run python jobsweep.py --profile-from-cv.")
                try:
                    style = _path("style_file", "writing_style.md").read_text(encoding="utf-8")
                except OSError:
                    style = ""
                    notes.append("Writing style file not found; answers used no style rules.")
                res = fill.run(dict(row), profile, background, fields,
                               cfg.get("backend") or "claude_cli",
                               timeout=int(cfg.get("timeout") or 120), style=style)
            except (applicant.ProfileError, llm.LLMError) as e:
                self._json(200, {"error": str(e)}, origin)
                return
            if notes:
                res = dict(res, notes=list(res.get("notes") or []) + notes)
            self._json(200, res, origin)

        def do_POST(self):
            if self._refused():
                return
            if self.path == "/api/fill":
                origin = self._origin()
                if origin is None:
                    self.send_error(403)
                    return
                self._fill(origin)
                return
            if self.path != "/api/status":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("content-length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                with store.connect(db) as conn:
                    hit = store.set_status(conn, data["uid"], data["status"],
                                           data.get("notes"))
            except Exception as e:
                self.send_error(400, str(e))
                return
            if not hit:
                # Reporting "saved" for a row that does not exist would hide a
                # mismatched database entirely.
                self.send_error(404, "no such job in this database")
                return
            self.send_response(204)
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(db: str | Path, port: int = 8765, open_browser: bool = True,
          threshold=None, max_age_days=None, apply_cfg: dict | None = None) -> None:
    server = make_server(db, port, threshold, max_age_days, apply_cfg)
    # Read stack and years out of every description now, so the first page
    # load does not wait for it.
    threading.Thread(target=lambda: render(db, None, threshold),
                     daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Review board: {url}  (ctrl-c to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    server.serve_forever()
