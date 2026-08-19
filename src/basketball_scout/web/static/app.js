/* Progressive enhancement only.
   The opponent form is a real <form> and works without this file; here it just
   navigates on change and shows a loading state. No data fetching, no state,
   no framework — and no credential of any kind ever reaches the browser. */
(function () {
  "use strict";

  var form = document.getElementById("team-form");
  if (!form) return;

  var select = document.getElementById("team-select");
  var loading = document.getElementById("selector-loading");
  var base = form.getAttribute("data-base") || "/teams/";

  function go() {
    var value = select.value;
    if (!value) return;
    if (loading) loading.hidden = false;
    window.location.href = base + encodeURIComponent(value);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    go();
  });

  select.addEventListener("change", go);
})();
