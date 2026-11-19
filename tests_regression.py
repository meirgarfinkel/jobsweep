"""Regression checks. Run: python tests_regression.py

No network: every check is local except the config-guard subprocesses,
which use a dummy token and never reach a real API.
"""
import importlib.util, json, os, pathlib, sqlite3, subprocess, sys, tempfile, yaml
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import store
spec=importlib.util.spec_from_file_location("js",str(pathlib.Path(__file__).parent/"jobsweep.py"))
js=importlib.util.module_from_spec(spec); sys.modules["js"]=js; spec.loader.exec_module(js)
# A committed fixture, so the suite runs on a fresh clone and asserts the
# CODE rather than whatever search terms the user happens to have configured.
HERE = pathlib.Path(__file__).parent
cfg = yaml.safe_load(open(HERE / "config.test.yaml"))
S = cfg["scoring"]
P=F=0
def check(name, cond, detail=""):
    global P,F
    if cond: P+=1; print(f"  PASS  {name}")
    else:    F+=1; print(f"  FAIL  {name}  {detail}")

def score(title, **kw):
    j=js.Job(uid="x",title=title,company="C",location=kw.pop("location","Remote"),
             url="",ats="t",**kw)
    js.score_job(j,S); return j

print("#1 word-boundary matching")
for t in ["Director, International SEO","Head of International Organic Search",
          "VP, Internal Communications"]:
    check(f'"{t}" not excluded', score(t, arrangement="Remote OK").score > 0)
check('"Geotechnical Engineer" not a geo match',
      "geo" not in " ".join(score("Geotechnical Engineer").reasons))
check('"Senior SEO Manager" still scores', score("Senior SEO Manager", arrangement="Remote OK").score >= 5)
check('"Marketing Intern" still excluded', score("Marketing Intern").score == -99)

print("\n#2/#19 malformed row fields")
for bad in [{"ai_salary_min_value":"150,000","ai_salary_max_value":"200,000"},
            {"ai_salary_min_value":"150000.0","ai_salary_max_value":"2e5"},
            {"ai_salary_min_value":"competitive","ai_salary_max_value":"lots"},
            {"locations_derived":{"city":"Austin"}},
            {"locations_derived":[None,None]}]:
    row={"title":"SEO Lead","organization":"X","url":"u",**bad}
    try:
        j=js.IndexedSource._normalize(row); ok=True; d=""
    except Exception as e: ok=False; d=f"{type(e).__name__}: {e}"
    check(f"normalize survives {list(bad)[0]}={list(bad.values())[0]!r}", ok, d)

print("\n#6 null nested location")
j=js.Job(uid="a",title="T",company="C",location=None,url="",ats="t",arrangement="On-site")
try: js.score_job(j,S); check("score_job tolerates location=None", True)
except Exception as e: check("score_job tolerates location=None", False, repr(e))

print("\n#13 Workday URL shapes")
for url,want in [("https://acme.wd1.myworkdayjobs.com/en-US/External","External"),
                 ("https://acme.wd1.myworkdayjobs.com/en-us/External","External"),
                 ("https://www.acme.wd1.myworkdayjobs.com/External","External"),
                 ("https://acme.wd1.myworkdaysite.com/en-US/External","External")]:
    g=js.parse_workday_url(url)          # the real parser, not a copy
    check(f"parses {url[:46]}", bool(g) and g[4]==want, f"got {g}")
check("rejects a non-Workday URL", js.parse_workday_url("https://example.com/x") is None)
import datetime as _dt
_today=_dt.datetime.now(_dt.timezone.utc).date()
check("'Posted Today' -> today's date",
      js._workday_date("Posted Today")==_today.isoformat(), js._workday_date("Posted Today"))
check("'Posted 3 Days Ago' -> exactly three days back",
      js._workday_date("Posted 3 Days Ago")==(_today-_dt.timedelta(days=3)).isoformat(),
      js._workday_date("Posted 3 Days Ago"))
check("'Posted Yesterday' differs from today",
      js._workday_date("Posted Yesterday")!=js._workday_date("Posted Today"))
check("unknown prose -> empty", js._workday_date("whenever")=="")

