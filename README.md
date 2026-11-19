# jobsweep

A job-search agent that reads companies' **own career pages** — all of them,
without you maintaining a list — scores what it finds against your profile,
and gives you a ranked digest plus a local board to track what you applied to.

```
Senior Analytics Engineer
Acme Corp · Remote - United States · greenhouse · score 8 · 150,000–185,000 USD

Posted 2026-10-20. core match: analytics engineer; right level; Remote Solely; 5-10 yrs
```

> **About this fork.** It adds a free **Comeet reader** (the careers platform
> many Israeli companies use) with a ready list of 406 companies; a
> **growing board list** that turns every company the paid index finds on
> Greenhouse, Lever, Ashby, Workday or SmartRecruiters into one read for
> free from then on; a years-of-experience cap; a "rank this region first"
> bonus; and a reworked review board (newest first, live "3 hours ago"
> times, tech stack and years asked on each card, old cards hidden). Copy `config.local.example.yaml` to get going;
> [Tweaking your search](#tweaking-your-search) lists every setting.
> Upstream: [ybendiks/jobsweep](https://github.com/ybendiks/jobsweep).

## Two things make this different

### 1. No company list to maintain

Most job tooling asks you to name the companies to watch. That question is
unanswerable: **you cannot know in advance which company is about to post the
role you want.** A hand-picked list of 30–40 employers produces maybe a couple
of relevant senior postings a month, and you'd have heard about both anyway —
so the list quietly becomes the ceiling on your search.

jobsweep inverts it. It sweeps **~275,000 company career sites across 50+ ATS
platforms** and filters by *role*, not employer. You describe the job you want
once; every company that posts one is in scope, including the ones you'd never
have thought to add. A single sweep in testing returned postings from 14
different ATS platforms — Greenhouse, Workday, BambooHR, Ashby, Jobvite,
UltiPro, Rippling, Oracle Cloud, iCIMS and more.

### 2. Straight from the source, not the resale market

Job boards are a resale market: they copy listings from employers and show
them to everyone at once. jobsweep reads the employer's own board — the same
endpoint their careers page calls — so a posting reaches you on the company's
clock rather than the aggregator's.

Each sweep prints its source mix so you can see where the postings came from:

```
sources: greenhouse 12, ashby 5, icims 3, avature 3, workday 2,
         smartrecruiters 2, hibob 1, paylocity 1 (+4 more)
```

Those are applicant tracking systems — the software employers run their own
hiring on — not places that resell listings.

One thing that reads oddly at first: a posting can show **LinkedIn** as the
*employer* while its source is `smartrecruiters`. That is LinkedIn
Corporation's own career board, which happens to run on SmartRecruiters. It is
not linkedin.com/jobs.

Be clear-eyed about what that buys you: **it's latency, not exclusivity.**
Nothing stays unlisted forever. In spot-checks, one of three roles posted the
previous day had already been syndicated to third-party boards, and two had no
trace anywhere. The gap is measured in days, and it's widest on the platforms
few crawlers bother with — which is exactly where the least-contested roles
sit.

![review board](docs/board.png)

## Quickstart

**The short version:** download the repo and give it to your favorite LLM.
It will walk you through the install and use.

Open the folder in Claude Code, Codex, Cursor or any assistant that can run
commands, and say *"set this up for me."* If you only have a chat window,
paste `AGENTS.md` and `config.yaml` into it and say the same thing; it will
hand you the commands one at a time and ask for the output. Either way the
assistant follows [`AGENTS.md`](AGENTS.md): install, token, a dry run with
the example search, then an interview that turns the job you want into your
own search config, an audit of those terms, and a scheduled morning sweep.

**The manual version** is below.

**1. Install.** Python 3.9+, two dependencies. A virtual environment keeps
them out of your system Python (the schedule example below assumes `.venv`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
```

Then run the commands below with `.venv/bin/python` (Windows:
`.venv\Scripts\python`), or activate the environment first with
`source .venv/bin/activate` and use plain `python`.

**2. Get an API token.** The sweep runs through a hosted index of company
career sites. Create a free account at [apify.com](https://apify.com), then
**Settings → API & Integrations** and copy the token. The free tier allows 500
runs a month; a daily two-track sweep uses about 60 (one run per track).

```bash
export APIFY_TOKEN=apify_api_...              # macOS / Linux
$env:APIFY_TOKEN = "apify_api_..."            # Windows PowerShell
```

**3. Try it before changing anything.** `config.yaml` ships a working
data-engineering search, so the first run returns real postings and you can
see the shape of the thing. `--dry-run` fetches and scores but writes nothing.

```bash
python jobsweep.py --dry-run
```

*No token yet, or only after Comeet companies?* This needs nothing but the
install, and is free:

```bash
python jobsweep.py --source direct --dry-run    # reads all 406 Comeet companies (~10 min)
```

**4. Make it yours.** Copy the template and edit the lines marked `CHANGE`:
your titles, the countries and cities you'd work in, what to exclude.

```bash
cp config.local.example.yaml config.local.yaml
```

**`config.local.yaml`** merges over `config.yaml` and is gitignored, so the
committed config stays shareable and your real search stays private. Lists
you set there replace the ones in `config.yaml`. Every setting is described in
[Tweaking your search](#tweaking-your-search).

Then audit what you wrote (see
[Editing the term lists safely](#editing-the-term-lists-safely)):

```bash
python jobsweep.py              # a real sweep, so there is data to audit
python jobsweep.py --explain    # what each of your terms actually matches
```

**5. Use it.**

```bash
python jobsweep.py                   # each morning: sweep + write the digest
python jobsweep.py --source direct   # Comeet and your harvested boards only, free
python jobsweep.py --source direct --light   # same, minus Workday/SmartRecruiters (quick)
python jobsweep.py --harvest         # add boards behind stored index postings to boards.tsv
python jobsweep.py --serve           # the review board
python jobsweep.py --rescore         # re-apply scoring after you edit the config
python jobsweep.py --evaluate        # optional LLM fit scoring (ANTHROPIC_API_KEY)
python comeet_discover.py            # refresh the Comeet company list
python tests_regression.py           # offline checks
python tests_e2e.py                  # full lifecycle, needs a token (a few cents)
```

**6. Schedule it.** One paid sweep on workdays plus free runs in between
works well. The paid index is queried for the last 24 hours, so running it
more often re-buys the same rows; its main job is finding companies, which
the free readers then cover every run. `crontab -e` on macOS/Linux (times
are your machine's local time; `0-4` is Sunday–Thursday):

```
JS=/full/path/to/jobsweep
0 10 * * 0-4 cd $JS && { eval "$(grep '^export APIFY_TOKEN=' $HOME/.bashrc)"; .venv/bin/python jobsweep.py; } >> sweep.log 2>&1
0 10 * * 5,6 cd $JS && .venv/bin/python jobsweep.py --source direct >> sweep.log 2>&1
0 13 * * *   cd $JS && .venv/bin/python jobsweep.py --source direct --light >> sweep.log 2>&1
30 16 * * *  cd $JS && .venv/bin/python jobsweep.py --source direct --light >> sweep.log 2>&1
0 20 * * *   cd $JS && .venv/bin/python jobsweep.py --source direct --light >> sweep.log 2>&1
```

Days without a paid run leave a gap in what the index sees, but only for
companies not yet in your board list: those already harvested are read on
every run, weekends included. A longer `timeRange` ("7d") on the next paid
run would close the gap, but it re-buys rows you already have and the row
limit then spreads thin, so it is rarely worth it.

The first line reads the token from your `~/.bashrc` at run time, so it is
never stored in the crontab; or put `APIFY_TOKEN=...` on a line of its own
above the jobs. Adjust the paths if you didn't install into `.venv`. Cron
skips runs while the computer is asleep. Windows: see step 7 of
[`AGENTS.md`](AGENTS.md).

`tests_regression.py` covers scoring, matching, storage and config handling
with no network. `tests_e2e.py` clones the repo into a temp directory, runs
two real sweeps, and checks that the digest, the database and the review board
agree with each other — then that a second run is idempotent and leaves your
application statuses untouched.

## Comeet

Comeet (now Spark Hire Recruit) is the careers platform behind many Israeli
companies' job pages. The paid index returns some Comeet postings, but only
those it first saw in the last 24 hours, up to your row limit. Each
company's public page, `www.comeet.com/jobs/<company>/<id>`, carries its open
jobs as structured data, which `comeet.py` reads directly: title, location
and country, on-site/hybrid/remote, experience level, full description and a
link. No account or token.

- **Which companies.** `comeet_companies.tsv` lists 406 companies that had a
  live Comeet page in November 2026, with their job counts then. Comeet has
  no public directory, so the list was built from
  [Common Crawl](https://commoncrawl.org)'s public URL index plus web
  searches.
- **Refreshing it.** `python comeet_discover.py` re-reads the most recent
  Common Crawl archives, merges in any new companies, opens each page once,
  and drops companies that have left Comeet. It takes 10–15 minutes. To add
  companies you found yourself, put their Comeet URLs in a text file, one
  per line, and pass `--extra urls.txt`.
- **Countries.** `comeet_countries: [IL]` keeps Israeli jobs only. Add `US`,
  `GB` and so on to keep those too; `[]` keeps everything.
- **Politeness.** A sweep opens every company page once, one per second, so
  a full pass takes about 10 minutes. Running it a few times a day is fine.
  Please don't lower `comeet_delay` or run it every few minutes: that much
  traffic is how an IP address gets blocked.
- **Times.** Comeet only publishes when a posting was last *edited*, so
  Comeet cards on the board say "updated 3 hours ago" rather than "posted".
  A job edited this morning looks new even if it opened weeks ago.

## The growing board list

The paid index finds jobs anywhere, but only those it first saw in the last
24 hours, a capped number per run, at about 1¢ each. The free readers see
every open job on a board, as often as you like, but only on boards they
know about. This fork joins the two.

After each paid sweep, every posting located in `board_locations` is checked
for the board behind its link:

| Platform | From a link like | Read free with |
|---|---|---|
| Greenhouse | `job-boards.greenhouse.io/discord/…`, or a company site with `?gh_jid=…` (the board name is guessed from the company name and confirmed against Greenhouse) | public board API, descriptions included |
| Lever | `jobs.lever.co/goodleap/…` | public postings API |
| Ashby | `jobs.ashbyhq.com/pinecone/…` | public job-board API |
| Workday | `nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/…` | the board's own JSON; `workday_country` asks it for one country only. No descriptions, so the years and stack readers have nothing to read |
| SmartRecruiters | `jobs.smartrecruiters.com/ServiceNow/…` | public postings API, plus one request per local job for its description |
| Comeet | `comeet.com/jobs/reflectiz/CA.008/…` | added to `comeet_companies.tsv` |

New boards go into `boards.tsv` and the run prints them (`harvest: 3 new
board(s) …`). From the next run on, each one is read in full, filtered to
`board_locations`. Run `python jobsweep.py --harvest` once to seed the list
from postings you already have. You can also add boards by hand: one line
per board, `platform<TAB>board<TAB>Company name`.

Workday and SmartRecruiters boards take many requests each (NVIDIA has
~400 Israeli reqs, 20 per page, with a pause between pages to avoid being
rate-limited), so `--light` skips them for quick extra runs. When the same
job arrives from two places (the index one day, the board the next), it
updates the existing card instead of adding a second one; company names are
compared without suffixes like "Ltd" or "Software Technologies".

## Tweaking your search

Everything lives in `config.local.yaml` (start from
`config.local.example.yaml`). After editing scoring settings, run
`python jobsweep.py --rescore` to re-score what's already stored, then
`--explain` to audit the terms.

**What gets fetched** (`source:`)

| Setting | What it does |
|---|---|
| `mode` | `indexed` (paid index), `direct` (Comeet and other boards, free), or `[indexed, direct]` for both |
| `indexed.queries[].input.titleSearch` | Titles to fetch. Word match: `"backend engineer"` finds "Senior Backend Engineer". `"Python:*"` for a prefix. |
| `…titleExclusionSearch` | Titles never fetched, so never paid for. Can't be audited, so keep it to sure things. |
| `…locationSearch` | `["Israel"]`, `["United States"]`, or `"City, Region, Country"` in English |
| `…aiWorkArrangementFilter` | `["Remote OK", "Remote Solely"]` for remote only; also `"On-site"`, `"Hybrid"` |
| `…limit` | Rows per track per run, about 1¢ each. Minimum 10. |
| `direct.comeet_countries` | ISO country codes to keep from Comeet; `[]` for all |
| `direct.comeet_file` | The company list (`comeet_companies.tsv`) |
| `direct.boards_file` | Harvested boards (`boards.tsv`, gitignored, grows by itself) |
| `direct.board_locations` | Keep only jobs located here from harvested boards; also limits what gets harvested |
| `direct.workday_country` | `Israel`: ask Workday boards for that country's jobs only |
| `board.max_age_days` | Hide untouched cards older than this on the board (e.g. `4`) |

**How it's scored** (`scoring:`). A posting appears in the digest when its
score reaches `threshold`.

| Setting | Points | Notes |
|---|---|---|
| `title_strong_core` | +3 | Words that make a title yours. Whole-word match; `engineer*` for a prefix. |
| `title_weak` | +1 | Generic words like `engineer`, `developer` |
| `seniority` | +1 | Leave `[]` if you don't want senior titles ranked up |
| Remote work | +2 | From the index's or Comeet's own classification |
| `locations_ok` | +1 | On-site/hybrid in a place you'd go to; elsewhere it's −2 |
| `preferred_locations` | `+preferred_bonus` | Ranks one region first; only applies to title matches |
| 5–10 / 10+ years band | +1 | As tagged by the index |
| 0–2 years band | −`junior_penalty` | Default 3; set `0` to keep junior roles level |
| `exclude` | drops it | Title words that rule a posting out (no `*`) |
| `max_years_required` | drops it | When the description asks for more years than this |
| `bonus_terms` | +1 | When the description mentions something you're strong in |

**Examples**

- *Remote anywhere, no on-site:* leave `locations_ok: []`, drop the
  `locationSearch` line or set `aiWorkArrangementFilter` on every track, and
  set `comeet_countries: []`.
- *Tel Aviv area only, no remote abroad:* delete the second track, set
  `comeet_countries: [IL]`, and list only nearby cities in `locations_ok`
  (not `israel`).
- *Mid-level and up:* `seniority: [senior, lead]`, `junior_penalty: 3`, and
  add `junior` to `exclude`.
- *Skip an industry:* add its words to `domain_exclusions`, and move any
  ambiguous title words to `title_strong_conditional` so they only count
  when no excluded domain is mentioned.

## Editing the term lists safely

The scoring terms match **whole words**: `intern` does not match
`International`. That matters more than it sounds — before this was fixed, an
`intern` entry in the exclude list was silently deleting `Director,
International SEO` from every digest, with nothing to show it had happened.

The flip side is no stemming: `engineer` does not match `engineering`. Add a
trailing `*` when you want a prefix — `engineer*`, `market*`.

Because a bad term fails *silently*, the tool audits itself:

```bash
python jobsweep.py --explain
```

It reports how many stored postings each term matches, with an example, and
flags terms that match nothing or that drop an unusual share of postings. Run
it after editing any list. Every sweep also prints what its local exclude list
removed, so a term that starts eating good roles shows up the same day.

**One blind spot, worth knowing about.** `titleExclusionSearch` filters at the
API, before anything is fetched or stored — so `--explain` cannot audit it and
no report can tell you what it dropped. That is the price of filtering at the
source, which is also what keeps the bill down, since you pay per row
returned. The practical rule: put *cheap, obviously-safe* exclusions in
`titleExclusionSearch` (`intern`, `account executive`), and anything you are
unsure about in the local `exclude` list, where its effect is visible and
costs only the row you already paid for.

```
title_strong_core  (scores +3 on its own)
  seo                             9 (25%)  e.g. "Manager of SEO"
  generative engine               0 ( 0%)  <- no match in this sample
```

## The review board

`python jobsweep.py --serve` runs a local page (standard library only — no
Node, no build step) at http://127.0.0.1:8765/ listing everything swept.

- **Order.** Roles that reached your threshold come first, newest at the
  top; the rest follow. A link switches to best-score order.
- **Times.** "2 hours ago", "3 days and 4 hours ago", kept current while the
  tab is open. Green under 24 hours, yellow after.
- **Each card** shows the company, location, source, score and the reasons
  for it, the years of experience the description asks for, and the tech
  stack it names (Python, AWS, Kubernetes and ~60 others).
- **Status.** Set each role to `new / shortlist / applied / interview /
  offer / rejected / silence / skipped`; it writes straight back to SQLite.
  `skipped` hides a role. A later sweep never clobbers a status you set.
- **Old cards.** With `board: max_age_days: 4`, cards still marked `new`
  disappear once their posting is more than 4 days old, also in an open
  tab. Anything you've shortlisted or applied to stays. A link at the top
  shows the hidden ones.

The board reads the database on every load, so new postings from a
scheduled sweep appear on refresh.

## Optional LLM fit scoring

The built-in scorer is keyword-based: fast, free, runs on everything, and
blunt. `--evaluate` sends the postings that cleared that bar — plus your
background from `profile.local.md` — to Claude, and returns a 0–100 fit score
with strengths, gaps, risks and an apply/consider/skip call. Results are
cached in the database, so a posting is never paid for twice. It costs money
and is strictly optional; the sweep works fine without it.

## How it works

**Two tracks.** Track 1 matches titles that are unambiguously your field.
Track 2 takes a *broad* title set and ANDs it against a few *specific*
description terms, catching roles whose work is your field whatever the title
calls them. Track 2 is the portable idea here — useful in any discipline where
titles are inconsistent.

**Scoring** uses the index's structured classifications — work arrangement,
experience band, salary — rather than guessing them from strings, then adds
title tiers, seniority, and a location rule. Every digest line prints the
reasons behind its score, so a surprising rank is debuggable.

**Location.** Remote scores up. On-site and hybrid outside your commute range
are penalised *unless* the title is senior enough or the pay high enough to be
worth relocating for — thresholds you set.

**Ambiguous terms.** Some tokens mean different things by industry:
`discovery` is drug discovery in pharma, `marketing engineer` is a
semiconductor role. Those sit in a conditional tier that only scores strong
when no `domain_exclusions` term appears in the posting.

**Two sources** behind one interface: `indexed` sweeps nationally through a
hosted ATS index; `direct` hits company boards yourself (Greenhouse, Lever,
Ashby, Workday adapters included) for a free, narrower setup. Switching is a
config change.

## Findings

Things that cost real API credits to learn:

- **Matching is token-based, not substring.** `Search` does not match
  `Research`. Prefix matching needs explicit `:*` syntax.
- **`timeRange` filters on index time, not posted date.** An 8-day-old posting
  can arrive in a 24-hour window. Switching to `datePostedAfter` looks like
  the fix and is a trap: Workday and BambooHR lagged up to 4 days behind their
  own posted dates in testing, so a daily posted-date filter silently drops
  them — and those slow platforms hold the least-syndicated roles. Use
  `timeRange` and let the store dedupe.
- **A term that appears in most postings will crowd out the real matches.**
  Gating product roles on `LLM` returned 36 generic AI postings out of 40,
  because every tech posting name-drops LLMs now; the relevant ones were
  pushed past `limit`. Dropping that one term took 40 noisy rows to 16
  precise ones.
- **Never put a country in the commute list.** `united states` in
  `locations_ok` matches every US city, so an on-site role 2,000 miles away
  scores as "reachable".
- **Don't exclude `associate`.** It reads like a junior marker but kills
  `Associate Director`, a senior title.
- **Filter at the source.** You pay per row returned, so an exclusion term in
  the query is cheaper than one applied afterwards.
- **Expect noise at wide filters.** Before tuning, one sweep ran about 26%
  relevant; the dominant false positives were companies *building* search
  engines rather than optimising for them.

## Cost

Pay-per-row against an ATS index, roughly $0.01 per posting returned. With
tight filters a two-track daily sweep lands near **$4–6/month**. Comeet and
the other direct boards are free. `--evaluate` is billed separately by
Anthropic and only runs on postings that already cleared the keyword bar.

## Privacy

`config.local.yaml`, `profile.local.md`, `jobsweep.sqlite3`, `digests/` and
`sweep.log` are gitignored — they hold your real search terms, your CV text, your shortlist
and your application history. API keys are read from the environment; the tool
never writes them to a file. If you schedule the sweep, the token lives in
your own crontab or user environment, which only your account can read.
`comeet_companies.tsv` is committed: it holds only public company page IDs
and job counts, nothing about you.

## License

MIT
