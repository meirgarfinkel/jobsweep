# Apply for me: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An **Apply for me** button on each board card opens the job in the user's Zen/Firefox browser; a **Fill with jobsweep** button on the form page fills it in (profile facts, CV, AI-drafted answers) for the user to review and submit.

**Architecture:** A Firefox extension (MV2) collects a page's form fields and sends them, via its background script, to the local board server (`dashboard.py`). The server builds one prompt from the job, `applicant.local.yaml` and `profile.local.md`, runs one model call (`claude -p` on the user's subscription by default, the Anthropic API optionally), validates the answer against the fields, and returns values. The extension fills them in, attaches the CV, shows a banner, and stops.

**Tech Stack:** Python 3 stdlib + PyYAML (existing); `claude` CLI (subprocess); plain JavaScript WebExtension, no build step, no Node.

**Spec:** `docs/superpowers/specs/2026-11-19-apply-for-me-design.md`

## Global Constraints

- Never submit a form. The extension never clicks or dispatches `submit`.
- One model call per fill; nothing runs after it returns. `claude -p` runs with `--tools ""` (no tools) and a timeout (default 120 s) that kills the process.
- Default backend `claude_cli`; `apply.backend: api` switches to the API (`ANTHROPIC_API_KEY`, model `claude-sonnet-5-5` unless `JOBSWEEP_MODEL` is set). For `claude_cli`, `JOBSWEEP_MODEL` (if set) is passed as `--model`.
- Personal data only in gitignored files: `applicant.local.yaml`, `profile.local.md`, the CV PDF. Never in the repo, never in logs.
- `/api/fill` and `/api/cv` answer only requests whose `Origin` starts with `moz-extension://`; anything else gets 403.
- No new Python dependencies; no Node in the app. The API backend follows `evaluate.py`'s existing urllib pattern (the project deliberately has no SDK dependency).
- Answer rules (verbatim from the spec, enforced in prompt and validation): facts from profile only; choices must be one of the offered options; free text in the question's language, no claims the background doesn't support; screening questions only when the background clearly supports them; demographics from the profile, else a "decline"/"prefer not to say" option, else empty and flagged; required agreements ticked, optional ones left unticked; "how did you hear" from the profile default; everything else empty and listed in `unanswered`.
- Tests go in `tests_regression.py` in its existing `check(name, cond)` style, offline (subprocess and HTTP are stubbed). Run: `.venv/bin/python tests_regression.py`, expected last line `N passed, 0 failed`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014coFUxm24z8oygeG2gx6sR
  ```

## Review Focus

1. **`claude` missing, not logged in, or over the usage limit:** the fill returns an error the banner shows in plain words ("Claude Code isn't logged in: run `claude` once"), not a stack trace or a silent nothing. Test in Task 2.
2. **The model returns malformed JSON, an option not on the list, or ids that don't exist:** bad values are dropped and the field is listed as unanswered; the fill still succeeds with the rest. Test in Task 3.
3. **A web page (not the extension) POSTs to `127.0.0.1:8765/api/fill` or fetches `/api/cv`:** 403, and the CV is never served. Test in Task 4.
4. **The board server isn't running when Fill is clicked:** the banner says to start `python jobsweep.py --serve`. Manual check in Task 6.
5. **Form inside a cross-origin iframe** (Greenhouse embeds `boards.greenhouse.io` on company sites): the content script runs with `all_frames: true`, so the Fill button appears inside the frame that holds the form. Manual check in Task 6.

---

### Task 1: Applicant profile

**Files:**
- Create: `applicant.py`, `applicant.local.example.yaml`
- Modify: `.gitignore` (add `applicant.local.yaml`; the CV stays wherever the user keeps it, outside the repo)
- Test: `tests_regression.py` (new group `#A1 applicant`)

**Interfaces:**
- Produces: `applicant.load(path: str | Path) -> dict` — returns the YAML as a dict with keys `name, email, phone, city, links (dict), work_authorization, notice_period, salary_expectation, heard_about_us, cv_path, demographics (dict)`; missing optional keys become `""`/`{}`. Raises `applicant.ProfileError(msg)` when the file is missing, `name`/`email` are empty, or `cv_path` doesn't point to an existing `.pdf` (path expanded with `~`, relative to the repo dir).
- Produces: `applicant.facts_text(profile: dict) -> str` — the profile as `key: value` lines for the prompt, excluding `cv_path`.

