/* Render the documented status taxonomy (design/index.md) as badges.
   The markdown stays byte-identical — tests assert on the literal
   "**Status: X.**" strings — so the transform happens client-side. */
(function () {
  var KINDS = {
    "Shipped": "shipped",
    "Experimental/live-only": "experimental",
    "Design—not shipped": "design",
  };

  function decorate(root) {
    var strongs = root.querySelectorAll(".md-typeset p > strong:first-child");
    strongs.forEach(function (el) {
      if (el.dataset.meStatus) return;
      var m = el.textContent.match(/^Status:\s*(.+?)\.?\s*$/);
      if (!m) return;
      var kind = KINDS[m[1].trim()];
      if (!kind) return;
      el.classList.add("me-status", "me-status--" + kind);
      el.dataset.meStatus = kind;
      el.textContent = m[1].trim();
    });
  }

  if (typeof document$ !== "undefined") {
    /* Material's page observable — re-runs after instant navigation. */
    document$.subscribe(function () {
      decorate(document);
    });
  } else {
    decorate(document);
  }
})();

