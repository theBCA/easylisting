// Domain-aware favicon: kolaylistele.com keeps the "K" tile, every other
// host (easylisting.app) gets the "E" tile. Both Railway services serve the
// same static files, so this has to be decided client-side by hostname.
(function () {
  function swapFavicon() {
    if (/kolaylistele/.test(window.location.hostname)) return; // keep the "K" tile
    var icon = document.querySelector('link[rel="icon"][type="image/svg+xml"]');
    if (icon) icon.href = "/static/favicon-e.svg";
  }
  // This script loads in <head> *before* the favicon <link> is parsed, so the
  // element won't exist yet — wait for the DOM before querying for it.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", swapFavicon);
  } else {
    swapFavicon();
  }
})();

(function () {
  var _fetch = window.fetch;
  window.fetch = function (url, opts) {
    opts = opts || {};
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
      opts.headers = Object.assign({ "X-CSRFToken": meta.content }, opts.headers || {});
    }
    return _fetch(url, opts);
  };
})();
