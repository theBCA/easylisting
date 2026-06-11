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
