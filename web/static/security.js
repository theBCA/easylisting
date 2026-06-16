// Domain-aware favicon: kolaylistele.com keeps the "K" tile, every other
// host (easylisting.app) gets the "E" tile. Both Railway services serve the
// same static files, so this has to be decided client-side by hostname.
(function () {
  if (!/kolaylistele/.test(window.location.hostname)) {
    var icon = document.querySelector('link[rel="icon"][type="image/svg+xml"]');
    if (icon) icon.href = "/static/favicon-e.svg";
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
