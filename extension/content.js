// Runs in every frame. On a tab linked to a jobsweep job, it shows a
// "Fill with jobsweep" button next to the form; one click collects the
// fields, asks the board for answers, fills them in, attaches the CV and
// shows a banner. It never submits the form.
(() => {
  if (window.__jobsweepContent) return;
  window.__jobsweepContent = true;

  const CV_HINT = /resume|résumé|\bcv\b|curriculum|pdf|קורות/i;
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "password", "range", "color"]);
  const KEEP_TYPES = new Set(["text", "email", "tel", "url", "number", "date", "checkbox", "radio", "file"]);
  const GREEN = "#1a7f37";
  const AMBER = "#d97706";
  const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif";

  let uid = null;
  let host = null, root = null, button = null, banner = null;
  let busy = false, finished = false;

  // ---------- uid ----------

  function hashUid() {
    const m = /[#&]jobsweep=([^&]+)/.exec(location.hash || "");
    if (!m) return null;
    try {
      return decodeURIComponent(m[1]);
    } catch (e) {
      return null;
    }
  }

  async function findUid() {
    const fromHash = window === window.top ? hashUid() : null;
    try {
      const r = await browser.runtime.sendMessage(
        fromHash ? { type: "register", uid: fromHash } : { type: "whoami" });
      return (r && r.uid) || null;
    } catch (e) {
      return null;
    }
  }

  // ---------- visibility and labels ----------

  function shown(el) {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }

  function kind(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "textarea") return "textarea";
    if (tag === "select") return "select";
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (SKIP_TYPES.has(t)) return null;
    return KEEP_TYPES.has(t) ? t : "text";
  }

  // Checkboxes and radios are often hidden behind a styled label.
  function visibleField(el, k) {
    if (el.disabled) return false;
    if (shown(el)) return true;
    if (k === "checkbox" || k === "radio") return Array.from(el.labels || []).some(shown);
    return false;
  }

  function clean(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function labelsText(el) {
    return clean(Array.from(el.labels || []).map((l) => l.textContent).join(" "));
  }

  function byIds(ids) {
    return clean((ids || "").split(/\s+/).map((id) => {
      const n = id && document.getElementById(id);
      return n ? n.textContent : "";
    }).join(" "));
  }

  function hasControl(node) {
    return node.nodeType === 1 &&
      (node.matches("input, textarea, select") || !!node.querySelector("input, textarea, select"));
  }

  // The closest text before el in the document: previous siblings first,
  // then the parent's previous siblings, a few levels up.
  function precedingText(el) {
    let node = el;
    for (let depth = 0; node && depth < 4; depth++) {
      for (let s = node.previousSibling; s; s = s.previousSibling) {
        if (s.nodeType === 1 && hasControl(s)) return "";
        const t = clean(s.textContent);
        if (t) return t.length <= 300 ? t : "";
      }
      node = node.parentElement;
    }
    return "";
  }

  function labelFor(el) {
    return labelsText(el) ||
      byIds(el.getAttribute("aria-labelledby")) ||
      clean(el.getAttribute("aria-label")) ||
      precedingText(el) ||
      clean(el.getAttribute("placeholder")) ||
      clean(el.getAttribute("name"));
  }

  function groupLabel(radios) {
    const first = radios[0];
    const fs = first.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && clean(lg.textContent)) return clean(lg.textContent);
    }
    const rg = first.closest("[role=radiogroup], [role=group]");
    if (rg) {
      const t = byIds(rg.getAttribute("aria-labelledby")) || clean(rg.getAttribute("aria-label"));
      if (t) return t;
    }
    // Climb to the element that holds every radio of the group, then take
    // the text just before it.
    let box = first.parentElement;
    while (box && !radios.every((r) => box.contains(r))) box = box.parentElement;
    return (box && precedingText(box)) || clean(first.getAttribute("name"));
  }

  function optionLabel(radio) {
    return labelsText(radio) || clean(radio.getAttribute("aria-label")) || clean(radio.value);
  }

  function isRequired(el, label) {
    return !!el.required || el.getAttribute("aria-required") === "true" || /\*\s*$/.test(label);
  }

  // ---------- collect ----------

  // fields: what the board gets; refs: id -> {kind, el or els}.
  function collect() {
    const fields = [];
    const refs = new Map();
    const groups = new Map();
    let n = 0;
    const nextId = () => "js-" + n++;

    for (const el of document.querySelectorAll("input, textarea, select")) {
      const k = kind(el);
      if (!k) continue;
      if (k === "file") {
        if (el.disabled) continue;
        const id = nextId();
        const label = labelFor(el);
        fields.push({ id, label, type: "file", options: [], required: isRequired(el, label), value: "" });
        refs.set(id, { kind: k, el });
        continue;
      }
      if (!visibleField(el, k)) continue;
      if (k === "radio") {
        const key = el.name || "#" + n;
        let g = groups.get(key);
        if (!g) {
          g = { id: nextId(), els: [] };
          groups.set(key, g);
          fields.push(g.field = { id: g.id, label: "", type: "radio", options: [], required: false, value: "" });
          refs.set(g.id, { kind: k, els: g.els });
        }
        g.els.push(el);
        continue;
      }
      const id = nextId();
      const label = labelFor(el);
      const f = { id, label, type: k, options: [], required: isRequired(el, label), value: "" };
      if (k === "select") {
        f.options = Array.from(el.options)
          .filter((o) => o.value !== "" && clean(o.text))
          .map((o) => clean(o.text));
        const sel = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
        f.value = sel && sel.value !== "" ? clean(sel.text) : "";
      } else if (k === "checkbox") {
        f.value = !!el.checked;
      } else {
        f.value = el.value || "";
      }
      fields.push(f);
      refs.set(id, { kind: k, el });
    }

    for (const g of groups.values()) {
      const f = g.field;
      f.label = groupLabel(g.els);
      f.options = g.els.map(optionLabel);
      f.required = g.els.some((r) => isRequired(r, ""));
      const on = g.els.find((r) => r.checked);
      f.value = on ? optionLabel(on) : "";
    }
    return { fields, refs };
  }

  // ---------- apply ----------

  function fire(el, names) {
    for (const name of names) el.dispatchEvent(new Event(name, { bubbles: true }));
  }

  // The prototype's setter, so frameworks that wrap .value on the element
  // (React) see the change when the input event arrives.
  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype
      : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, v);
    else el.value = v;
    fire(el, ["input", "change"]);
  }

  function setChecked(el, on) {
    if (el.checked === on) return;
    el.checked = on;
    fire(el, ["input", "change"]);
  }

  function same(a, b) {
    return clean(String(a)).toLowerCase() === clean(String(b)).toLowerCase();
  }

  function mark(els, color) {
    for (const el of els) {
      el.style.setProperty("outline", "2px solid " + color, "important");
      el.style.setProperty("outline-offset", "2px", "important");
    }
  }

  function visibleTargets(ref) {
    const els = ref.els || [ref.el];
    const out = [];
    for (const el of els) {
      if (shown(el)) out.push(el);
      else out.push(...Array.from(el.labels || []).filter(shown));
    }
    return out.length ? out : els;
  }

  function apply(ref, v) {
    if (ref.kind === "select") {
      const opt = Array.from(ref.el.options).find((o) => same(o.text, v));
      if (!opt) return false;
      setValue(ref.el, opt.value);
      return true;
    }
    if (ref.kind === "radio") {
      const r = ref.els.find((x) => same(optionLabel(x), v));
      if (!r) return false;
      setChecked(r, true);
      return true;
    }
    if (ref.kind === "checkbox") {
      setChecked(ref.el, v === true || v === "true");
      return true;
    }
    if (ref.kind === "file") return false;
    setValue(ref.el, String(v));
    return true;
  }

  function cvTarget(refs) {
    const empty = [];
    for (const ref of refs.values()) {
      if (ref.kind === "file" && !(ref.el.files && ref.el.files.length)) empty.push(ref);
    }
    const hint = (ref) => CV_HINT.test([labelFor(ref.el), ref.el.getAttribute("accept"),
      ref.el.getAttribute("name"), ref.el.id].join(" "));
    return empty.find(hint) || empty[0] || null;
  }

  async function attachCv(ref) {
    let r;
    try {
      r = await browser.runtime.sendMessage({ type: "cv" });
    } catch (e) {
      return "CV not attached: the extension stopped responding.";
    }
    if (!r || r.error || !r.data) return "CV not attached: " + ((r && r.error) || "no file came back.");
    try {
      const file = new File([r.data], r.name || "cv.pdf", { type: "application/pdf" });
      const dt = new DataTransfer();
      dt.items.add(file);
      ref.el.files = dt.files;
      fire(ref.el, ["input", "change"]);
    } catch (e) {
      return "CV not attached: this page's upload field didn't accept it. Attach it by hand.";
    }
    return null;
  }

  // ---------- UI ----------

  function css(el, text) {
    el.style.cssText = text;
    return el;
  }

  function ensureHost() {
    if (host && host.isConnected) return;
    host = document.createElement("jobsweep-ui");
    host.id = "jobsweep-ui";
    root = host.attachShadow({ mode: "closed" });
    const box = css(document.createElement("div"),
      "display:flex;flex-direction:column;align-items:flex-end;gap:8px;font:14px/1.4 " + FONT + ";color:#111;");
    root.appendChild(box);
    (document.documentElement || document.body).appendChild(host);
  }

  function box() {
    ensureHost();
    return root.firstChild;
  }

  function showButton() {
    if (button || finished) return;
    button = css(document.createElement("button"),
      "all:initial;cursor:pointer;font:600 14px/1 " + FONT + ";color:#fff;background:#1f6feb;" +
      "padding:10px 14px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.25);");
    button.type = "button";
    button.textContent = "Fill with jobsweep";
    button.addEventListener("click", onFill);
    box().appendChild(button);
  }

  function setBusy(on) {
    busy = on;
    if (!button) return;
    button.disabled = on;
    button.textContent = on ? "Filling…" : "Fill with jobsweep";
    button.style.setProperty("opacity", on ? "0.7" : "1");
    button.style.setProperty("cursor", on ? "progress" : "pointer");
  }

  function removeButton() {
    if (button) button.remove();
    button = null;
  }

  function showBanner(title, lines, isError) {
    if (banner) banner.remove();
    banner = css(document.createElement("div"),
      "all:initial;display:block;box-sizing:border-box;max-width:380px;padding:12px 32px 12px 14px;" +
      "border-radius:8px;position:relative;font:14px/1.4 " + FONT + ";color:#111;" +
      "box-shadow:0 2px 10px rgba(0,0,0,.25);background:" + (isError ? "#fff4e5" : "#e9f7ef") + ";" +
      "border:2px solid " + (isError ? AMBER : GREEN) + ";");
    const head = css(document.createElement("div"), "font-weight:600;");
    head.dir = "auto";
    head.textContent = title;
    banner.appendChild(head);
    if (lines.length) {
      const ul = css(document.createElement("ul"), "margin:6px 0 0;padding:0 0 0 18px;");
      for (const line of lines) {
        const li = document.createElement("li");
        li.dir = "auto";
        li.textContent = line;
        ul.appendChild(li);
      }
      banner.appendChild(ul);
    }
    const x = css(document.createElement("button"),
      "all:initial;position:absolute;top:6px;right:8px;cursor:pointer;font:18px/1 " + FONT + ";color:#555;");
    x.type = "button";
    x.textContent = "×";
    x.title = "Close";
    x.addEventListener("click", () => { banner.remove(); banner = null; });
    banner.appendChild(x);
    box().insertBefore(banner, box().firstChild);
  }

  // ---------- fill ----------

  async function onFill() {
    if (busy || finished) return;
    setBusy(true);
    const { fields, refs } = collect();
    let res;
    try {
      res = await browser.runtime.sendMessage({ type: "fill", url: location.href, fields });
    } catch (e) {
      res = { error: "The jobsweep extension stopped responding. Reload the page and try again." };
    }
    if (!res || res.error) {
      // Nothing was filled, so the button stays for another try.
      showBanner((res && res.error) || "jobsweep didn't answer.", [], true);
      setBusy(false);
      return;
    }

    const values = res.values || {};
    const unanswered = new Set(Array.isArray(res.unanswered) ? res.unanswered : []);
    const lines = (Array.isArray(res.notes) ? res.notes : []).map(String);
    for (const [id, v] of Object.entries(values)) {
      const ref = refs.get(id);
      if (!ref) continue;
      if (apply(ref, v)) mark(visibleTargets(ref), GREEN);
      else unanswered.add(id);
    }
    const target = cvTarget(refs);
    if (target) {
      const problem = await attachCv(target);
      if (problem) lines.push(problem);
      mark(visibleTargets(target), problem ? AMBER : GREEN);
    }
    for (const id of unanswered) {
      const ref = refs.get(id);
      if (ref) mark(visibleTargets(ref), AMBER);
    }

    const left = unanswered.size;
    finished = true;
    setBusy(false);
    removeButton();
    showBanner("Filled by jobsweep — review before submitting. " +
      (left === 1 ? "1 field needs you." : left + " fields need you."), lines, false);
  }

  // ---------- start ----------

  function hasForm() {
    for (const el of document.querySelectorAll("input, textarea, select")) {
      const k = kind(el);
      if (k && k !== "file" && visibleField(el, k)) return true;
    }
    return false;
  }

  function tooSmall() {
    // Captcha and widget frames hold an input or two; skip tiny frames.
    return window !== window.top && (window.innerWidth < 200 || window.innerHeight < 100);
  }

  async function check() {
    if (finished || button) return;
    if (!uid) uid = await findUid();
    if (uid && !tooSmall() && hasForm()) showButton();
  }

  check().then(() => setTimeout(check, 3000));
})();
