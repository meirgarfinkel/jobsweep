// Keeps tab -> job uid and talks to the jobsweep board on the content
// scripts' behalf. Fetching from here sends Origin moz-extension://..., which
// is the only origin the board's /api/fill and /api/cv answer, and keeps the
// job site's CSP out of the way.
const BOARD = "http://127.0.0.1:8765";
const DOWN = "jobsweep board isn't running: start it with python jobsweep.py --serve, then click Fill again.";

const tabJob = new Map();

browser.tabs.onRemoved.addListener((tabId) => tabJob.delete(tabId));

function okUid(uid) {
  return typeof uid === "string" && uid.length > 0 && uid.length <= 200;
}

async function openJob(msg, sender) {
  if (!sender.url || !sender.url.startsWith(BOARD + "/")) return { ok: false };
  if (!okUid(msg.uid)) return { ok: false };
  let u;
  try {
    u = new URL(msg.url);
  } catch (e) {
    return { ok: false };
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return { ok: false };
  const opts = { url: u.href };
  if (sender.tab) opts.openerTabId = sender.tab.id;
  const tab = await browser.tabs.create(opts);
  tabJob.set(tab.id, msg.uid);
  return { ok: true };
}

async function readError(r) {
  try {
    const d = await r.json();
    if (d && d.error) return String(d.error);
  } catch (e) { /* not JSON */ }
  if (r.status === 403) return "The jobsweep board refused the request (403).";
  return "The jobsweep board answered " + r.status + ".";
}

async function fill(uid, url, fields) {
  let r;
  try {
    r = await fetch(BOARD + "/api/fill", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ uid, url, fields }),
    });
  } catch (e) {
    return { error: DOWN };
  }
  if (!r.ok) return { error: await readError(r) };
  try {
    return await r.json();
  } catch (e) {
    return { error: "The jobsweep board's answer couldn't be read." };
  }
}

async function cv() {
  let r;
  try {
    r = await fetch(BOARD + "/api/cv");
  } catch (e) {
    return { error: DOWN };
  }
  if (!r.ok) return { error: await readError(r) };
  const data = await r.arrayBuffer();
  return { name: r.headers.get("x-filename") || "cv.pdf", data };
}

browser.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || typeof msg.type !== "string") return undefined;
  const tabId = sender.tab ? sender.tab.id : undefined;
  switch (msg.type) {
    case "open":
      return openJob(msg, sender);
    case "register":
      // The #jobsweep=<uid> fallback, read by the top frame of the tab.
      if (tabId !== undefined && sender.frameId === 0 && okUid(msg.uid)) {
        tabJob.set(tabId, msg.uid);
      }
      return Promise.resolve({ uid: tabJob.get(tabId) || null });
    case "whoami":
      return Promise.resolve({ uid: tabJob.get(tabId) || null });
    case "fill": {
      const uid = tabJob.get(tabId);
      if (!uid) return Promise.resolve({ error: "This tab isn't linked to a jobsweep job any more." });
      const url = (sender.tab && sender.tab.url) || msg.url || "";
      return fill(uid, url, Array.isArray(msg.fields) ? msg.fields : []);
    }
    case "cv":
      if (!tabJob.has(tabId)) return Promise.resolve({ error: "This tab isn't linked to a jobsweep job." });
      return cv();
    default:
      return undefined;
  }
});