- [ ] **Step 1: Write failing tests** in a temp dir: a valid file loads and `profile["demographics"] == {}` when absent; missing `email` raises `ProfileError`; `cv_path` to a non-existent file raises `ProfileError`; `"cv_path" not in facts_text(p)` and `"Company website" in facts_text(p)` when `heard_about_us: Company website`.
- [ ] **Step 2: Run, verify the new checks fail** (`ModuleNotFoundError: applicant`).
- [ ] **Step 3: Implement `applicant.py`; write `applicant.local.example.yaml`** with every key, values marked `CHANGE`, `heard_about_us: Company website`, demographics keys `gender, race_ethnicity, veteran, disability` commented as optional; add `applicant.local.yaml` to `.gitignore`.
- [ ] **Step 4: Run tests, all pass.** Also `git check-ignore applicant.local.yaml` prints the name.
- [ ] **Step 5: Commit** `Add applicant profile loader and example`.

### Task 2: Model backend

**Files:**
- Create: `llm.py`
- Test: `tests_regression.py` (`#A2 llm backend`)

**Interfaces:**
- Produces: `llm.ask(prompt: str, backend: str = "claude_cli", timeout: int = 120, tools: str = "") -> str` — returns the model's text. `backend` is `"claude_cli"` or `"api"`.
  - `claude_cli`: `subprocess.run(["claude", "-p", "--output-format", "json", "--tools", tools] + (["--model", m] if m) , input=prompt, capture_output=True, text=True, timeout=timeout)`; parse stdout JSON, return its `result`; if `is_error` is true or exit code ≠ 0, raise `LLMError` with the CLI's message.
  - `api`: POST like `evaluate._call` with model `os.environ.get("JOBSWEEP_MODEL", "claude-sonnet-5-5")`, `max_tokens` 4000.
- Produces: `llm.LLMError(Exception)` with a user-readable message. Messages: `FileNotFoundError` → `"Claude Code isn't installed (no 'claude' command)."`; `TimeoutExpired` → `"Claude took longer than {timeout}s; try again."`; stderr/result containing "login"/"auth" → `"Claude Code isn't logged in: run 'claude' once in a terminal."`; API without key → `"Set ANTHROPIC_API_KEY or use apply.backend: claude_cli."`

- [ ] **Step 1: Write failing tests** stubbing `llm.subprocess.run`: a stub returning `{"result": "hi", "is_error": false}` makes `ask("x") == "hi"` and the argv contains `"--tools", ""` and `"-p"`; with `JOBSWEEP_MODEL=m` set, argv contains `"--model", "m"`; a stub raising `FileNotFoundError` raises `LLMError` whose text contains `"isn't installed"`; a stub raising `subprocess.TimeoutExpired` raises `LLMError` containing `"longer than"`; exit code 1 with stderr `"Please log in"` raises `LLMError` containing `"logged in"`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement `llm.py`** (refactor nothing in `evaluate.py`; copy its request shape).
- [ ] **Step 4: Run tests, all pass.** Then one live smoke check, free on the subscription: `.venv/bin/python -c "import llm; print(llm.ask('Reply with the word ok'))"` prints `ok` (or similar).
- [ ] **Step 5: Commit** `Add model backend: claude -p by default, API optional`.

### Task 3: The fill step

**Files:**
- Create: `fill.py`
- Test: `tests_regression.py` (`#A3 fill`)

