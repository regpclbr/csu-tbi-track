/* The shared chat box (templates/components/chat_box.html, ASSUMPTIONS 439): used by the AI Assistant and (Part 2) the
   report generator. Behaviour only, never a rule (the server checks the length and refuses an empty message):
   - the box grows with the text from its CSS min-height (one line, ASSUMPTIONS 441) up to its CSS max-height (about
     5 lines), then scrolls inside; an empty box is always one line;
   - Enter sends, Shift + Enter is a new line, Ctrl / Cmd + Enter also sends; on phones (touch, no hover) Enter is a new
     line and the Send button sends;
   - a character counter appears near the limit.
   A page mixes tbiChatBox.state() into its Alpine x-data and provides `busy` (true while waiting for an answer). */
(function () {
  "use strict";

  function isTouch() {
    return window.matchMedia("(pointer: coarse)").matches && !window.matchMedia("(hover: hover)").matches;
  }

  function resize(el) {
    if (!el) return;
    if (!el.value) {  // empty: one line (a long placeholder wraps on phones and would count in scrollHeight)
      el.style.height = "";
      el.style.overflowY = "hidden";
      return;
    }
    const style = window.getComputedStyle(el);
    const borders = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const max = parseFloat(style.maxHeight) || Infinity;
    el.style.height = "auto"; // measure the text, not the old height
    const wanted = el.scrollHeight + borders;
    el.style.height = Math.min(wanted, max) + "px";
    el.style.overflowY = wanted > max ? "auto" : "hidden";
  }

  function wantsSend(event) {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return false;
    if (event.ctrlKey || event.metaKey) return true;
    if (event.shiftKey || event.altKey) return false;
    return !isTouch();
  }

  function state() {
    return {
      hasText: false,
      length: 0,
      checkText() {
        const box = this.$refs.question;
        this.length = box.value.length;
        this.hasText = box.value.trim() !== "";
        resize(box);
      },
      enterKey(event) {
        if (!wantsSend(event)) return;
        event.preventDefault();
        if (!this.busy && this.hasText) event.target.form.requestSubmit();
      },
      clearBox() {
        const box = this.$refs.question;
        box.value = "";
        this.checkText();
      },
    };
  }

  window.tbiChatBox = { isTouch: isTouch, resize: resize, wantsSend: wantsSend, state: state };
  window.addEventListener("resize", function () {
    document.querySelectorAll("textarea.chat-box-input").forEach(resize);
  });
})();
