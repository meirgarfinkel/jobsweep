# "Apply for me": design

Date: 2026-11-19 · Status: draft for review

## Intent

Filling out application forms is the part of applying the user wants taken
off their plate. Each card on the review board gets an **Apply for me**
button. Clicking it opens that job's real application page in a new tab of
the user's own browser, with the form already filled in: their details,
their CV attached, and AI-drafted answers to the free-text questions, based
on their background and that job's description. The user reviews it, edits
what they like, and submits it themselves.

## Goals

- One click from a card to a filled-in form in a new tab of the user's
  normal browser (Zen, which is Firefox-based), using their existing
  logins and sessions.
- Works on the platforms jobsweep reads: Comeet, Greenhouse, Lever, Ashby,
  Workday, SmartRecruiters. Ideally also on forms it has never seen.
- Free-text questions and cover-letter boxes get drafts written from the
  user's CV/background and the job description.
- The same CV PDF is attached to every application.

## Non-goals

- **No automatic submission.** The user always presses submit.
- No tailored CV per job.
- No account creation or login on the user's behalf. If a site (typically
  Workday) needs a login first, the user logs in and clicks again.
- No tracking of whether the user submitted (they set the card's status
  themselves, as today).
- Not Chrome-first. Chrome support can come later from the same code.

## Anti-goals (a failure even if it technically works)

- **An AI process that keeps running after the form is filled.** The fill
  is one round trip that ends completely; nothing watches the page, polls,
  or waits for the user to submit. Afterwards the tab holds only a static
  banner.
- Invented facts in the user's name: an AI answer claiming experience,
  numbers, dates or employers that aren't in the user's background.
- Ticking an optional box the user didn't ask for (marketing emails,
  talent pools).
- Silently leaving a required field wrong or empty without the user being
  able to see that.
- Sending the user's CV or details anywhere except the job's own form and
  the one model request that drafts the answers.

## Constraints

- jobsweep is plain Python with a stdlib HTTP server (`dashboard.py`) on
  `127.0.0.1:8765`. It already has `/api/status`. No build step, no Node in
  the app itself.
- A web page can't fill a form in another site's tab, so a browser
  extension does the filling. Driving the user's everyday browser from an
  automation tool isn't an option (needs a separate profile, losing logins).
- **Firefox only installs signed extensions permanently.** Unsigned ones
  load "temporarily" via `about:debugging` and vanish on restart. Free
  private signing exists: upload to addons.mozilla.org as *unlisted* under
  the user's own account, get a signed `.xpi` back. Whether Zen honours
  `xpinstall.signatures.required = false` is unverified; check it first.