print("\n#16/#20 store behaviour")
db=tempfile.mktemp(suffix=".sqlite3")
with store.connect(db) as c:
    j=js.Job(uid="u1",title="T",company="C",location="L",url="",ats="t",description="")
    store.upsert(c,j)
    j2=js.Job(uid="u1",title="T",company="C",location="L",url="",ats="t",
              description="full body text", arrangement="Remote OK")
    store.upsert(c,j2)
    row=list(c.execute("SELECT description,arrangement FROM jobs WHERE uid='u1'"))[0]
    check("re-sweep fills a previously empty description", row[0]=="full body text", repr(row[0]))
    check("re-sweep fills arrangement", row[1]=="Remote OK")
    check("set_status reports 0 for unknown uid", store.set_status(c,"NOPE","applied")==0)
    check("set_status reports 1 for real uid", store.set_status(c,"u1","applied")==1)

print("\n#17/#10 config guards")
import copy
# os.devnull and tempdir rather than /dev/null and /tmp, so this block also
# runs under native Windows Python. The environment is passed through — a
# user-site pip install needs APPDATA to be importable on Windows — with the
# API keys removed, so a real token in the shell can never reach the API.
NULL = os.devnull
TMP = tempfile.gettempdir()
ENV = {k: v for k, v in os.environ.items()
       if k not in ("APIFY_TOKEN", "ANTHROPIC_API_KEY")}

def run(cfgdict, args=("--dry-run",), cwd=TMP):
    f=tempfile.NamedTemporaryFile("w",suffix=".yaml",delete=False)
    yaml.dump(cfgdict,f); f.close()
    r=subprocess.run([sys.executable,str(pathlib.Path(__file__).parent/"jobsweep.py"),
                      "--config",f.name,"--local",NULL,*args],
                     capture_output=True,text=True,cwd=cwd,timeout=90,
                     env={**ENV,"APIFY_TOKEN":"dummy"})
    return r
base=copy.deepcopy(cfg); base["output"]["db"]=tempfile.mktemp(suffix=".sqlite3")
c1=copy.deepcopy(base); c1["source"]["indexed"]["queries"]=[]
r=run(c1); check("empty queries refuses to run", r.returncode!=0 and "empty" in (r.stdout+r.stderr).lower(),
                 (r.stdout+r.stderr)[-120:])
c2=copy.deepcopy(base); c2["source"]["mode"]="direct"
r=run(c2); check("mode=direct without a direct block errors clearly",
                 r.returncode!=0 and "no source.direct" in (r.stdout+r.stderr),
                 (r.stdout+r.stderr)[-120:])
c3=copy.deepcopy(base); c3["source"]={"mode":"direct","direct":{"greenhouse":None}}
r=run(c3); check("null slug list doesn't TypeError", "TypeError" not in r.stderr, r.stderr[-120:])
# --explain needs no network, so it is the one command that can be run
# end to end offline against a stored posting.
c4=copy.deepcopy(base)
c4["output"]["db"]=tempfile.mktemp(suffix=".sqlite3")
with store.connect(c4["output"]["db"]) as _c:
    store.upsert(_c, js.Job(uid="led1",title="Senior SEO Manager",company="C",
                            location="Remote",url="https://job-boards.greenhouse.io/a/1",
                            ats="greenhouse",description="organic search"))
_f=tempfile.NamedTemporaryFile("w",suffix=".yaml",delete=False); yaml.dump(c4,_f); _f.close()
r=subprocess.run([sys.executable, str(HERE/"jobsweep.py"), "--config",_f.name,
                  "--local",NULL,"--explain"],
                 capture_output=True,text=True,timeout=120, env=ENV)
check("--explain runs end to end against a stored posting",
      r.returncode==0 and "Auditing term lists" in r.stdout, (r.stdout+r.stderr)[-140:])
print("\n#R1 regressions introduced by the QA pass itself")
for term, title in [("sr ", "sr director of search"), ("sr.", "sr. seo manager"),
                    (" head of ", "head of organic growth")]:
    check(f"term {term!r} with stray whitespace still matches", js._has(term, title))
check('"Sr Director" gets the seniority point',
      "right level" in "; ".join(score("Sr Director of Search", arrangement="Remote OK").reasons))

