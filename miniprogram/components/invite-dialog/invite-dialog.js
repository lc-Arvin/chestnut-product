const access = require("../../services/access");
Component({
  properties: { visible: Boolean },
  data: { code: "", busy: false, error: "" },
  methods: {
    input(event) { this.setData({ code: event.detail.value, error: "" }); },
    cancel() { this.attempt = (this.attempt || 0) + 1; this.setData({ code: "", error: "", busy: false }); this.triggerEvent("cancel"); },
    async submit() {
      if (this.data.busy) return;
      if (!this.data.code.trim()) { this.setData({ error: "Enter your invitation code." }); return; }
      const attempt = this.attempt = (this.attempt || 0) + 1;
      this.setData({ busy: true, error: "" });
      try {
        await access.login(this.data.code.trim());
        if (attempt !== this.attempt) return;
        this.setData({ code: "", busy: false });
        this.triggerEvent("verified");
      } catch (error) {
        if (attempt === this.attempt) this.setData({ busy: false, error: error?.status === 401 ? "Invalid invitation code. Please try again."
          : error?.status === 429 ? "Too many attempts. Please wait and try again."
          : "Unable to verify your code. Please try again." });
      }
    },
  },
});
