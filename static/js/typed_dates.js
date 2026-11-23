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

  window.tbiTimeInput = function () {
    return {
      // Only digits can be typed (the colon is written for you).
      keydown: function (event) {
        if (event.ctrlKey || event.metaKey || event.altKey || ALLOWED_KEYS.indexOf(event.key) >= 0) return;
        if (!/^\d$/.test(event.key)) event.preventDefault();
      },
      // 730 -> 07:30, 1430 -> 02:30 + PM, 0015 -> 12:15 + AM. A pasted "7:30 pm" keeps its PM.
      format: function () {
        var raw = this.$refs.text.value, digits = raw.replace(/\D/g, "").slice(0, 4);
        if (/p/i.test(raw)) this.$refs.period.value = "PM";
        else if (/a/i.test(raw)) this.$refs.period.value = "AM";
        if (digits.length < 3) { this.$refs.text.value = digits; return; }
        var hours = +digits.slice(0, digits.length - 2), minutes = digits.slice(-2);
        if (digits.length === 4 && (hours > 12 && hours < 24 || hours === 0)) {
          this.$refs.period.value = hours >= 12 ? "PM" : "AM";
          hours = hours % 12 || 12;
        }
        this.$refs.text.value = String(hours).padStart(2, "0") + ":" + minutes;
      },
      pad: function () {
        var digits = this.$refs.text.value.replace(/\D/g, "");
        if (digits.length === 1 || digits.length === 2) this.$refs.text.value = digits.padStart(2, "0") + ":00";
      },
    };
  };
})();
