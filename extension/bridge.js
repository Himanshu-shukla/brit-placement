/* Dashboard bridge: lets the Placement OS dashboard (a local page) start the auto-apply runner.
   The background worker only acts when this page's origin is the configured backend. */
window.addEventListener('message', event => {
  const data = event.data;
  if (event.source !== window || !data || data.source !== 'brit-dashboard' || !data.id) return;
  chrome.runtime.sendMessage({type: 'dashboard', action: data.action, learnerId: data.learnerId}, reply => {
    const error = chrome.runtime.lastError ? chrome.runtime.lastError.message : null;
    window.postMessage({source: 'brit-extension', id: data.id, ...(error ? {error} : reply || {})}, window.location.origin);
  });
});
