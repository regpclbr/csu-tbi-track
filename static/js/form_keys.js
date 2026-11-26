/* Enter never submits a long staff form (ASSUMPTIONS 450).
 *
 * A form marked data-enter-submit="off" is submitted only by clicking (or pressing Enter / Space ON) its submit button:
 * pressing Enter while typing in a text box, a date or time box, a number box or a select does nothing. A textarea
 * still makes a new line, and buttons, links and the shared chat box (its own keys, ASSUMPTIONS 441) are untouched.
 * Login, search boxes and the public pages are not marked, so Enter submits them as usual. Display help only: the
 * server checks every value whatever the key.
 */
(function () {
  "use strict";

  var NEVER = { textarea: true, button: true, a: true };
  var PASS_TYPES = { submit: true, button: true, reset: true, image: true, file: true };

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.isComposing || event.defaultPrevented) return;
    var target = event.target;
    if (!target || !target.tagName) return;
    var tag = target.tagName.toLowerCase();
    if (NEVER[tag]) return;
    if (tag === "input" && PASS_TYPES[(target.type || "").toLowerCase()]) return;
    if (target.closest && target.closest("[data-chat-box]")) return;
    var form = target.form || (target.closest && target.closest("form"));
    if (form && form.getAttribute("data-enter-submit") === "off") event.preventDefault();
  });
})();
