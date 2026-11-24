// Typed dates and times on the activity form (ASSUMPTIONS 422; templates/components/typed_datetime.html).
// Only helps typing: the server checks every value again (activities/typed_fields.py), and the boxes work without it.
(function () {
  "use strict";
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];

  function formatDate(year, monthIndex, day) {
    return MONTHS[monthIndex] + " " + day + ", " + year;
  }

  // A typed date the server also accepts -> [year, monthIndex, day], or null.
  function readDate(text) {
    var value = (text || "").trim(), match;
    if ((match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(value))) return check(+match[1], +match[2] - 1, +match[3]);
    if ((match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(value))) return check(+match[3], +match[1] - 1, +match[2]);
    if ((match = /^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$/.exec(value))) {
      var months = monthsStarting(match[1]);
      return months.length === 1 && match[1].length >= 3 ? check(+match[3], MONTHS.indexOf(months[0]), +match[2]) : null;
    }
    return null;
  }

  function check(year, monthIndex, day) {
    var date = new Date(year, monthIndex, day);
    return date.getFullYear() === year && date.getMonth() === monthIndex && date.getDate() === day
      ? [year, monthIndex, day] : null;
  }

  function monthsStarting(letters) {
    var start = letters.toLowerCase();
    return MONTHS.filter(function (month) { return month.toLowerCase().indexOf(start) === 0; });
  }

  // Suggestions for what is typed so far: "j" -> January, June, July; "jun 5" -> June 5, <this year>, <next year>.
  function suggestions(text) {
    var match = /^([A-Za-z]+)\.?(?:\s+(\d{1,2})(?:,?\s*(\d{0,4}))?)?\s*$/.exec((text || "").trim());
    if (!match) return [];
    var months = monthsStarting(match[1]);
    if (!match[2]) return months.map(function (month) { return month + " "; });
    var day = +match[2], year = new Date().getFullYear(), years = [year, year + 1];
    if (match[3] && match[3].length === 4) years = [+match[3]];
    else if (match[3]) years = years.filter(function (y) { return String(y).indexOf(match[3]) === 0; });
    var options = [];
    months.forEach(function (month) {
      years.forEach(function (y) {
        if (check(y, MONTHS.indexOf(month), day)) options.push(formatDate(y, MONTHS.indexOf(month), day));
      });
    });
    return options.slice(0, 8);
  }

  window.tbiDateInput = function () {
    return {
      open: false,
      options: [],
      active: -1,
      suggest: function () {
        this.options = suggestions(this.$refs.text.value);
        this.active = -1;
        this.open = this.options.length > 0;
      },
      close: function () { this.open = false; this.active = -1; },
      choose: function (option) {
        this.$refs.text.value = option;
        this.close();
        // A month alone ("June "): keep typing the day; a full date: done.
        if (/\s$/.test(option)) { this.$refs.text.focus(); this.suggest(); }
      },
      keydown: function (event) {
        if (!this.open) { if (event.key === "ArrowDown") this.suggest(); return; }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          var step = event.key === "ArrowDown" ? 1 : -1;
          this.active = (this.active + step + this.options.length) % this.options.length;
        } else if (event.key === "Enter" && this.active >= 0) {
          event.preventDefault();  // choose the suggestion, never submit the form
          this.choose(this.options[this.active]);
        } else if (event.key === "Escape") {
          event.preventDefault();
          this.close();
        }
      },
      // On leaving the box, a readable date is written the same way every time ("June 5, 2026").
      tidy: function () {
        this.close();
        var parts = readDate(this.$refs.text.value);
        if (parts) this.$refs.text.value = formatDate(parts[0], parts[1], parts[2]);
      },
      openCalendar: function () {
        var parts = readDate(this.$refs.text.value), picker = this.$refs.picker;
        if (parts) picker.value = parts[0] + "-" + String(parts[1] + 1).padStart(2, "0") + "-" + String(parts[2]).padStart(2, "0");
        if (typeof picker.showPicker === "function") {
          try { picker.showPicker(); return; } catch (error) { /* not allowed here: fall back below */ }
        }
        picker.focus();
        picker.click();
      },
      picked: function () {
        var parts = readDate(this.$refs.picker.value);
        if (parts) this.$refs.text.value = formatDate(parts[0], parts[1], parts[2]);
        this.$refs.text.focus();
      },
    };
  };

  var ALLOWED_KEYS = ["Backspace", "Delete", "Tab", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", "Escape"];

  // The time box (ASSUMPTIONS 422, 432): nothing is rewritten while typing, except that a 4th digit in a row adds the
  // colon (1100 -> 11:00). Leaving the box tidies it the way the server reads it (activities.typed_fields.TIME_PATTERN):
  // 9 -> 09:00, 130 -> 01:30, 1:30 -> 01:30, 0015 / 00:15 -> 12:15 + AM, 1430 -> 02:30 + PM, "7:30 pm" -> 07:30 + PM.
  // The AM/PM select is changed ONLY by a typed a/p or a 24-hour time; never otherwise (the person's choice stays).
  // Anything unreadable is left as typed: the server's message says what to type.
  var TIME = /^\s*(\d{1,2})(?::?(\d{2}))?\s*([AaPp])?\.?\s*(?:[Mm]\.?)?\s*$/;

  function tidyTime(text) {
    var match = TIME.exec(text || "");
    if (!match) return null;
    var hours = +match[1], minutes = +(match[2] || 0), letter = (match[3] || "").toUpperCase(), period = "";
    if (minutes > 59 || hours > 23) return null;
    if (letter) period = letter + "M";
    if (hours === 0 || hours > 12) {  // a 24-hour time
      if (letter) return null;  // "14:30 am": let the server explain
      period = hours >= 12 ? "PM" : "AM";
      hours = hours % 12 || 12;
    }
    return {text: String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0"), period: period};
  }

  window.tbiTimeInput = function () {
    return {
      // Digits, the colon, a/p/m and a space can be typed (a pasted "7:30 pm" works too).
      keydown: function (event) {
        if (event.ctrlKey || event.metaKey || event.altKey || ALLOWED_KEYS.indexOf(event.key) >= 0) return;
        if (!/^[0-9:aApPmM. ]$/.test(event.key)) event.preventDefault();
      },
      // While typing: only four digits in a row get their colon (1100 -> 11:00, 0140 -> 01:40).
      typed: function (event) {
        if (event && event.inputType && event.inputType.indexOf("delete") === 0) return;  // Backspace / Delete: as is
        var box = this.$refs.text;
        if (/^\d{4}$/.test(box.value)) box.value = box.value.slice(0, 2) + ":" + box.value.slice(2);
      },
      // On leaving the box: the tidy form and, only when typed, the AM/PM.
      tidy: function () {
        var result = tidyTime(this.$refs.text.value);
        if (!result) return;
        this.$refs.text.value = result.text;
        if (result.period) this.$refs.period.value = result.period;
      },
    };
  };

  window.tbiTidyTime = tidyTime;  // for the browser test only
})();
