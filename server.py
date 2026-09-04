"""Unified HTTP and WebSocket bridge for Chestnut Conference Console.

The browser sends 16 kHz PCM to this process. This process authenticates with
Alibaba Cloud Model Studio, so the DashScope API key never enters browser code.
The same single-port service runs locally and in WeChat CloudBase Run.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web
from qcloud_cos import CosConfig, CosS3Client
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


HOST = os.environ.get("CHESTNUT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("CHESTNUT_PORT", "8080")))
ROOT = Path(__file__).resolve().parent
MODEL = "qwen3.5-livetranslate-flash-realtime"
MAX_TRANSCRIPT_BYTES = 5_000_000
COS_BUCKET = os.environ.get("CHESTNUT_COS_BUCKET", "").strip()
COS_REGION = os.environ.get("TENCENTCLOUD_REGION", os.environ.get("CHESTNUT_COS_REGION", "")).strip()
LOG_PATH = Path(os.environ.get("CHESTNUT_LOG_FILE", ROOT / "logs" / "chestnut.log"))
CLOUD_SEND_TIMEOUT_SECONDS = float(os.environ.get("CHESTNUT_CLOUD_SEND_TIMEOUT", "10"))
CLOUD_RESPONSE_TIMEOUT_SECONDS = float(os.environ.get("CHESTNUT_CLOUD_RESPONSE_TIMEOUT", "60"))
def safe_invite_label(value, code=""):
    label = "".join(character.lower() for character in str(value or "") if character.isalnum() or character in "-_")
    if label:
        return label[:40]
    digest = hashlib.sha256(str(code or value or "invite").encode()).hexdigest()[:8]
    return f"invite-{digest}"


def parse_web_invitations(value):
    invitations = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            configured_label, code = item.split("=", 1)
            code = code.strip()
            if not code:
                continue
            label = safe_invite_label(configured_label, code)
        else:
            code = item
            label = safe_invite_label("", code)
        invitations.append((label, code))
    return tuple(invitations)


WEB_INVITATIONS = parse_web_invitations(os.environ.get("CHESTNUT_WEB_INVITE_CODES", ""))
AUTH_SECRET = os.environ.get("CHESTNUT_AUTH_SECRET", "").strip()
AUTH_REQUIRED = bool(WEB_INVITATIONS)
WEB_TOKEN_TTL_SECONDS = int(os.environ.get("CHESTNUT_WEB_TOKEN_TTL_SECONDS", "43200"))
MAX_MEETING_SECONDS = int(os.environ.get("CHESTNUT_MAX_MEETING_SECONDS", "3600"))
MEETING_WARNING_SECONDS = int(os.environ.get("CHESTNUT_MEETING_WARNING_SECONDS", "300"))
MAX_CONCURRENT_MEETINGS = int(os.environ.get("CHESTNUT_MAX_CONCURRENT_MEETINGS", "20"))
LOGIN_RATE_LIMIT = int(os.environ.get("CHESTNUT_LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("CHESTNUT_LOGIN_RATE_WINDOW_SECONDS", "600"))
CONNECTION_RATE_LIMIT = int(os.environ.get("CHESTNUT_CONNECTION_RATE_LIMIT", "10"))
CONNECTION_RATE_WINDOW_SECONDS = int(os.environ.get("CHESTNUT_CONNECTION_RATE_WINDOW_SECONDS", "60"))
ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.environ.get("CHESTNUT_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)


@dataclass(frozen=True)
class ClientIdentity:
    subject: str
    kind: str
    label: str = ""


class SlidingWindowLimiter:
    def __init__(self, limit, window_seconds):
        self.limit = max(0, limit)
        self.window_seconds = max(1, window_seconds)
        self.attempts = defaultdict(deque)

    def allow(self, key):
        if not self.limit:
            return True
        now = time.monotonic()
        cutoff = now - self.window_seconds
        entries = self.attempts[key]
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if len(entries) >= self.limit:
            return False
        entries.append(now)
        return True


class MeetingRegistry:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.active = {}

    async def acquire(self, identity, meeting_id, socket):
        session_key = secrets.token_hex(8)
        previous = None
        async with self.lock:
            existing = self.active.get(identity.subject)
            if existing and existing["meeting_id"] != meeting_id:
                return None, None, "This account already has an active meeting."
            if not existing and MAX_CONCURRENT_MEETINGS and len(self.active) >= MAX_CONCURRENT_MEETINGS:
                return None, None, "Chestnut is currently at capacity. Please try again shortly."
            if existing:
                previous = existing["socket"]
            self.active[identity.subject] = {
                "session_key": session_key,
                "meeting_id": meeting_id,
                "socket": socket,
            }
        return session_key, previous, None

    async def release(self, identity, session_key):
        async with self.lock:
            existing = self.active.get(identity.subject)
            if existing and existing["session_key"] == session_key:
                self.active.pop(identity.subject, None)


LOGIN_LIMITER = SlidingWindowLimiter(LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS)
CONNECTION_LIMITER = SlidingWindowLimiter(CONNECTION_RATE_LIMIT, CONNECTION_RATE_WINDOW_SECONDS)
MEETING_REGISTRY = MeetingRegistry()


def configure_logging():
    logger = logging.getLogger("chestnut")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as error:
        logger.warning("event=log_file_unavailable error_type=%s", type(error).__name__)
    return logger


LOGGER = configure_logging()


def base64url_encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def token_signature(payload):
    return base64url_encode(hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).digest())


def issue_access_token(client_id, invite_label="invite"):
    now = int(time.time())
    subject_digest = hmac.new(AUTH_SECRET.encode(), client_id.encode(), hashlib.sha256).hexdigest()[:32]
    payload = base64url_encode(json.dumps({
        "sub": f"web-{subject_digest}",
        "kind": "web",
        "label": safe_invite_label(invite_label),
        "iat": now,
        "exp": now + WEB_TOKEN_TTL_SECONDS,
    }, separators=(",", ":")).encode())
    return f"{payload}.{token_signature(payload)}"


def verify_access_token(token):
    if not token or not AUTH_SECRET:
        return None
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, token_signature(payload)):
            return None
        claims = json.loads(base64url_decode(payload))
        if int(claims.get("exp", 0)) <= int(time.time()):
            return None
        subject = safe_owner_id(claims.get("sub"))
        if not subject or claims.get("kind") != "web":
            return None
        return ClientIdentity(subject=subject, kind="web", label=safe_invite_label(claims.get("label")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def request_ip(request):
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or request.remote or "unknown"


def request_token(request):
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get("chestnut_access", "").strip()


def request_identity(request):
    wechat_openid = request.headers.get("x-wx-openid", "").strip()
    if wechat_openid:
        return ClientIdentity(subject=f"wechat-{safe_owner_id(wechat_openid)}", kind="wechat")
    identity = verify_access_token(request_token(request))
    if identity:
        return identity
    if not AUTH_REQUIRED:
        return ClientIdentity(subject="local-anonymous", kind="local")
    return None


def origin_is_allowed(request):
    origin = request.headers.get("origin", "").strip().rstrip("/")
    if not origin:
        return True
    if ALLOWED_ORIGINS:
        return origin in ALLOWED_ORIGINS
    try:
        return urlsplit(origin).netloc == request.host
    except ValueError:
        return False


def invitation_label(candidate):
    candidate = str(candidate or "")
    matched_label = None
    for label, configured_code in WEB_INVITATIONS:
        if hmac.compare_digest(candidate, configured_code):
            matched_label = label
    return matched_label


class SessionMetrics:
    def __init__(self):
        self.session_id = secrets.token_hex(6)
        self.started_at = time.monotonic()
        self.audio_chunks = 0
        self.audio_bytes = 0
        self.last_voice_at = None
        self.voice_wait_started_at = None
        self.last_cloud_event_at = time.monotonic()
        self.cloud_events = Counter()
        self.request_ids = {}

    def record_audio(self, message):
        self.audio_chunks += 1
        self.audio_bytes += len(message)
        if pcm_has_voice(message):
            self.last_voice_at = time.monotonic()

    def record_cloud_event(self, target_language, event):
        now = time.monotonic()
        self.last_cloud_event_at = now
        self.voice_wait_started_at = None
        event_type = str(event.get("type") or "unknown")
        self.cloud_events[f"{target_language}:{event_type}"] += 1
        request_id = event.get("request_id") or event.get("requestId")
        if request_id:
            self.request_ids[target_language] = str(request_id)[:128]

    def summary(self):
        return (
            f"duration_s={int(time.monotonic() - self.started_at)} "
            f"audio_chunks={self.audio_chunks} audio_bytes={self.audio_bytes} "
            f"cloud_events={sum(self.cloud_events.values())} "
            f"request_zh={self.request_ids.get('zh', '-')} "
            f"request_en={self.request_ids.get('en', '-')}"
        )


def pcm_has_voice(message):
    if len(message) < 2:
        return False
    try:
        samples = memoryview(message)[:len(message) - (len(message) % 2)].cast("h")
        return any(abs(sample) >= 700 for sample in samples[::32])
    except (TypeError, ValueError):
        return False


def transcript_time(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def markdown_quote(text):
    return "\n".join(f"> {line}" if line else ">" for line in str(text).splitlines())


def render_meeting_transcript(payload, filename_label=""):
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")

    now = datetime.now().astimezone()
    if filename_label:
        filename = f"web-{now.strftime('%Y-%m-%d')}-{safe_invite_label(filename_label)}-{now.strftime('%H%M%S')}.md"
    else:
        filename = f"meeting-{now.strftime('%Y%m%d-%H%M%S')}.md"
    started_at = str(payload.get("started_at") or "")
    ended_at = str(payload.get("ended_at") or now.isoformat())
    duration = max(0, int(payload.get("duration_seconds") or 0))
    lines = [
        "---",
        "format: chestnut-meeting-transcript-v1",
        f"started_at: {json.dumps(started_at)}",
        f"ended_at: {json.dumps(ended_at)}",
        f"duration_seconds: {duration}",
        f"entries: {len(entries)}",
        "---",
        "",
        "# Chestnut Meeting Transcript",
        "",
        f"Duration: {transcript_time(duration)}",
        "",
    ]

    if not entries:
        lines.extend(["_No completed captions were recorded._", ""])
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("text", "")).strip():
            continue
        language = "中文" if entry.get("language") == "zh" else "English"
        role = "ORIGINAL" if entry.get("role") == "original" else "TRANSLATION"
        timestamp = transcript_time(entry.get("time_seconds", 0))
        lines.extend([
            f"## {timestamp} · {language} · {role}",
            "",
            markdown_quote(str(entry["text"]).strip()),
            "",
        ])

    return filename, "\n".join(lines)


def safe_owner_id(value):
    owner = "".join(character for character in str(value or "") if character.isalnum() or character in "-_")
    return owner[:128] or "anonymous"


def save_local_transcript(filename, content, owner_id):
    owner = safe_owner_id(owner_id)
    meetings_dir = ROOT / "meetings" if owner == "local-anonymous" else ROOT / "meetings" / owner
    meetings_dir.mkdir(parents=True, exist_ok=True)
    output_path = meetings_dir / filename
    suffix = 2
    while output_path.exists():
        output_path = meetings_dir / filename.replace(".md", f"-{suffix}.md")
        suffix += 1
    temporary_path = output_path.with_suffix(".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(output_path)
    return {
        "filename": output_path.name,
        "storage": "local",
        "url": f"/meetings/{owner}/{output_path.name}",
    }


def save_cos_transcript(filename, content, owner_id):
    secret_id = os.environ.get("TENCENTCLOUD_SECRETID") or os.environ.get("CHESTNUT_COS_SECRET_ID", "")
    secret_key = os.environ.get("TENCENTCLOUD_SECRETKEY") or os.environ.get("CHESTNUT_COS_SECRET_KEY", "")
    token = os.environ.get("TENCENTCLOUD_SESSIONTOKEN") or os.environ.get("CHESTNUT_COS_SESSION_TOKEN", "")
    if not COS_REGION:
        raise RuntimeError("TENCENTCLOUD_REGION is unavailable; configure CHESTNUT_COS_REGION")
    if not secret_id or not secret_key:
        raise RuntimeError("COS credentials are unavailable; configure a least-privilege COS service account")

    config = CosConfig(
        Region=COS_REGION,
        SecretId=secret_id,
        SecretKey=secret_key,
        Token=token or None,
        Scheme="https",
    )
    object_key = f"meetings/{safe_owner_id(owner_id)}/{filename}"
    CosS3Client(config).put_object(
        Bucket=COS_BUCKET,
        Key=object_key,
        Body=content.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    return {"filename": filename, "storage": "cos", "object_key": object_key}


def save_meeting_transcript(payload, owner_id="", filename_label=""):
    filename, content = render_meeting_transcript(payload, filename_label)
    if COS_BUCKET:
        return save_cos_transcript(filename, content, owner_id)
    return save_local_transcript(filename, content, owner_id)


class AiohttpSocket:
    """Small compatibility wrapper used by the existing relay functions."""

    def __init__(self, socket):
        self.socket = socket

    async def send(self, message):
        if isinstance(message, bytes):
            await self.socket.send_bytes(message)
        else:
            await self.socket.send_str(message)

    async def close(self):
        await self.socket.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.socket.receive()
        if message.type in (WSMsgType.TEXT, WSMsgType.BINARY):
            return message.data
        raise StopAsyncIteration


async def send_cloud(cloud, target_language, payload, metrics):
    try:
        await asyncio.wait_for(cloud.send(payload), timeout=CLOUD_SEND_TIMEOUT_SECONDS)
    except Exception as error:
        LOGGER.error(
            "session=%s event=cloud_send_failed target=%s error_type=%s",
            metrics.session_id,
            target_language,
            type(error).__name__,
        )
        raise


async def relay_browser_audio(browser, clouds, metrics):
    async for message in browser:
        if isinstance(message, bytes):
            metrics.record_audio(message)
            event = {
                "event_id": f"audio_{os.urandom(8).hex()}",
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(message).decode("ascii"),
            }
            payload = json.dumps(event)
            await asyncio.gather(*(
                send_cloud(cloud, target_language, payload, metrics)
                for target_language, cloud in clouds
            ))
            continue

        try:
            event = json.loads(message)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session.finish":
            payload = json.dumps({"event_id": f"finish_{os.urandom(8).hex()}", "type": "session.finish"})
            LOGGER.info("session=%s event=browser_finish_requested", metrics.session_id)
            await asyncio.gather(*(
                send_cloud(cloud, target_language, payload, metrics)
                for target_language, cloud in clouds
            ))
            return True
    return False


async def relay_cloud_events(cloud, browser, target_language, browser_send_lock, metrics):
    async for message in cloud:
        try:
            event = json.loads(message)
            metrics.record_cloud_event(target_language, event)
            if event.get("type") == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else {}
                LOGGER.error(
                    "session=%s event=cloud_error target=%s code=%s request_id=%s",
                    metrics.session_id,
                    target_language,
                    error.get("code") or event.get("code") or "unknown",
                    metrics.request_ids.get(target_language, "-"),
                )
            event["translation_target"] = target_language
            async with browser_send_lock:
                await browser.send(json.dumps(event))
            if event.get("type") == "session.finished":
                LOGGER.info(
                    "session=%s event=cloud_session_finished target=%s",
                    metrics.session_id,
                    target_language,
                )
                return
        except (json.JSONDecodeError, TypeError):
            async with browser_send_lock:
                await browser.send(message)


async def monitor_cloud_responsiveness(metrics):
    next_progress_at = time.monotonic() + 60
    while True:
        await asyncio.sleep(5)
        now = time.monotonic()
        if now >= next_progress_at:
            LOGGER.info(
                "session=%s event=session_progress last_cloud_event_age_s=%d %s",
                metrics.session_id,
                int(now - metrics.last_cloud_event_at),
                metrics.summary(),
            )
            next_progress_at = now + 60
        voice_is_recent = metrics.last_voice_at is not None and now - metrics.last_voice_at <= 6
        if not voice_is_recent:
            metrics.voice_wait_started_at = None
            continue
        if metrics.voice_wait_started_at is None:
            metrics.voice_wait_started_at = now
            continue
        waiting_seconds = now - metrics.voice_wait_started_at
        if waiting_seconds >= CLOUD_RESPONSE_TIMEOUT_SECONDS:
            LOGGER.error(
                "session=%s event=cloud_response_timeout waiting_s=%d last_event_age_s=%d",
                metrics.session_id,
                int(waiting_seconds),
                int(now - metrics.last_cloud_event_at),
            )
            raise TimeoutError("Bailian stopped responding while speech audio was still arriving")


async def enforce_meeting_duration(browser, metrics):
    warning_seconds = min(MEETING_WARNING_SECONDS, MAX_MEETING_SECONDS)
    if warning_seconds > 0:
        await asyncio.sleep(MAX_MEETING_SECONDS - warning_seconds)
        await browser.send(json.dumps({
            "type": "meeting.limit_warning",
            "remaining_seconds": warning_seconds,
            "message": "This meeting is ending soon.",
        }))
        await asyncio.sleep(warning_seconds)
    else:
        await asyncio.sleep(MAX_MEETING_SECONDS)
    LOGGER.info(
        "session=%s event=meeting_limit_reached limit_s=%d",
        metrics.session_id,
        MAX_MEETING_SECONDS,
    )
    await browser.send(json.dumps({
        "type": "meeting.limit_reached",
        "limit_seconds": MAX_MEETING_SECONDS,
        "message": "Meeting time limit reached · Saving transcript…",
    }))


def session_update(target_language, include_transcription):
    return {
        "event_id": f"session_{target_language}_{os.urandom(8).hex()}",
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "sample_rate": 16000,
            "input_audio_format": "pcm",
            "input_audio_transcription": {
                "model": "qwen3-asr-flash-realtime",
            } if include_transcription else None,
            "translation": {
                "language": target_language,
                "same_language_skip_options": {"skip_text": True, "skip_audio": True},
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.2,
                "silence_duration_ms": 700,
            },
        },
    }


async def handle_browser(browser, meeting_limit_enabled=True):
    metrics = SessionMetrics()
    LOGGER.info("session=%s event=browser_connected", metrics.session_id)
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    api_host = os.environ.get("BAILIAN_API_HOST")
    if not api_key or not api_host:
        LOGGER.error("session=%s event=credentials_missing", metrics.session_id)
        await browser.send(json.dumps({
            "type": "error",
            "error": {"message": "Bailian credentials are not configured. Restart Chestnut and enter your API key and host."},
        }))
        await browser.close()
        return

    url = f"wss://{api_host}/api-ws/v1/realtime?model={MODEL}"
    try:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=20,
            close_timeout=5,
            ping_interval=15,
            ping_timeout=15,
            max_size=None,
        ) as chinese_cloud:
            LOGGER.info("session=%s event=cloud_connected target=zh", metrics.session_id)
            async with connect(
                url,
                additional_headers={"Authorization": f"Bearer {api_key}"},
                open_timeout=20,
                close_timeout=5,
                ping_interval=15,
                ping_timeout=15,
                max_size=None,
            ) as english_cloud:
                LOGGER.info("session=%s event=cloud_connected target=en", metrics.session_id)
                browser_send_lock = asyncio.Lock()
                first_chinese, first_english = await asyncio.gather(
                    asyncio.wait_for(chinese_cloud.recv(), timeout=20),
                    asyncio.wait_for(english_cloud.recv(), timeout=20),
                )
                for message, target in ((first_chinese, "zh"), (first_english, "en")):
                    event = json.loads(message)
                    metrics.record_cloud_event(target, event)
                    event["translation_target"] = target
                    await browser.send(json.dumps(event))

                await asyncio.gather(
                    send_cloud(
                        chinese_cloud,
                        "zh",
                        json.dumps(session_update("zh", include_transcription=True)),
                        metrics,
                    ),
                    send_cloud(
                        english_cloud,
                        "en",
                        json.dumps(session_update("en", include_transcription=False)),
                        metrics,
                    ),
                )
                browser_to_cloud = asyncio.create_task(
                    relay_browser_audio(
                        browser,
                        (("zh", chinese_cloud), ("en", english_cloud)),
                        metrics,
                    ),
                    name=f"browser-to-cloud-{metrics.session_id}",
                )
                chinese_to_browser = asyncio.create_task(
                    relay_cloud_events(chinese_cloud, browser, "zh", browser_send_lock, metrics),
                    name=f"cloud-zh-{metrics.session_id}",
                )
                english_to_browser = asyncio.create_task(
                    relay_cloud_events(english_cloud, browser, "en", browser_send_lock, metrics),
                    name=f"cloud-en-{metrics.session_id}",
                )
                watchdog = asyncio.create_task(
                    monitor_cloud_responsiveness(metrics),
                    name=f"watchdog-{metrics.session_id}",
                )
                duration_limit = None
                if meeting_limit_enabled and MAX_MEETING_SECONDS > 0:
                    duration_limit = asyncio.create_task(
                        enforce_meeting_duration(browser, metrics),
                        name=f"duration-limit-{metrics.session_id}",
                    )
                cloud_tasks = {chinese_to_browser, english_to_browser}
                all_tasks = {browser_to_cloud, *cloud_tasks, watchdog}
                if duration_limit:
                    all_tasks.add(duration_limit)
                done, pending = await asyncio.wait(
                    all_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                browser_finished_session = False
                if browser_to_cloud in done and not browser_to_cloud.cancelled():
                    browser_finished_session = browser_to_cloud.result() is True

                if browser_finished_session:
                    await asyncio.wait(cloud_tasks, timeout=5, return_when=asyncio.ALL_COMPLETED)

                for task in all_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*all_tasks, return_exceptions=True)
                for task in done:
                    if task is browser_to_cloud and browser_finished_session:
                        continue
                    if task is duration_limit:
                        continue
                    if task in cloud_tasks and task.exception() is None:
                        LOGGER.warning(
                            "session=%s event=cloud_relay_ended target_task=%s",
                            metrics.session_id,
                            task.get_name(),
                        )
                        raise ConnectionError(f"{task.get_name()} ended before the browser session")
                    task.result()
    except InvalidStatus as error:
        status = getattr(error.response, "status_code", None)
        if status == 401:
            message = (
                "Bailian rejected this API Key (401). Create a new pay-as-you-go API Key "
                "in the same workspace as BAILIAN_API_HOST, update .env, and restart Chestnut."
            )
        elif status == 403:
            message = (
                "This Bailian workspace cannot use the live translation model (403). "
                "Check the API Key permissions and model access."
            )
        else:
            message = f"Bailian connection was rejected (HTTP {status or 'unknown'})."
        LOGGER.error(
            "session=%s event=cloud_connection_rejected status=%s %s",
            metrics.session_id,
            status or "unknown",
            metrics.summary(),
        )
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass
    except Exception as error:
        message = str(error) or type(error).__name__
        LOGGER.exception(
            "session=%s event=realtime_session_failed error_type=%s %s",
            metrics.session_id,
            type(error).__name__,
            metrics.summary(),
        )
        try:
            await browser.send(json.dumps({"type": "error", "error": {"message": message}}))
        except Exception:
            pass
    finally:
        LOGGER.info("session=%s event=browser_session_closed %s", metrics.session_id, metrics.summary())


async def health_handler(_request):
    return web.json_response({"status": "ok", "service": "chestnut-api"})


async def auth_status_handler(request):
    return web.json_response({
        "auth_required": AUTH_REQUIRED,
        "authenticated": request_identity(request) is not None,
        "max_meeting_seconds": MAX_MEETING_SECONDS,
        "meeting_warning_seconds": MEETING_WARNING_SECONDS,
    })


async def invite_auth_handler(request):
    ip = request_ip(request)
    if not origin_is_allowed(request):
        raise web.HTTPForbidden(text="Origin not allowed")
    if not LOGIN_LIMITER.allow(ip):
        LOGGER.warning("event=invite_rate_limited ip=%s", ip)
        return web.json_response({"error": "Too many attempts. Please wait before trying again."}, status=429)
    if not AUTH_REQUIRED:
        return web.json_response({"auth_required": False})
    try:
        payload = await request.json()
    except (json.JSONDecodeError, TypeError):
        return web.json_response({"error": "Invalid request."}, status=400)
    code = payload.get("code", "")
    client_id = str(payload.get("client_id", "")).strip()
    invite_label = invitation_label(code)
    if len(client_id) < 8 or len(client_id) > 200 or not invite_label:
        LOGGER.warning("event=invite_rejected ip=%s", ip)
        return web.json_response({"error": "Invitation code not accepted."}, status=401)
    token = issue_access_token(client_id, invite_label)
    LOGGER.info("event=invite_accepted ip=%s", ip)
    response = web.json_response({"authenticated": True, "expires_in": WEB_TOKEN_TTL_SECONDS})
    forwarded_scheme = request.headers.get("x-forwarded-proto", request.scheme).split(",", 1)[0].strip()
    response.set_cookie(
        "chestnut_access",
        token,
        max_age=WEB_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=forwarded_scheme == "https",
        samesite="Strict",
        path="/",
    )
    return response


async def logout_handler(request):
    if not origin_is_allowed(request):
        raise web.HTTPForbidden(text="Origin not allowed")
    response = web.json_response({"authenticated": False})
    response.del_cookie("chestnut_access", path="/")
    return response


async def websocket_handler(request):
    ip = request_ip(request)
    if not origin_is_allowed(request):
        raise web.HTTPForbidden(text="Origin not allowed")
    socket = web.WebSocketResponse(max_msg_size=0, heartbeat=10)
    await socket.prepare(request)
    browser = AiohttpSocket(socket)
    identity = request_identity(request)
    if not identity:
        await browser.send(json.dumps({
            "type": "access.denied",
            "error": {"message": "Your access has expired. Enter the invitation code again."},
        }))
        await socket.close(code=1008, message=b"Authentication required")
        return socket
    if not CONNECTION_LIMITER.allow(f"{identity.subject}:{ip}"):
        await browser.send(json.dumps({
            "type": "connection.rate_limited",
            "message": "Too many connection attempts. Please wait and try again.",
        }))
        await socket.close(code=1008, message=b"Rate limited")
        return socket

    meeting_id = safe_owner_id(request.query.get("meeting_id"))
    session_key, previous_socket, error = await MEETING_REGISTRY.acquire(identity, meeting_id, browser)
    if error:
        await browser.send(json.dumps({"type": "meeting.rejected", "message": error}))
        await socket.close(code=1008, message=b"Meeting unavailable")
        return socket
    if previous_socket:
        await previous_socket.close()
    LOGGER.info("event=meeting_admitted owner=%s kind=%s meeting=%s", identity.subject, identity.kind, meeting_id)
    try:
        # Web preview meetings use the configurable time limit. Keep the
        # already-published Mini Program behavior unchanged until its UI can
        # present the warning and complete an expired meeting gracefully.
        await handle_browser(browser, meeting_limit_enabled=identity.kind == "web")
    finally:
        await MEETING_REGISTRY.release(identity, session_key)
        if not socket.closed:
            await socket.close()
    return socket


async def save_meeting_handler(request):
    try:
        if not origin_is_allowed(request):
            raise web.HTTPForbidden(text="Origin not allowed")
        identity = request_identity(request)
        if not identity:
            return web.json_response({"error": "Authentication required."}, status=401)
        if request.content_length is not None and request.content_length > MAX_TRANSCRIPT_BYTES:
            raise ValueError("Invalid transcript size")
        raw = await request.read()
        if not raw or len(raw) > MAX_TRANSCRIPT_BYTES:
            raise ValueError("Invalid transcript size")
        payload = json.loads(raw)
        filename_label = identity.label if identity.kind == "web" else ""
        saved = await asyncio.to_thread(save_meeting_transcript, payload, identity.subject, filename_label)
        LOGGER.info(
            "event=transcript_saved storage=%s entries=%d filename=%s",
            saved["storage"],
            len(payload.get("entries", [])),
            saved["filename"],
        )
        return web.json_response(saved, status=201)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except web.HTTPException:
        raise
    except Exception as error:
        LOGGER.exception("event=transcript_save_failed error_type=%s", type(error).__name__)
        return web.json_response({"error": "会议稿云端保存失败，请稍后重试"}, status=503)


async def static_handler(request):
    relative_path = request.match_info.get("path") or "index.html"
    candidate = (ROOT / relative_path).resolve()
    public_files = {"index.html", "style.css", "app.js"}
    if candidate.parent != ROOT or candidate.name not in public_files:
        raise web.HTTPNotFound()
    response = web.FileResponse(candidate)
    response.headers["Cache-Control"] = "no-store"
    return response


async def meeting_file_handler(request):
    identity = request_identity(request)
    if not identity:
        raise web.HTTPUnauthorized(text="Authentication required")
    owner = safe_owner_id(request.match_info["owner"])
    if owner != safe_owner_id(identity.subject):
        raise web.HTTPForbidden(text="This transcript belongs to another account")
    filename = Path(request.match_info["filename"]).name
    owner_dir = (
        ROOT / "meetings" if owner == "local-anonymous" else ROOT / "meetings" / owner
    ).resolve()
    candidate = (owner_dir / filename).resolve()
    if candidate.parent != owner_dir or not candidate.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(candidate, headers={"Cache-Control": "no-store"})


def validate_configuration():
    if AUTH_REQUIRED and len(AUTH_SECRET) < 32:
        raise RuntimeError("CHESTNUT_AUTH_SECRET must contain at least 32 characters when Web authentication is enabled")
    if AUTH_REQUIRED and WEB_TOKEN_TTL_SECONDS <= 0:
        raise RuntimeError("CHESTNUT_WEB_TOKEN_TTL_SECONDS must be greater than zero")
    if MAX_MEETING_SECONDS < 0:
        raise RuntimeError("CHESTNUT_MAX_MEETING_SECONDS must be zero or greater")
    if MEETING_WARNING_SECONDS < 0:
        raise RuntimeError("CHESTNUT_MEETING_WARNING_SECONDS must be zero or greater")
    if MAX_CONCURRENT_MEETINGS < 0:
        raise RuntimeError("CHESTNUT_MAX_CONCURRENT_MEETINGS must be zero or greater")


def create_app():
    validate_configuration()
    app = web.Application(client_max_size=MAX_TRANSCRIPT_BYTES)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/api/auth/status", auth_status_handler)
    app.router.add_post("/api/auth/invite", invite_auth_handler)
    app.router.add_post("/api/auth/logout", logout_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_post("/api/meetings", save_meeting_handler)
    app.router.add_get("/meetings/{owner}/{filename}", meeting_file_handler)
    app.router.add_get("/{path:.*}", static_handler)
    return app


def main():
    display_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
    LOGGER.info("event=service_started url=http://%s:%d", display_host, PORT)
    if HOST == "0.0.0.0":
        LOGGER.info("event=network_access_enabled")
    LOGGER.info("event=realtime_endpoint_ready path=/ws")
    web.run_app(create_app(), host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
