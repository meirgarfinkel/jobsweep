"""The fill step: build one prompt for a form, validate the model's answer."""
import json

import applicant
import llm

CHAR_LIMIT = 6000
UNREADABLE = "The model's answer couldn't be read; fill these by hand."

RULES = """Rules for your answers:
- Use facts only from the applicant facts and the background below. Never invent facts.
- For select, radio and checkbox-group fields, the value must be exactly one of the offered options.
- Answer free text in the language of the question (a Hebrew question gets a Hebrew answer, an English question an English one). Make no claim the background doesn't support.
- Answer screening questions (experience, skills, eligibility) only when the background clearly supports the answer; otherwise leave them out.
- Demographic questions: use the applicant's profile value if there is one; otherwise pick a "decline" or "prefer not to say" option if one is offered; otherwise leave the field out and mention it in notes.
- Agreement checkboxes that are required (terms, privacy policy, confirmations) get true. Optional ones (marketing, talent pool, newsletter) get false or are left out.
- "How did you hear about us" gets the applicant's heard_about_us value.
- Leave out everything else you can't answer from the facts. Do not guess.
- Skip file upload fields; the CV is attached separately."""


def _sendable(fields):
    return [f for f in fields if f.get("type") != "file"]


def build_prompt(job, profile, background, fields):
    sent = [{k: f.get(k) for k in ("id", "label", "type", "options", "required", "value")}
            for f in _sendable(fields)]
    return "\n\n".join([
        "You are filling in a job application form for the applicant below. "
        "You never submit it; a person reviews every answer.",
        f"JOB\nTitle: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Description:\n{(job.get('description') or '')[:CHAR_LIMIT]}",
        f"APPLICANT FACTS\n{applicant.facts_text(profile)}",
        f"BACKGROUND\n{(background or '')[:CHAR_LIMIT]}",
        f"FORM FIELDS (JSON)\n{json.dumps(sent, ensure_ascii=False, indent=1)}",
        RULES,
        'Reply with ONLY a JSON object, no other text, shaped exactly like '
        '{"values": {"<field id>": "<answer>"}, "notes": ["<short note for the applicant>"]}. '
        "Use true or false for checkboxes. Put anything the applicant should check by hand in notes.",
    ])


def _first_json(raw):
    s = raw or ""
    dec = json.JSONDecoder()
    i = s.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(s, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return None


def parse_fill(raw, fields):
    fields = list(fields)
    by_id = {f.get("id"): f for f in fields}
    need = [f["id"] for f in fields if f.get("required") and f.get("type") != "file"]
    data = _first_json(raw)
    if data is None:
        return {"values": {}, "notes": [UNREADABLE], "unanswered": need}
    raw_vals = data.get("values")
    if not isinstance(raw_vals, dict):
        raw_vals = {}
    values = {}
    for fid, v in raw_vals.items():
        f = by_id.get(fid)
        if f is None or f.get("type") == "file" or v is None:
            continue
        t = f.get("type")
        if t == "checkbox":
            values[fid] = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "on")
            continue
        if isinstance(v, (dict, list)):
            continue
        v = str(v).strip() if not isinstance(v, bool) else str(v).lower()
        if not v:
            continue
        if t in ("select", "radio"):
            opts = f.get("options") or []
            match = next((o for o in opts if o == v), None) or \
                next((o for o in opts if str(o).strip().lower() == v.lower()), None)
            if match is None:
                continue
            v = match
        values[fid] = v
    notes = data.get("notes")
    notes = [str(n) for n in notes if str(n).strip()] if isinstance(notes, list) else []
    return {"values": values, "notes": notes,
            "unanswered": [i for i in need if i not in values]}


def run(job, profile, background, fields, backend, timeout=120):
    prompt = build_prompt(job, profile, background, fields)
    return parse_fill(llm.ask(prompt, backend, timeout=timeout), fields)
