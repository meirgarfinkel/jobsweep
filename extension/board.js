// Runs on the jobsweep board. Marks the page so the board knows the
// extension is active, and relays the "Apply for me" button: the board posts
// {jobsweep: "open", uid, url} to its own window; answering "opened" within
// 300 ms tells it not to open the tab itself.
// The manifest matches every 127.0.0.1 port (Firefox match patterns and
// ports don't mix reliably), so act only on the board's own port.
if (location.port === "8765") {
document.documentElement.setAttribute("data-jobsweep-ext", "1");

window.addEventListener("message", (e) => {
  if (e.origin !== location.origin) return;
  const d = e.data;
  if (!d || d.jobsweep !== "open") return;
  const uid = d.uid, url = d.url;
  if (typeof uid !== "string" || typeof url !== "string") return;
  window.postMessage({ jobsweep: "opened" }, location.origin);
  browser.runtime.sendMessage({ type: "open", uid, url }).catch(() => {});
});
}