print("\n#R2 skipped rows stay off the board but remain reachable")
import tempfile as _tf
db2=_tf.mktemp(suffix=".sqlite3")
with store.connect(db2) as c:
    a=js.Job(uid="d1",title="Live Role",company="C",location="A",url="u1",ats="t")
    b=js.Job(uid="d2",title="Shelved Role",company="C",location="B",url="u2",ats="t")
    a.score=9
    store.upsert(c,a); store.upsert(c,b,status="skipped")
    # all_jobs() with no status must hide skipped rows, or a shelved posting
    # sits on the board at score zero next to the live ones.
    check("all_jobs() hides skipped rows by default",
          all(r["status"] != "skipped" for r in store.all_jobs(c)))
    check("all_jobs('skipped') can still reach them",
          len(store.all_jobs(c, "skipped")) == 1)

print("\n#R4 prefix wildcard and whole-word default")
check('"engineer" does NOT match "engineering"', not js._has("engineer","senior engineering manager"))
check('"engineer*" DOES match "engineering"', js._has("engineer*","senior engineering manager"))
check('"market*" matches "marketing"', js._has("market*","marketing manager"))
check('"*" alone matches nothing', not js._has("*","anything at all"))
check('"intern" still does not match "international"', not js._has("intern","director, international seo"))
check('"intern" still matches "intern"', js._has("intern","marketing intern"))

print("\n#C1 years-of-experience reader")
import extract, comeet, dashboard
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
for text, want in [("5+ years of experience in Python", 5),
                   ("at least 7 years of professional experience", 7),
                   ("7-10 years of backend experience", 7),
                   ("Experience: 8+ years building services", 8),
                   ("more than 5 years of experience", 6),
                   ("ניסיון של 4 שנים לפחות", 4),
                   ("5+ שנות ניסיון בפיתוח", 5),
                   ("our founders bring 20 years of experience", None),
                   ("no number here", None)]:
    got = extract.years_required(text)
    check(f"years_required({text[:34]!r}) == {want}", got == want, f"got {got}")

print("\n#C2 tech stack reader")
st = extract.tech_stack("We use Python, Go and React with AWS and k8s. You will react fast.")
check("finds Python, Go, React, AWS, Kubernetes in order",
      st == ["Python", "Go", "React", "AWS", "Kubernetes"], st)
check('"go" and "react" as plain verbs are not tech',
      extract.tech_stack("we go fast and react to feedback") == [])
check('"Java" does not fire on "JavaScript"',
      extract.tech_stack("Strong JavaScript skills") == ["JavaScript"])

print("\n#C3 Comeet page parsing")
_page = ('<script>COMPANY_DATA = {"name": "Acme", "company_uid": "AA.001"};'
         'COMPANY_POSITIONS_DATA = [{"name": " Backend Engineer ", "uid": "B1.001",'
         ' "location": {"name": "Tel Aviv - Israel", "country": "IL", "is_remote": false},'
         ' "workplace_type": "Hybrid", "experience_level": "Entry-level",'
         ' "time_updated": "2026-11-18T06:22:38Z",'
         ' "url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/AA.001/be/B1.001",'
         ' "custom_fields": {"details": [{"name": "Description", "value": "<p>3+ years of experience</p>"}]}},'
         ' {"name": "Internal only", "uid": "B1.002", "is_internal": true}];</script>')
rows = comeet.parse_page(_page, "acme")
check("one public position parsed (internal one skipped)", len(rows) == 1, rows)
if rows:
    r0 = rows[0]
    check("title stripped, company from COMPANY_DATA",
          r0["title"] == "Backend Engineer" and r0["company"] == "Acme")
    check("country, arrangement, experience band mapped",
          (r0["country"], r0["arrangement"], r0["experience"]) == ("IL", "Hybrid", "0-2"), r0)
    check("description has tags removed", r0["description"] == "3+ years of experience", r0["description"])
try:
    comeet.parse_page("<html>generic shell</html>", "gone"); inactive = False
except comeet.Inactive:
    inactive = True
check("a page with no company data reads as Inactive, not an error", inactive)

print("\n#C4 scoring knobs")
K = {**S, "title_strong_core": ["backend"], "title_weak": [], "seniority": [],
     "exclude": [], "locations_ok": ["israel"], "threshold": 5}
def ks(title, extra=None, **kw):
    j = js.Job(uid="x", title=title, company="C", location=kw.pop("location", "Tel Aviv, Israel"),
               url="", ats="t", **kw)
    js.reset_exclusions(); js.score_job(j, {**K, **(extra or {})}); return j
check("max_years_required drops a role asking for more",
      ks("Backend Engineer", {"max_years_required": 6},
         description="8+ years of experience").score == -99)
