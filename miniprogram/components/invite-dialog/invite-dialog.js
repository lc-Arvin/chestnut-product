const access = require("../../services/access");
Component({
  properties: { visible: { type: Boolean, observer(visible) { if (visible) this.refreshTrial(); } }, allowTrial: { type: Boolean, value: true } },
  data: { code: "", busy: false, error: "", trialEnabled: false, trialAllowed: false, trialLabel: "Try free for 3 minutes" },
  methods: {
    async refreshTrial() {
      try {
        const result = await access.status();
        const resumable = result.access_mode === "trial" && result.trial.state !== "ended";
        const allowed = Boolean(result.trial?.available || resumable);
        this.setData({ trialEnabled: Boolean(result.trial?.enabled), trialAllowed: allowed,
          trialLabel: resumable ? "Continue your trial" : allowed ? `Try free for ${Math.ceil(result.trial.duration_seconds / 60)} minutes` : "Trial already used" });
      } catch { this.setData({ trialEnabled: false }); }
    },
    async startTrial() {
      if (this.data.busy || !this.data.trialAllowed) return;
      const attempt = this.attempt = (this.attempt || 0) + 1;
      this.setData({ busy: true, error: "" });
      try {
        await access.startTrial();
        if (attempt !== this.attempt) return;
        this.setData({ busy: false });
        this.triggerEvent("verified");
      } catch (error) {
        if (attempt === this.attempt) {
          this.setData({ busy: false, error: error.status === 409 ? "Your trial has already been used. Enter an invitation code." : "Unable to start the trial. Please try again." });
          await this.refreshTrial();
        }
      }
    },
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
