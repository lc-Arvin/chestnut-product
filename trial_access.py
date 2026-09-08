"""Short-lived, process-local trial grants. No persistent customer profiles."""
import asyncio
from dataclasses import dataclass, field
import math
import secrets
import time


@dataclass
class Trial:
    subject: str
    duration: int
    issued_at: float
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    meeting_id: str = field(default_factory=lambda: "trial-" + secrets.token_hex(12))
    deadline: float | None = None
    finished_at: float | None = None
    saved: dict | None = None
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def state(self, now):
        if self.finished_at is not None or (self.deadline is not None and now >= self.deadline):
            return "ended"
        if self.deadline is None and now >= self.issued_at + 900:
            return "ended"
        return "active" if self.deadline is not None else "ready"

    def credential_valid(self, now):
        end = self.finished_at or self.deadline or (self.issued_at + 900)
        return now < end + 600  # Allow saving after translation access ends.

    def info(self, now):
        state = self.state(now)
        remaining = self.duration if state == "ready" else max(0, math.ceil((self.deadline or now) - now)) if state == "active" else 0
        return {"state": state, "duration_seconds": self.duration, "remaining_seconds": remaining,
                "ends_at": self.deadline, "server_time": now, "meeting_id": self.meeting_id}


class TrialRegistry:
    def __init__(self, duration=180, clock=time.time):
        self.duration = duration
        self.clock = clock
        self.by_subject = {}
        self.by_id = {}

    def prune(self):
        cutoff = self.clock() - 6 * 3600
        for subject, trial in list(self.by_subject.items()):
            if trial.issued_at < cutoff:
                self.by_subject.pop(subject)
                self.by_id.pop(trial.id, None)

    def claim(self, subject):
        self.prune()
        existing = self.by_subject.get(subject)
        if existing:
            if existing.state(self.clock()) == "ended":
                raise ValueError("Your trial has already been used. Enter an invitation code to continue.")
            return existing
        if len(self.by_id) >= 10000:
            raise OverflowError("Trials are temporarily unavailable. Please use an invitation code.")
        trial = Trial(subject, self.duration, self.clock())
        self.by_subject[subject] = trial
        self.by_id[trial.id] = trial
        return trial

    def get(self, trial_id, subject):
        trial = self.by_id.get(trial_id)
        return trial if trial and trial.subject == subject and trial.credential_valid(self.clock()) else None

    def start(self, trial):
        if trial.state(self.clock()) == "ready":
            trial.deadline = self.clock() + trial.duration

    def finish(self, trial):
        if trial.finished_at is None:
            trial.finished_at = min(self.clock(), trial.deadline or self.clock())


class TrialSocket:
    """Start the clock on upstream readiness and gate every audio frame."""
    def __init__(self, browser, registry, trial):
        self.browser, self.registry, self.trial = browser, registry, trial

    async def send(self, message):
        import json
        event = json.loads(message)
        if event.get("type") == "session.updated" and self.trial.state(self.registry.clock()) == "ready":
            self.registry.start(self.trial)
            await self.browser.send(json.dumps({"type": "trial.status", **self.trial.info(self.registry.clock())}))
        await self.browser.send(message)

    async def close(self):
        await self.browser.close()

    async def __aiter__(self):
        import json
        async for message in self.browser:
            if isinstance(message, bytes):
                state = self.trial.state(self.registry.clock())
                if state == "ended":
                    return
                if state != "active":
                    continue
            else:
                try:
                    if json.loads(message).get("type") == "session.finish":
                        self.registry.finish(self.trial)
                except (ValueError, AttributeError):
                    pass
            yield message


async def enforce_trial(browser, registry, trial):
    import json
    while trial.state(registry.clock()) != "ended":
        await asyncio.sleep(0.1)
    await browser.send(json.dumps({"type": "trial.ended", "message": "Your trial is complete. Enter an invitation code to keep translating."}))
    await browser.close()