check("max_years_required keeps a role asking for exactly the cap",
      ks("Backend Engineer", {"max_years_required": 6},
         description="6+ years of experience").score > 0)
check("junior_penalty 0 leaves 0-2 yr roles unpenalised",
      ks("Backend Engineer", {"junior_penalty": 0}, experience="0-2").score
      == ks("Backend Engineer", {"junior_penalty": 0}).score)
check("junior_penalty defaults to 3",
      ks("Backend Engineer", experience="0-2").score == ks("Backend Engineer").score - 3)
pref = {"preferred_locations": ["israel"], "preferred_bonus": 4}
check("preferred bonus applies to a title match at home",
      ks("Backend Engineer", pref).score == ks("Backend Engineer").score + 4)
check("preferred bonus does NOT lift an unrelated title",
      ks("Office Manager", pref).score == ks("Office Manager").score)

print("\n#C5 example local config and source modes")
os.environ.setdefault("APIFY_TOKEN", "dummy-for-tests")
ex = js.deep_merge(yaml.safe_load(open(HERE / "config.yaml")),
                   yaml.safe_load(open(HERE / "config.local.example.yaml")))
src = js.make_source(ex)
check("config.local.example.yaml builds a combined indexed + direct source",
      type(src).__name__ == "CombinedSource" and len(src.sources) == 2)
check("comeet_companies.tsv ships with the repo and loads",
      len(comeet.load_companies(HERE / "comeet_companies.tsv")) > 100)

print("\n#C6 board time labels")
now = _dt.now(_tz.utc)
check("'2 hours ago'", dashboard._ago(now - _td(hours=2), True) == "2 hours ago")
check("'3 days and 4 hours ago'",
      dashboard._ago(now - _td(days=3, hours=4), True) == "3 days and 4 hours ago")
check("date-only source -> days", dashboard._ago(now - _td(days=2), False) == "2 days ago")

