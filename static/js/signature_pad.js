/*
 * Draw a signature on the screen (ASSUMPTIONS 434): an Alpine component used by templates/public/_signature_field.html.
 * Touch, pen or mouse (Pointer Events). After each stroke the drawing is written into the hidden input
 * <prefix>_drawn as a PNG data URL (transparent background); "Clear" empties both. The server checks and re-encodes
 * it (registrations.signatures); nothing here is a rule. Without JavaScript the participant uploads or types instead.
 * Loaded before Alpine (base.html), self-hosted, no library.
 */
document.addEventListener("alpine:init", () => {
  window.Alpine.data("signaturePad", () => ({
    drawing: false,
    hasInk: false,
    last: null,

    init() {
      this.canvas = this.$refs.canvas;
      this.context = this.canvas.getContext("2d");
      this.resize();
      // Phones fire "resize" when the address bar hides while scrolling: only a real width change clears the box.
      this._onResize = () => {
        if (Math.round(this.canvas.getBoundingClientRect().width) !== this._width) this.resize();
      };
      window.addEventListener("resize", this._onResize);
      // The box is hidden until "Draw it on the screen" is chosen: size the canvas when it appears.
      this.$watch("kind", (value) => { if (value === "draw") this.$nextTick(() => this.resize()); });
    },

    destroy() {
      window.removeEventListener("resize", this._onResize);
    },

    resize() {
      // Keep the drawing sharp on high-density screens. Resizing a canvas wipes it, so a drawing already made (a phone
      // turned sideways) is drawn back, scaled to the new box; the hidden value keeps the original drawing.
      const ratio = Math.max(window.devicePixelRatio || 1, 1);
      const box = this.canvas.getBoundingClientRect();
      if (!box.width) return;
      const saved = this.$refs.value ? this.$refs.value.value : "";
      this._width = Math.round(box.width);
      this.canvas.width = Math.round(box.width * ratio);
      this.canvas.height = Math.round(box.height * ratio);
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.context.lineWidth = 2.5;
      this.context.lineCap = "round";
      this.context.lineJoin = "round";
      this.context.strokeStyle = "#111827";
      if (!saved) {
        this.clear();
        return;
      }
      const image = new Image();
      image.onload = () => this.context.drawImage(image, 0, 0, box.width, box.height);
      image.src = saved;
      this.hasInk = true;
    },

    point(event) {
      const box = this.canvas.getBoundingClientRect();
      return { x: event.clientX - box.left, y: event.clientY - box.top };
    },

    start(event) {
      event.preventDefault();
      this.canvas.setPointerCapture?.(event.pointerId);
      this.drawing = true;
      this.last = this.point(event);
      this.context.beginPath();
      this.context.arc(this.last.x, this.last.y, 1.2, 0, Math.PI * 2);
      this.context.fillStyle = "#111827";
      this.context.fill();
    },

    move(event) {
      if (!this.drawing) return;
      event.preventDefault();
      const next = this.point(event);
      this.context.beginPath();
      this.context.moveTo(this.last.x, this.last.y);
      this.context.lineTo(next.x, next.y);
      this.context.stroke();
      this.last = next;
      this.hasInk = true;
    },

    end() {
      if (!this.drawing) return;
      this.drawing = false;
      this.hasInk = true;
      this.$refs.value.value = this.canvas.toDataURL("image/png");
    },

    clear() {
      this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.hasInk = false;
      if (this.$refs.value) this.$refs.value.value = "";
    },
  }));
});