**Interfaces:**
- Consumes: `applicant.facts_text`, `llm.ask`, `llm.LLMError`.
- Field shape (from the extension, Task 6): `{"id": str, "label": str, "type": "text"|"textarea"|"email"|"tel"|"url"|"number"|"date"|"select"|"radio"|"checkbox"|"file", "options": [str], "required": bool, "value": str}`.
- Produces: `fill.build_prompt(job: dict, profile: dict, background: str, fields: list[dict]) -> str` — contains the job's title, company, description (first 6000 chars), `facts_text(profile)`, the background (first 6000 chars), the fields as JSON, and the answer rules from Global Constraints, asking for ONLY `{"values": {id: value}, "notes": [str]}`. `file` fields are not sent (the extension attaches the CV itself).
- Produces: `fill.parse_fill(raw: str, fields: list[dict]) -> dict` → `{"values": {id: str|bool}, "notes": [str], "unanswered": [id]}`. Rules: extract the first `{...}` JSON; ids not in `fields` dropped; `select`/`radio` values not exactly in `options` dropped (case-insensitive match allowed, returning the option's own spelling); `checkbox` values coerced to bool; empty strings dropped; `unanswered` = required non-file fields with no value. Unparseable output → all required non-file fields unanswered and a note `"The model's answer couldn't be read; fill these by hand."`.
- Produces: `fill.run(job: dict, profile: dict, background: str, fields: list[dict], backend: str) -> dict` — `parse_fill(llm.ask(build_prompt(...), backend), fields)`; lets `LLMError` propagate.

- [ ] **Step 1: Write failing tests:** prompt contains the job title, a profile value and a field label, and not `cv_path`; parse keeps a valid text value; drops unknown id `"zzz"`; drops select value `"Maybe"` when options are `["Yes","No"]` and puts the required select in `unanswered`; maps `"yes"` to `"Yes"`; checkbox `"true"` → `True`; garbage input `"sorry"` → every required field unanswered plus the note.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement `fill.py`.**
- [ ] **Step 4: Run tests, all pass.**
- [ ] **Step 5: Commit** `Add fill step: prompt, validation of the model's answers`.

### Task 4: Server endpoints and the board button

**Files:**
- Modify: `dashboard.py` (`serve()` handler around lines 359–405; card rendering near line 223)
- Modify: `config.yaml`, `config.local.example.yaml` (new `apply:` block: `backend: claude_cli`, `applicant_file: applicant.local.yaml`, `profile_file: profile.local.md`, `timeout: 120`)
- Modify: `jobsweep.py` (pass `cfg.get("apply")` into `dashboard.serve`)
- Test: `tests_regression.py` (`#A4 server`)

**Interfaces:**
- Consumes: `fill.run`, `applicant.load`, `applicant.ProfileError`, `llm.LLMError`.
- Produces: `dashboard.apply_url(url: str, ats: str) -> str` — Lever `https://jobs.lever.co/<co>/<id>` → append `/apply` (unless already there); Ashby `https://jobs.ashbyhq.com/<co>/<id>` → append `/application`; anything else unchanged.
- Produces: `dashboard.extension_origin(origin: str | None) -> bool` — true only for `moz-extension://…`.
- Produces: `serve(..., apply_cfg: dict | None = None)`.
- Endpoints:
  - `OPTIONS /api/fill`, `/api/cv` → 204 with CORS headers echoing an extension origin; 403 otherwise.
  - `POST /api/fill` body `{uid, url, fields}` → 200 JSON from `fill.run` (job row loaded by uid; unknown uid → 404 JSON `{"error": "This job isn't in the database."}`); `ProfileError`/`LLMError` → 200 JSON `{"error": "<message>"}` so the banner can show it; non-extension origin → 403.
  - `GET /api/cv` → the PDF from `cv_path` (`application/pdf`, header `x-filename: <basename>`); non-extension origin → 403.
- Card: an **Apply for me** button `<button class="applyme" data-uid="…" data-url="{apply_url(...)}">Apply for me</button>` next to the job link. Board JS: on click, `window.postMessage({jobsweep: "open", uid, url}, location.origin)`; if no `{jobsweep: "opened"}` reply within 300 ms, `window.open(url + "#jobsweep=" + uid, "_blank", "noopener")`.

- [ ] **Step 1: Write failing tests:** `apply_url` on a Lever URL ends with `/apply`, on Ashby with `/application`, on Greenhouse unchanged, and is idempotent; `extension_origin("moz-extension://abc")` true, `"https://evil.example"` and `None` false; start `serve` on a free port in a thread against a temp DB (with `open_browser=False`, `fill.run` stubbed to return `{"values": {}, "notes": [], "unanswered": []}`) and check: POST `/api/fill` with `Origin: https://evil.example` → 403; with `Origin: moz-extension://x` and a known uid → 200 JSON with `values`; unknown uid → 404; GET `/api/cv` without an extension origin → 403; the rendered board HTML contains `class="applyme"`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** the helpers, endpoints, card button, board JS and config blocks. `serve` must stay stoppable in tests (return the server object from a helper `make_server(...)` that `serve` calls, so tests can `shutdown()` it).
- [ ] **Step 4: Run tests, all pass.** Restart the running board (stop its background task, relaunch `--serve`) and check a card shows the button.
- [ ] **Step 5: Commit** `Board: Apply for me button and fill/cv endpoints`.

### Task 5: Background from the CV (`--profile-from-cv`)

**Files:**
- Modify: `jobsweep.py` (new flag and `run_profile_from_cv(cfg)`)
- Test: `tests_regression.py` (`#A5 profile from cv`)

**Interfaces:**
- Consumes: `applicant.load`, `llm.ask(prompt, backend, tools=...)`.
- Produces: `run_profile_from_cv(cfg: dict) -> Path` — with `claude_cli`, calls `llm.ask(prompt, "claude_cli", tools="Read")` where the prompt names the absolute CV path and asks for a Markdown background (roles with dates, skills, education, languages, notable projects; facts only, nothing invented). With `api`, sends the PDF as a base64 `document` block (extend `llm.ask` with an optional `pdf_path: str | None = None` used only by the api backend). Writes `apply.profile_file`; if it exists, writes `profile.local.md.new` instead and says so, never overwriting the user's edits.
- CLI: `python jobsweep.py --profile-from-cv` prints the path written and "Read it and correct anything wrong; fills use it as written."

- [ ] **Step 1: Write failing tests** stubbing `llm.ask` to return `"# Background\n..."`: in a temp dir the file is created with that text; run again → `profile.local.md.new` created and the original untouched; with backend `claude_cli` the stub received `tools="Read"` and a prompt containing the CV's absolute path.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests, all pass.**
- [ ] **Step 5: Commit** `Add --profile-from-cv`.

### Task 6: Firefox extension

**Files:**
- Create: `extension/manifest.json`, `extension/background.js`, `extension/content.js`, `extension/board.js`, `extension/banner.css`, `extension/test/form.html`
- Test: manual checklist below (no JS test runner in this project); a manifest check in `tests_regression.py` (`#A6 extension`)

**Decisions:**
- Manifest V2 (Firefox grants host permissions at install; MV3 makes them opt-in per site, which would add a step for the user). `browser_specific_settings.gecko.id: "jobsweep@local"`. Permissions: `tabs`, `http://127.0.0.1:8765/*`, `<all_urls>`.
- `board.js`: content script on `http://127.0.0.1:8765/*`; listens for `{jobsweep:"open"}` page messages, replies `{jobsweep:"opened"}`, and messages the background script.
- `background.js`: `tabJob: Map<tabId, uid>`; on `open` → `browser.tabs.create({url})`, store the mapping; drop on `tabs.onRemoved`. Relays `fill` and `cv` requests to `127.0.0.1:8765` with `fetch` (so the page's CSP never applies). Answers `whoami` from a content script with its tab's uid. Holds no timers.
- `content.js` (`<all_urls>`, `all_frames: true`, `run_at: document_idle`): asks `whoami` (also accepts `#jobsweep=<uid>` in the URL, registering it with the background); if there is a uid and the page has at least one visible `input`/`textarea`/`select`, shows the floating **Fill with jobsweep** button (re-checks once after 3 s for late-rendering forms, then stops looking). On click: collect fields (label from `<label for>`, wrapping label, `aria-label`, `aria-labelledby`, nearest preceding text; radios grouped by `name`; ids `js-0…`), send `fill`, apply values (set `.value` via the native setter + dispatch `input` and `change`; select by option text; radios/checkboxes `.checked` + `click`-free `change` event), fetch the CV and attach it to the first `input[type=file]` that is empty and whose label/accept mentions resume/CV/pdf (else the first empty one) via `DataTransfer`, outline filled fields green and `unanswered` amber, show the banner *"Filled by jobsweep — review before submitting. N fields need you."* (or the error message), remove the Fill button. Then it does nothing more.
- `test/form.html`: a static form with text, email, tel, textarea ("Why do you want to work here?"), select, radio group, required and optional checkboxes, a file input, and the same fields again inside an `<iframe srcdoc>`.

- [ ] **Step 1: Write a failing check** in `tests_regression.py`: `extension/manifest.json` parses, `manifest_version == 2`, `"http://127.0.0.1:8765/*" in permissions`, content script entry for `content.js` has `all_frames: true`, and no `.js` file in `extension/` contains `submit(` or `requestSubmit`.
- [ ] **Step 2: Run, verify it fails.**
- [ ] **Step 3: Implement the extension files.**
- [ ] **Step 4: Run tests, all pass.** Then manual checks in Zen with the board running (`about:debugging` → This Firefox → Load Temporary Add-on → `extension/manifest.json`):
  - Open `extension/test/form.html#jobsweep=<a real uid>` via `python -m http.server` in `extension/test`: Fill button appears in the page and inside the iframe; clicking fills fields, attaches the CV, ticks the required box, leaves the optional one unticked, shows the banner; the submit button is untouched.
  - Stop the board, click Fill: banner says to start `python jobsweep.py --serve`.
  - On the board, click **Apply for me** on a Lever and a Comeet card: a new tab opens on the job; the Fill button appears once the form is on screen.
  - Check whether Zen keeps the extension after a restart with `xpinstall.signatures.required` set to `false` in `about:config`; note the result for Task 7.
- [ ] **Step 5: Commit** `Add Firefox extension for Apply for me`.

### Task 7: Docs

**Files:**
- Modify: `README.md` (new section "Apply for me"), `AGENTS.md` (setup steps), `config.yaml` comments

- [ ] **Step 1: Write the README section:** what it does and doesn't do (never submits); setup (copy `applicant.local.example.yaml`, run `--profile-from-cv`, read and fix `profile.local.md`); installing the extension in Zen/Firefox (temporary load to try it; permanent: the result from Task 6's restart check, else free unlisted signing at addons.mozilla.org with `web-ext sign --channel=unlisted`); backends (subscription by default, `apply.backend: api` with cost about 2–4¢ per fill); the answer rules in one short list; privacy (what goes to Claude: job, profile facts, background, field labels).
- [ ] **Step 2: Add the same setup steps to `AGENTS.md`** in its interview flow (ask for the CV path and demographics preferences; never write personal data outside the gitignored files).
- [ ] **Step 3: Run tests, all pass.**
- [ ] **Step 4: Commit** `Document Apply for me`, then `git push fork main`.