print("\n#B1 boards behind index links")
import boards
for url, ats, want in [
        ("https://jobs.ashbyhq.com/pinecone/499ce77a", "ashby", ("ashby", "pinecone")),
        ("https://jobs.lever.co/goodleap/65bb56a4", "lever.co", ("lever", "goodleap")),
        ("https://job-boards.greenhouse.io/discord/jobs/89", "greenhouse", ("greenhouse", "discord")),
        ("https://jobs.smartrecruiters.com/ServiceNow/7440-x", "smartrecruiters",
         ("smartrecruiters", "ServiceNow")),
        ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Israel/x_JR1", "workday",
         ("workday", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")),
        ("https://www.comeet.com/jobs/port/59.004/be/F3.27B", "comeet", ("comeet", "port/59.004")),
        ("https://careers.example.com/job/123", "in-house", None)]:
    got = boards.board_of(url, ats, "X")
    check(f"board_of({ats}) -> {want}", got == want, got)

print("\n#B2 Workday country filter")
exact = [{"facetParameter": "locationMainGroup", "values": [
    {"facetParameter": "locationHierarchy1", "values": [
        {"descriptor": "Israel", "id": "IL1"}, {"descriptor": "India", "id": "IN1"}]}]}]
check("exact country value wins", js._workday_country_facet(exact, "Israel")
      == ("locationHierarchy1", ["IL1"]))
offices = [{"facetParameter": "locations", "values": [
    {"descriptor": "Office - Israel - Tel Aviv", "id": "a"},
    {"descriptor": "Office - Israel - Petach Tikva", "id": "b"},
    {"descriptor": "Office - US - Santa Clara", "id": "c"}]}]
check("falls back to every office naming the country",
      js._workday_country_facet(offices, "Israel") == ("locations", ["a", "b"]))
check("no match -> None", js._workday_country_facet(offices, "Japan") is None)

print("\n#B3 cross-source duplicate key")
check("company suffixes ignored",
      store.job_key("Check Point Software Technologies Ltd", "Backend Developer")
      == store.job_key("Check Point", "backend  developer"))
check("different titles stay apart",
      store.job_key("Acme", "Backend Developer") != store.job_key("Acme", "Frontend Developer"))

print("\n#B4 harvest grows the lists")
with tempfile.TemporaryDirectory() as d:
    bpath, cpath = pathlib.Path(d) / "boards.tsv", pathlib.Path(d) / "comeet.tsv"
    cpath.write_text("port\t59.004\n", encoding="utf-8")
    mk = lambda **k: js.Job(uid="u", title="T", location=k.pop("location", "Tel Aviv, Israel"),
                            track="israel", **k)
    jobs = [mk(company="Pinecone", url="https://jobs.ashbyhq.com/pinecone/1", ats="ashby"),
            mk(company="Pinecone", url="https://jobs.ashbyhq.com/pinecone/2", ats="ashby"),
            mk(company="Faraway", url="https://jobs.ashbyhq.com/faraway/1", ats="ashby",
               location="Paris, France"),
            mk(company="Port", url="https://www.comeet.com/jobs/port/59.004/x/1", ats="comeet"),
            mk(company="Newco", url="https://www.comeet.com/jobs/newco/AB.001/x/1", ats="comeet")]
    keep = lambda j: "israel" in j.location.lower()
    added = boards.harvest(jobs, keep, bpath, cpath)
    check("adds each new board once, skips other countries and known Comeet",
          sorted(added) == ["ashby pinecone (Pinecone)", "comeet newco/AB.001 (Newco)"], added)
    check("boards.tsv holds the new board", boards.load(bpath) == [("ashby", "pinecone", "Pinecone")])
    check("second harvest adds nothing", boards.harvest(jobs, keep, bpath, cpath) == [])

print("\n#B5 board hides old new cards")
class _R(dict):
    def __getitem__(self, k): return dict.get(self, k)
old = (_dt.now(_tz.utc) - _td(days=6)).isoformat()
check("6-day-old new card is hidden at 4 days",
      dashboard._too_old(_R(status="new", posted_at=old), 4))
check("6-day-old applied card stays",
      not dashboard._too_old(_R(status="applied", posted_at=old), 4))
check("no limit hides nothing", not dashboard._too_old(_R(status="new", posted_at=old), None))

print("\n#W1 Workday descriptions")
check("HTML stripped and whitespace collapsed",
      js._strip_html("<p>5+ years&nbsp;of <b>C++</b></p>\n<ul><li>Linux</li></ul>")
      == "5+ years of C++ Linux")
check("description capped", len(js._strip_html("x" * 9000)) == 4000)
_wj = js.Job(uid="u", title="Senior Software Engineer", company="NVIDIA Corporation",
             location="Israel", url="", ats="workday")
_known = {store.job_key("NVIDIA", "senior software engineer")}
check("already-described req is skipped (key ignores suffix and case)",
      js._workday_skip(_wj, _known))
check("new req is fetched", not js._workday_skip(_wj, set()))
with tempfile.TemporaryDirectory() as d:
    with store.connect(pathlib.Path(d) / "t.sqlite3") as c:
        store.upsert(c, js.Job(uid="a", title="Backend Developer", company="Acme",
                               location="", url="", ats="workday", description="text"))
        store.upsert(c, js.Job(uid="b", title="Frontend Developer", company="Acme",
                               location="", url="", ats="workday"))
        check("keys_with_description maps only described rows to their text",
              store.keys_with_description(c) == {store.job_key("Acme", "Backend Developer"): "text"})

print("#A1 applicant")
import applicant
with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    (d / "cv.pdf").write_bytes(b"%PDF-1.4")
    (d / "cv.txt").write_text("x")
    def _w(name, **over):
        data = {"name": "Ada Test", "email": "ada@example.com", "cv_path": str(d / "cv.pdf"),
                "heard_about_us": "Company website"}
        data.update(over)
        (d / name).write_text(yaml.safe_dump(data))
        return d / name
    def _raises(path):
        try: applicant.load(path)
        except applicant.ProfileError: return True
        return False
    _p = applicant.load(_w("ok.yaml"))
    check("valid applicant file loads", _p["name"] == "Ada Test" and _p["phone"] == "")
    check("absent demographics default to {}", _p["demographics"] == {} and _p["links"] == {})
    check("missing file raises ProfileError", _raises(d / "nope.yaml"))
    check("missing email raises ProfileError", _raises(_w("noemail.yaml", email="")))
    check("cv_path to missing file raises ProfileError", _raises(_w("nocv.yaml", cv_path=str(d / "gone.pdf"))))
    check("cv_path that is not a pdf raises ProfileError", _raises(_w("txt.yaml", cv_path=str(d / "cv.txt"))))
    _f = applicant.facts_text(_p)
    check("facts_text omits cv_path", "cv_path" not in _f)
    check("facts_text includes heard_about_us value", "Company website" in _f)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
