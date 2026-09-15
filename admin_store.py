"""Local invitation management and privacy-conscious operational records.

SQLite is deliberately opt-in and separate from transcript storage. No audio,
transcript text, attempted invitation secrets, or raw IP addresses are recorded.
"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CHINA = timezone(timedelta(hours=8))
PASSWORD_ROUNDS = 600_000
RETENTION_DAYS = 90
EXPIRY_PRESETS = {"1h": 3600, "4h": 14400, "12h": 43200, "24h": 86400, "7d": 604800}


def day_start(timestamp):
    return datetime.fromtimestamp(timestamp, CHINA).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def integer(value, name, minimum=0, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name}必须是 {minimum}–{maximum} 之间的整数")
    return value


def expiry(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("有效期格式不正确")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        timestamp = parsed.timestamp()
        if timestamp <= time.time():
            raise ValueError()
        return timestamp
    except (ValueError, OverflowError):
        raise ValueError("有效期须为带时区的未来时间") from None


class AdminStore:
    dialect = "sqlite"

    def __init__(self, path, invitations=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        # SQLite backup includes committed WAL data. Keep one pre-migration
        # snapshot; never replace an existing backup or delete historical codes.
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "events" in tables and "settings" in tables and not self.setting("activity_compaction_v1"):
            backup = self.path.with_name(self.path.name + ".before-activity-v1.bak")
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as destination:
                    self.db.backup(destination)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS codes (
                id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'generated',
                enabled INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, expires_at REAL, max_uses INTEGER NOT NULL DEFAULT 0,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, created_at REAL NOT NULL, kind TEXT NOT NULL,
                code_id TEXT NOT NULL DEFAULT '', client TEXT NOT NULL DEFAULT '',
                network TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '', meeting TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS events_time ON events(created_at);
            CREATE INDEX IF NOT EXISTS events_code_time ON events(code_id, created_at);
            CREATE INDEX IF NOT EXISTS events_network_time ON events(network, created_at);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                code_id TEXT NOT NULL, subject TEXT NOT NULL, first_at REAL NOT NULL,
                last_at REAL NOT NULL, occurrences INTEGER NOT NULL, detail TEXT NOT NULL,
                acknowledged_at REAL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_visits (
                day TEXT NOT NULL, client TEXT NOT NULL, channel TEXT NOT NULL,
                visits INTEGER NOT NULL, last_at REAL NOT NULL,
                PRIMARY KEY(day,client,channel)
            );
        """)
        with self.db:
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
            if "occurrences" not in columns:
                self.db.execute("ALTER TABLE events ADD COLUMN occurrences INTEGER NOT NULL DEFAULT 1")
            if "last_at" not in columns:
                self.db.execute("ALTER TABLE events ADD COLUMN last_at REAL")
            self.db.execute("CREATE INDEX IF NOT EXISTS events_compaction ON events(kind,code_id,client,meeting,channel,created_at)")
            if not self.setting("activity_compaction_v1"):
                self._compact_legacy_events()
                self.db.execute("INSERT INTO settings VALUES ('activity_compaction_v1','1')")
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('signing_secret', ?)", (secrets.token_urlsafe(48),))
            # A code imported before must never be re-enabled by restarting.
            for label, code in invitations:
                self.db.execute("""INSERT OR IGNORE INTO codes
                    (id,code,label,source,created_at) VALUES (?,?,?,'environment',?)""",
                    (secrets.token_hex(12), code, label, time.time()))
        self.signing_secret = self.setting("signing_secret")
        self.maintenance()

    def _compact_legacy_events(self):
        self.db.execute("""INSERT INTO daily_visits(day,client,channel,visits,last_at)
            SELECT date(created_at,'unixepoch','+8 hours'),client,channel,sum(occurrences),max(created_at)
            FROM events WHERE kind='visit' GROUP BY 1,2,3
            ON CONFLICT(day,client,channel) DO UPDATE SET
            visits=daily_visits.visits+excluded.visits,last_at=max(daily_visits.last_at,excluded.last_at)""")
        self.db.execute("DELETE FROM events WHERE kind IN ('visit','meeting_ended')")
        groups = self.db.execute("""SELECT min(id) keep_id,group_concat(id) ids,sum(occurrences) total,
            max(coalesce(last_at,created_at)) last_at FROM events WHERE kind='meeting_started'
            GROUP BY date(created_at,'unixepoch','+8 hours'),code_id,client,meeting,channel HAVING count(*)>1""").fetchall()
        for group in groups:
            self.db.execute("UPDATE events SET occurrences=?,last_at=? WHERE id=?", (group["total"], group["last_at"], group["keep_id"]))
            self.db.executemany("DELETE FROM events WHERE id=?", [(int(value),) for value in group["ids"].split(",") if int(value) != group["keep_id"]])

    def maintenance(self):
        """Keep today plus 89 previous Beijing calendar days; free pages are reused."""
        cutoff = day_start(time.time()) - (RETENTION_DAYS-1)*86400
        with self.lock, self.db:
            self.db.execute("DELETE FROM events WHERE created_at<?", (cutoff,))
            self.db.execute("DELETE FROM daily_visits WHERE day<?", (datetime.fromtimestamp(cutoff, CHINA).date().isoformat(),))
            self.db.execute("DELETE FROM alerts WHERE last_at<?", (cutoff,))
            self.db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (time.time(),))

    def close(self):
        with self.lock:
            self.db.close()

    def setting(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE `key`=?", (key,)).fetchone()
            return row[0] if row else ""

    def pseudonym(self, value):
        if not value:
            return ""
        return hmac.new(self.signing_secret.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:16]

    def setup(self, password):
        if not isinstance(password, str) or not 12 <= len(password) <= 200:
            raise ValueError("管理员密码须为 12–200 个字符")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ROUNDS).hex()
        with self.lock, self.db:
            if self.setting("password"):
                raise ValueError("管理员已设置，请登录")
            self.db.execute("INSERT INTO settings VALUES ('password', ?)", (f"{salt}:{digest}",))

    def check_password(self, password):
        stored = self.setting("password")
        if not stored or not isinstance(password, str) or len(password) > 200:
            return False
        salt, expected = stored.split(":")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ROUNDS).hex()
        return hmac.compare_digest(actual, expected)

    def new_session(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (time.time(),))
            self.db.execute("INSERT INTO admin_sessions VALUES (?,?,?)",
                            (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + 8 * 3600))
        return token, csrf

    def session(self, token):
        with self.lock:
            row = self.db.execute("SELECT csrf FROM admin_sessions WHERE digest=? AND expires_at>?",
                                  (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return row[0] if row else None

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM admin_sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def code_status(row):
        if not row["enabled"]:
            return "disabled"
        if row["expires_at"] is not None and row["expires_at"] <= time.time():
            return "expired"
        if row["max_uses"] and row["use_count"] >= row["max_uses"]:
            return "exhausted"
        return "active"

    def public_code(self, row):
        item = dict(row)
        raw = item.pop("code")
        item["masked_code"] = "•••••• · " + raw[-4:]
        item["status"] = self.code_status(row)
        item["enabled"] = bool(item["enabled"])
        remaining = row["expires_at"] - time.time() if row["expires_at"] is not None else None
        item["expiry_warning"] = ("urgent" if remaining <= 3600 else "soon") if item["status"] == "active" and remaining is not None and 0 < remaining <= 86400 else ""
        return item

    def create_codes(self, payload):
        label = payload.get("label", "")
        note = payload.get("note", "")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
            raise ValueError("请填写 1–60 个字符的客户标签")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("备注不能超过 500 个字符")
        quantity = integer(payload.get("quantity", 1), "生成数量", 1, 100)
        max_uses = integer(payload.get("max_uses", 0), "验证次数上限")
        preset = payload.get("expiry_preset", "custom")
        if not isinstance(preset, str) or preset not in {"custom", "forever", *EXPIRY_PRESETS}:
            raise ValueError("有效期快捷选项不正确")
        expires_at = (time.time() + EXPIRY_PRESETS[preset]) if preset in EXPIRY_PRESETS else None if preset == "forever" else expiry(payload.get("expires_at"))
        created = []
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for _ in range(quantity):
                for attempt in range(1000):
                    code = str(100000 + secrets.randbelow(900000))
                    if not self.db.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
                        break
                else:
                    raise ValueError("暂时无法分配未使用的六位码，请减少数量后重试")
                code_id = secrets.token_hex(12)
                self.db.execute("""INSERT INTO codes (id,code,label,note,created_at,expires_at,max_uses)
                    VALUES (?,?,?,?,?,?,?)""", (code_id, code, label.strip(), note.strip(), time.time(), expires_at, max_uses))
                self._event("admin_created", code_id=code_id, channel="admin")
                created.append({"id": code_id, "label": label.strip(), "code": code})
        return created

    def update_code(self, code_id, payload):
        if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
            raise ValueError("请指定启用或停用状态")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM codes WHERE id=?", (code_id,)).fetchone()
            if not row:
                raise KeyError(code_id)
            if bool(row["enabled"]) != payload["enabled"]:
                self.db.execute("UPDATE codes SET enabled=?,version=version+1 WHERE id=?", (int(payload["enabled"]), code_id))
                self._event("admin_enabled" if payload["enabled"] else "admin_disabled", code_id=code_id, channel="admin")
            return self.public_code(self.db.execute("SELECT * FROM codes WHERE id=?", (code_id,)).fetchone())

    def reveal(self, code_id):
        with self.lock, self.db:
            row = self.db.execute("SELECT code FROM codes WHERE id=?", (code_id,)).fetchone()
            if not row:
                raise KeyError(code_id)
            self._event("admin_revealed", code_id=code_id, channel="admin")
            return row[0]

    def list_codes(self, search="", status="", page=1, sort="created_desc"):
        page = integer(page, "页码", 1, 100_000)
        if len(search) > 100 or status not in {"", "active", "disabled", "expired", "exhausted"}:
            raise ValueError("筛选条件不正确")
        orders = {"created_desc": "c.created_at DESC,c.id", "created_asc": "c.created_at ASC,c.id",
                  "expires_asc": "c.expires_at IS NULL,c.expires_at ASC,c.created_at DESC,c.id",
                  "expires_desc": "c.expires_at IS NULL,c.expires_at DESC,c.created_at DESC,c.id"}
        if sort not in orders:
            raise ValueError("排序方式不正确")
        clauses, params = [], []
        if search:
            clauses.append("(instr(lower(c.label),lower(?))>0 OR instr(lower(c.code),lower(?))>0 OR instr(lower(c.note),lower(?))>0)")
            params.extend([search] * 3)
        if status == "disabled":
            clauses.append("c.enabled=0")
        elif status:
            clauses.append("c.enabled=1")
            if status == "expired":
                clauses.append("c.expires_at<=?")
            else:
                clauses.append("(c.expires_at IS NULL OR c.expires_at>?)")
                clauses.append("c.max_uses>0 AND c.use_count>=c.max_uses" if status == "exhausted" else "(c.max_uses=0 OR c.use_count<c.max_uses)")
            params.append(time.time())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.lock:
            total = self.db.execute("SELECT count(*) FROM codes c" + where, params).fetchone()[0]
            rows = self.db.execute("""SELECT c.*,
                (SELECT count(DISTINCT client) FROM events e WHERE e.code_id=c.id AND kind='invite_success') AS clients,
                (SELECT count(*) FROM events e WHERE e.code_id=c.id AND kind='invite_failure') AS failures
                FROM codes c""" + where + " ORDER BY " + orders[sort] + " LIMIT 25 OFFSET ?", [*params, (page-1)*25]).fetchall()
            return {"items": [self.public_code(row) for row in rows], "total": total, "page": page, "page_size": 25}

    def token_valid(self, code_id, version):
        # A used-up code cannot be redeemed again, but its issued credentials
        # remain usable. Disabling or expiry revokes access; re-enable does not
        # revive old tokens because the version changed.
        with self.lock:
            row = self.db.execute("SELECT enabled,version,expires_at FROM codes WHERE id=?", (code_id,)).fetchone()
            return bool(row and row["enabled"] and row["version"] == version and
                        (row["expires_at"] is None or row["expires_at"] > time.time()))

    def invitation_expiry(self, code_id):
        with self.lock:
            row = self.db.execute("SELECT expires_at FROM codes WHERE id=?", (code_id,)).fetchone()
            return row["expires_at"] if row else None

    def _event(self, kind, code_id="", client="", network="", channel="", reason="", meeting=""):
        now = time.time()
        if kind == "visit":
            day = datetime.fromtimestamp(now, CHINA).date().isoformat()
            upsert = ("ON DUPLICATE KEY UPDATE visits=visits+1,last_at=VALUES(last_at)" if self.dialect == "mysql" else
                      "ON CONFLICT(day,client,channel) DO UPDATE SET visits=visits+1,last_at=excluded.last_at")
            self.db.execute("INSERT INTO daily_visits VALUES (?,?,?,1,?) " + upsert, (day, client, channel, now))
            return
        if kind == "meeting_ended":
            return
        if kind in {"meeting_started", "admin_revealed"}:
            cutoff = day_start(now) if kind == "meeting_started" else now-600
            existing = self.db.execute("""SELECT id FROM events WHERE kind=? AND code_id=? AND client=? AND meeting=?
                AND channel=? AND created_at>=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (kind, code_id, client, meeting, channel, cutoff)).fetchone()
            if existing:
                self.db.execute("UPDATE events SET occurrences=occurrences+1,last_at=?,network=? WHERE id=?", (now, network, existing["id"]))
                return
        self.db.execute("INSERT INTO events (created_at,kind,code_id,client,network,channel,reason,meeting) VALUES (?,?,?,?,?,?,?,?)",
                        (now, kind, code_id, client, network, channel, reason, meeting))

    def record(self, kind, code_id="", client="", ip="", channel="", reason="", meeting=""):
        with self.lock, self.db:
            self._event(kind, code_id, self.pseudonym(client), self.pseudonym(ip), channel, reason, self.pseudonym(meeting))
            if kind in {"invite_rate_limited", "admin_login_failure"}:
                network = self.pseudonym(ip)
                count = self.db.execute("SELECT count(*) FROM events WHERE kind=? AND network=? AND created_at>?",
                                        (kind, network, time.time()-600)).fetchone()[0]
                if count >= 3:
                    self._alert(kind, "", network, 600, f"10 分钟内出现 {count} 次" + ("验证限流" if kind == "invite_rate_limited" else "管理员登录失败"))

    def _alert(self, kind, code_id, subject, window, detail):
        now = time.time()
        fingerprint = f"{kind}:{code_id}:{subject}:{int(now//window)}"
        upsert = ("ON DUPLICATE KEY UPDATE last_at=VALUES(last_at),occurrences=occurrences+1,detail=VALUES(detail),acknowledged_at=NULL"
                  if self.dialect == "mysql" else "ON CONFLICT(fingerprint) DO UPDATE SET "
                  "last_at=excluded.last_at,occurrences=alerts.occurrences+1,detail=excluded.detail,acknowledged_at=NULL")
        self.db.execute("""INSERT INTO alerts (fingerprint,kind,code_id,subject,first_at,last_at,occurrences,detail)
            VALUES (?,?,?,?,?,?,1,?) """ + upsert,
            (fingerprint, kind, code_id, subject, now, now, detail))

    def login_failures(self, ip):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM events WHERE kind='admin_login_failure' AND network=? AND created_at>?",
                                   (self.pseudonym(ip), time.time()-600)).fetchone()[0]

    def redeem(self, candidate, client, ip, channel):
        if not isinstance(candidate, str) or len(candidate) > 200:
            candidate = ""
        client, network = self.pseudonym(client), self.pseudonym(ip)
        with self.lock, self.db:
            # Serialize quota checks and increments, including other processes.
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM codes WHERE code=?", (candidate.strip(),)).fetchone()
            status = self.code_status(row) if row else "unknown"
            code_id = row["id"] if row else ""
            if status != "active":
                self._event("invite_failure", code_id, client, network, channel, status)
                count = self.db.execute("SELECT count(*) FROM events WHERE kind='invite_failure' AND network=? AND created_at>?",
                                        (network, time.time()-600)).fetchone()[0]
                if count >= 5:
                    self._alert("repeated_failures", "", network, 600, f"同一网络来源 10 分钟内验证失败 {count} 次")
                if row:
                    rejected = self.db.execute("SELECT count(*) FROM events WHERE kind='invite_failure' AND code_id=? AND created_at>?",
                                               (code_id, time.time()-600)).fetchone()[0]
                    if rejected >= 3:
                        self._alert("unavailable_code", code_id, "", 600, f"不可用邀请码在 10 分钟内被尝试 {rejected} 次")
                return None
            self.db.execute("UPDATE codes SET use_count=use_count+1,last_used_at=? WHERE id=?", (time.time(), code_id))
            self._event("invite_success", code_id, client, network, channel)
            count = self.db.execute("SELECT count(DISTINCT client) FROM events WHERE kind='invite_success' AND code_id=? AND created_at>?",
                                    (code_id, time.time()-86400)).fetchone()[0]
            if count >= 5:
                self._alert("shared_code", code_id, "", 86400, f"同一码 24 小时内被 {count} 个匿名客户端验证成功；可能为正常团队使用，请核实")
            return dict(row)

    def events(self, code_id="", page=1, kind=""):
        page = integer(page, "页码", 1, 100_000)
        clauses, params = [], []
        if code_id:
            clauses.append("e.code_id=?")
            params.append(code_id)
        if kind:
            clauses.append("e.kind=?")
            params.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.lock:
            total = self.db.execute("SELECT count(*) FROM events e" + where, params).fetchone()[0]
            rows = self.db.execute("SELECT e.*,coalesce(c.label,'') label,coalesce(substr(c.code,-4),'') code_suffix FROM events e LEFT JOIN codes c ON c.id=e.code_id" + where +
                                   " ORDER BY coalesce(e.last_at,e.created_at) DESC,e.id DESC LIMIT 30 OFFSET ?", [*params, (page-1)*30]).fetchall()
            return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": 30}

    def alerts(self, state="open", page=1):
        page = integer(page, "页码", 1, 100_000)
        if state not in {"open", "acknowledged", "all"}:
            raise ValueError("异常状态不正确")
        where = {"open": " WHERE a.acknowledged_at IS NULL", "acknowledged": " WHERE a.acknowledged_at IS NOT NULL", "all": ""}[state]
        with self.lock:
            total = self.db.execute("SELECT count(*) FROM alerts a" + where).fetchone()[0]
            rows = self.db.execute("SELECT a.*,coalesce(c.label,'') label,coalesce(substr(c.code,-4),'') code_suffix FROM alerts a LEFT JOIN codes c ON c.id=a.code_id" + where +
                                   " ORDER BY a.last_at DESC,a.id DESC LIMIT 25 OFFSET ?", ((page-1)*25,)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": 25}

    def acknowledge(self, alert_id):
        with self.lock, self.db:
            if not self.db.execute("UPDATE alerts SET acknowledged_at=? WHERE id=?", (time.time(), alert_id)).rowcount:
                raise KeyError(alert_id)
            self._event("admin_acknowledged", channel="admin", reason=f"alert:{alert_id}")

    def overview(self, start="", end=""):
        today = datetime.now(CHINA).date()
        try:
            end_day = date.fromisoformat(end) if end else today
            start_day = date.fromisoformat(start) if start else end_day - timedelta(days=6)
        except ValueError:
            raise ValueError("日期格式不正确") from None
        if not today-timedelta(days=RETENTION_DAYS-1) <= start_day <= end_day <= today:
            raise ValueError("请选择最近 90 天内的日期范围")
        start_at = datetime.combine(start_day, datetime.min.time(), CHINA).timestamp()
        end_at = datetime.combine(end_day+timedelta(days=1), datetime.min.time(), CHINA).timestamp()
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE created_at>=? AND created_at<? AND channel!='admin'", (start_at, end_at)).fetchall()
            daily = {}
            for offset in range((end_day-start_day).days+1):
                day = (start_day+timedelta(days=offset)).isoformat()
                daily[day] = {"date": day, "visits": 0, "visitors": set(), "successes": 0, "failures": 0, "meetings": set()}
            visitors, users, meetings = set(), set(), set()
            usage = {}
            for visit in self.db.execute("SELECT * FROM daily_visits WHERE day>=? AND day<=?", (start_day.isoformat(), end_day.isoformat())):
                daily[visit["day"]]["visits"] += visit["visits"]
                daily[visit["day"]]["visitors"].add(visit["client"])
                visitors.add(visit["client"])
            for row in rows:
                item = daily[datetime.fromtimestamp(row["created_at"], CHINA).date().isoformat()]
                if row["kind"] == "invite_success":
                    item["successes"] += 1
                    users.add(row["client"])
                if row["kind"] in {"invite_failure", "invite_rate_limited"}:
                    item["failures"] += 1
                if row["kind"] == "meeting_started":
                    key = (row["client"], row["meeting"])
                    meetings.add(key)
                    item["meetings"].add(key)
                if row["code_id"] and row["kind"] in {"invite_success", "invite_failure", "meeting_started"}:
                    bucket = usage.setdefault(row["code_id"], {"code_id": row["code_id"], "successes": 0, "failures": 0, "clients": set(), "meetings": set()})
                    if row["kind"] == "invite_success":
                        bucket["successes"] += 1
                        bucket["clients"].add(row["client"])
                    elif row["kind"] == "invite_failure":
                        bucket["failures"] += 1
                    else:
                        bucket["meetings"].add((row["client"], row["meeting"]))
            labels = {row["id"]: (row["label"], row["code"][-4:]) for row in self.db.execute("SELECT id,label,code FROM codes")}
            for bucket in usage.values():
                label, suffix = labels.get(bucket["code_id"], ("", ""))
                bucket.update(label=label, code_suffix=suffix, clients=len(bucket["clients"]), meetings=len(bucket["meetings"]))
            for item in daily.values():
                item["visitors"] = len(item["visitors"])
                item["meetings"] = len(item["meetings"])
            return {"start": start_day.isoformat(), "end": end_day.isoformat(), "timezone": "Asia/Shanghai",
                    "visits": sum(d["visits"] for d in daily.values()), "visitors": len(visitors), "users": len(users),
                    "successes": sum(d["successes"] for d in daily.values()), "failures": sum(d["failures"] for d in daily.values()),
                    "meetings": len(meetings), "daily": list(daily.values()),
                    "usage": sorted(usage.values(), key=lambda b: b["successes"], reverse=True),
                    "open_alerts": self.db.execute("SELECT count(*) FROM alerts WHERE acknowledged_at IS NULL").fetchone()[0],
                    "total_codes": len(labels)}
