// Runs on the jobsweep board. The board's "Apply for me" button posts
// {jobsweep: "open", uid, url} to its own window; answering "opened" within
// 300 ms tells it not to open the tab itself.
window.addEventListener("message", (e) => {
  if (e.source !== window || e.origin !== location.origin) return;
  const d = e.data;
  if (!d || d.jobsweep !== "open") return;
  const uid = d.uid, url = d.url;
  if (typeof uid !== "string" || typeof url !== "string") return;
  window.postMessage({ jobsweep: "opened" }, location.origin);
  browser.runtime.sendMessage({ type: "open", uid, url }).catch(() => {});
});
