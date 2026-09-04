/* Progressive enhancement: live filtering with zero external dependencies.
   Without JS the form is a plain GET that reloads the page — fully functional.
   With JS, changes fetch just the results fragment and swap it in, and the URL
   is kept in sync so results are shareable/bookmarkable. */
(function () {
  "use strict";
  var form = document.getElementById("filters");
  if (!form || !window.fetch || !window.history) return;

  var col = document.querySelector(".results-col");
  var debounceTimer = null;
  var inFlight = null;

  function currentQuery() {
    var data = new FormData(form);
    var params = new URLSearchParams();
    data.forEach(function (v, k) {
      if (v !== "" && v != null) params.append(k, v);
    });
    return params;
  }

  function apply(pushUrl) {
    var params = currentQuery();
    params.set("partial", "1");
    var url = "/?" + params.toString();
    if (inFlight) inFlight.abort();
    inFlight = new AbortController();
    document.body.classList.add("is-loading");

    fetch(url, { signal: inFlight.signal, headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        col.innerHTML = html;
        document.body.classList.remove("is-loading");
        if (pushUrl) {
          params.delete("partial");
          var clean = params.toString();
          history.replaceState(null, "", clean ? "/?" + clean : "/");
        }
      })
      .catch(function (e) {
        if (e.name !== "AbortError") document.body.classList.remove("is-loading");
      });
  }

  // The sort <select> lives inside the form, so the delegated change handler
  // below catches it too — even after the results fragment is swapped.
  form.addEventListener("change", function (e) {
    if (e.target && e.target.name === "name") return; // save-search name field
    apply(true);
  });

  form.addEventListener("input", function (e) {
    var t = e.target;
    if (!t || (t.type !== "search" && t.type !== "text" && t.type !== "number")) return;
    if (t.name === "name") return;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () { apply(true); }, 300);
  });

  // Prevent full submit; let the fetch flow handle it.
  form.addEventListener("submit", function (e) {
    if (e.submitter && e.submitter.formAction && e.submitter.formAction.indexOf("saved-searches") !== -1) {
      return; // allow the save-search POST to submit normally
    }
    e.preventDefault();
    apply(true);
  });
})();