- **Model access: the user's Claude subscription by default.** Each fill
  runs Claude Code in its scripted mode (`claude -p`, logged in with the
  user's Pro/Max subscription) as a one-off process that answers and
  exits. No extra charge; it counts toward the subscription's usage
  limits. Adds a few seconds of start-up, so a fill takes roughly
  10-30 s. The Anthropic API (`ANTHROPIC_API_KEY`, about 2-4 cents per
  fill on Sonnet 5.5) is an optional setting for cloners without a
  subscription. Subscription use is for the user's own machine only, not
  a service for others.
- Personal data (applicant details, CV, background text) lives only in
  gitignored local files, never in the repo.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Firefox extension + local server; the AI reads each form's fields and maps them to the profile, and drafts free-text answers | Works on any platform and survives form redesigns. A few cents per fill; field mapping can occasionally be wrong (the user reviews anyway) |
| B | Extension with a hand-written field mapping per platform; AI only for free text | More predictable and cheaper on six known platforms, but more code, breaks quietly when a form changes, useless on unknown platforms |
| C | Separate Chromium window driven by Playwright | No extension, but not the user's browser: separate logins (Workday), more moving parts |

The user picked **A**.

## Design

### Flow

Most job links open the posting's description page, not the form: the
user still clicks the site's own Apply button, and Workday often asks
them to log in first. So the fill is triggered on the form page, with a
second click (agreed with the user):

```
board card ──click "Apply for me"──▶ extension opens a new tab on the job's
                                     link and notes: this tab → job <uid>
        user clicks the site's Apply / logs in, as needed
                                              │
   a page in that tab has a form → small "Fill with jobsweep" button appears
                                              │  user clicks it
             collects fields (label, type, options, required, current value)
                                              │
            POST 127.0.0.1:8765/api/fill  {uid, url, fields}
                                              │
          server: load job (description) + applicant profile + background
                  one Claude request → {field_id: value, ...} + notes
                                              │
                      ◀── response ──  content script fills fields,
                                        attaches CV, shows banner. Done.
```

Nothing runs after the fill. The server's request handler returns, and the
content script leaves no timers or listeners that call home. The only
thing kept is the extension's note of which tab belongs to which job
(dropped when the tab closes), so the button can appear on later pages of
the same application. Nothing happens until the user clicks it; on
multi-page forms (Workday) they click it once per page.

Where it's cheap, the card's link goes straight to the form: Lever
`<posting>/apply`, Ashby's application tab. Other platforms open the
posting page.

### Pieces

1. **Board button.** Each card gets an **Apply for me** button. It hands
   the extension the job's uid and link, and the extension opens the tab
   and records tab → uid. (If the extension isn't installed, the button
   falls back to opening the link with `#jobsweep=<uid>`, which the
   content script also recognises.)
2. **Applicant profile:** `applicant.local.yaml` (new, gitignored; ship
   `applicant.local.example.yaml` to copy) holds structured facts: name,
   email, phone, city, LinkedIn/GitHub/portfolio URLs, work authorization,
   notice period, salary expectation (optional), "how did you hear about
   us" default (e.g. "Company website"), the CV PDF path, and a
   **demographics** section (gender, race/ethnicity, veteran, disability,
   each optional).
3. **Background text** comes from `profile.local.md`, the file
   `--evaluate` already uses. A one-time setup command
   (`python jobsweep.py --profile-from-cv`) has Claude read the CV PDF
   (through `claude -p`, allowed to read that one file, or the API) and
   writes `profile.local.md` for the user
   to read and correct. Fills use that file; they never re-send the PDF to
   the model. No PDF library is added.
4. **Server endpoints** in `dashboard.py`:
   - `POST /api/fill`: takes `{uid, url, fields}`, returns
     `{values: {field_id: value}, notes: [...], unanswered: [field_id]}`.
     One model call per request, no state kept afterwards.
   - `GET /api/cv`: returns the CV PDF so the content script can attach it.
   - Both only answer requests from the extension (origin
     `moz-extension://…`), never from arbitrary web pages.
5. **The AI step** (new module next to `evaluate.py`): one model call,
   through a small backend switch: `claude -p` (default) or the API
   (`apply.backend: api` in config). With `claude -p`, Claude Code's
   tools are turned off for fills, so it only reads the prompt and
   answers; the process exits when it has replied, and a timeout kills it
   if it doesn't. One prompt with the
   job (title, company, description), the profile facts, the background
   text, and the list of fields. It returns a value per field. Rules
   (agreed with the user):
   - **facts** (name, email, phone, links, notice period, …) from the
     profile only;
   - **choices** (dropdowns, radios) as one of the offered options, never
     free text;
   - **free text and cover letters** drafted from background + job, in the
     language of the question (Hebrew questions get Hebrew answers), with
     no claims the background doesn't support;
   - **screening questions** ("3+ years of Python?", "willing to
     relocate?", "years with React") answered only when the background
     clearly supports the answer; otherwise empty and flagged;
   - **demographic questions** (gender, race, veteran, disability) filled
     from the profile's demographics section; where it has no answer, the
     form's "decline to answer"/"prefer not to say" option if one exists,
     else empty and flagged;
   - **required agreement checkboxes** (terms, privacy policy, data
     processing consent) ticked; optional ones (marketing emails, talent
     community, newsletters) left unticked;
   - **"How did you hear about us?"** from the profile default;
   - **anything else the profile doesn't answer is left empty and listed in
     `unanswered`**, never guessed (salary, dates, ID numbers, …).
6. **Firefox extension** (WebExtension, MV3 or MV2, whichever Firefox
   handles more simply for this):
   - background script keeps tab → job uid (from the board button or the
     `#jobsweep=` fallback) and drops it when the tab closes;
   - in a linked tab, a content script shows the **Fill with jobsweep**
     button once the page has form fields; one click runs one fill;
   - collects form fields, including ones inside same-origin iframes
     (Greenhouse and Comeet embed forms in iframes on company sites);
   - fills values the way frameworks notice (set value + dispatch
     `input`/`change` events), selects options, ticks boxes;
   - attaches the CV to the file input via `DataTransfer`;
   - outlines filled fields in one colour and `unanswered` required
     fields in another, and shows a banner at the top: *"Filled by
     jobsweep — review before submitting. N fields need you."*;
   - then stops.
7. **Install** instructions in the README for Zen/Firefox: the
   unlisted-signing route, plus the temporary-load route for trying it.

### Calls made by Claude (for the user to check)

- Fills run through the user's Claude subscription (`claude -p`) by
  default; the API is an opt-in setting (agreed with the user).
- One-time `--profile-from-cv` setup has Claude read the CV PDF once; fills
  reuse the resulting `profile.local.md`.
- Lever and Ashby links are rewritten to their form pages; other
  platforms open the posting page.
- `applicant.local.yaml` format is new, with an example file to copy.
- Fills and setup only answer requests from the extension's own origin.
- Required vs optional agreements are told apart by the form's own
  "required" marking first, and by wording only when the form doesn't
  mark them.

### Builder's to decide

- MV2 vs MV3 for Firefox, and the exact messaging between content script,
  background script and localhost.
- How fields are identified in the request and response (stable ids
  generated by the content script).
- Waiting for forms that render late (e.g. Workday's single-page app): the
  content script may wait a bounded few seconds for fields to appear before
  collecting them. That's part of the initial fill, not a lingering
  process.
- Which Claude model: `claude -p`'s own default unless `JOBSWEEP_MODEL`
  is set (passed as `--model`); for the API backend, Sonnet 5.5
  (`claude-sonnet-5-5`) via `JOBSWEEP_MODEL` as `evaluate.py` does.
- The exact `claude -p` flags for no tools, JSON output and timeout.
- How the board page talks to the extension (e.g. a content script on
  `127.0.0.1:8765` that relays the button click), and the fallback when
  the extension isn't installed.
