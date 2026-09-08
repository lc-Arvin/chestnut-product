"""Loopback-only admin routes, kept separate from public meeting APIs."""
import asyncio
import hmac
import ipaddress
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web
from admin_store import AdminStore

STORE_KEY = web.AppKey("admin_store", AdminStore)
ADMIN_COOKIE = "chestnut_admin"
ASSETS = Path(__file__).resolve().parent / "admin"


def local_admin_request(request):
    try:
        peer = ipaddress.ip_address(request.remote or "")
        hostname = urlsplit("//" + request.host).hostname
        return (peer.is_loopback and hostname in {"127.0.0.1", "localhost", "::1"}
                and not any(header in request.headers for header in
                            ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-WX-OpenID")))
    except ValueError:
        return False


@web.middleware
async def admin_boundary(request, handler):
    is_admin = request.path in {"/admin", "/admin/"} or request.path.startswith(("/admin/", "/api/admin/"))
    if not is_admin:
        return await handler(request)
    if STORE_KEY not in request.app or not local_admin_request(request):
        raise web.HTTPNotFound()
    try:
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
                raise web.HTTPForbidden(text="管理操作必须来自本机后台页面")
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(text="请使用 JSON 请求")
        public = {"/api/admin/session", "/api/admin/setup", "/api/admin/login"}
        if request.path.startswith("/api/admin/") and request.path not in public:
            csrf = request.app[STORE_KEY].session(request.cookies.get(ADMIN_COOKIE, ""))
            if not csrf:
                raise web.HTTPUnauthorized(text="管理员登录已失效，请重新登录")
            if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(csrf, request.headers.get("X-CSRF-Token", "")):
                raise web.HTTPForbidden(text="操作凭证不匹配，请刷新页面后重试")
        response = await handler(request)
    except (ValueError, json.JSONDecodeError) as error:
        response = web.json_response({"error": str(error)}, status=400)
    except KeyError:
        response = web.json_response({"error": "记录不存在"}, status=404)
    except web.HTTPException as error:
        response = web.json_response({"error": error.text}, status=error.status)
    response.headers.update({
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    return response


async def body(request):
    if request.content_length and request.content_length > 32768:
        raise ValueError("请求内容过长")
    raw = await request.read()
    if len(raw) > 32768:
        raise ValueError("请求内容过长")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("请求须为 JSON 对象")
    return value


def mount_admin(app, store, on_code_changed):
    app[STORE_KEY] = store

    async def upkeep(_app):
        async def sweep():
            while True:
                await asyncio.sleep(3600)
                store.maintenance()
        task = asyncio.create_task(sweep())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    app.cleanup_ctx.append(upkeep)

    async def cleanup(_app):
        store.close()
    app.on_cleanup.append(cleanup)

    async def asset(request):
        filename = request.match_info.get("filename") or "index.html"
        if filename not in {"index.html", "admin.css", "admin.js"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS / filename)

    async def session(_request):
        csrf = store.session(_request.cookies.get(ADMIN_COOKIE, ""))
        return web.json_response({"setup_required": not bool(store.setting("password")), "authenticated": bool(csrf), "csrf": csrf or ""})

    async def login(request):
        payload = await body(request)
        network = store.pseudonym(request.remote)
        with store.lock:
            failures = store.db.execute("SELECT count(*) FROM events WHERE kind='admin_login_failure' AND network=? AND created_at>?",
                                        (network, time.time()-600)).fetchone()[0]
        if failures >= 5:
            return web.json_response({"error": "登录尝试过多，请在 10 分钟后重试"}, status=429)
        if request.path.endswith("/setup"):
            await asyncio.to_thread(store.setup, payload.get("password"))
        elif not await asyncio.to_thread(store.check_password, payload.get("password")):
            store.record("admin_login_failure", ip=request.remote, channel="admin")
            return web.json_response({"error": "密码不正确"}, status=401)
        token, csrf = store.new_session()
        store.record("admin_login", ip=request.remote, channel="admin")
        response = web.json_response({"authenticated": True, "csrf": csrf})
        response.set_cookie(ADMIN_COOKIE, token, httponly=True, samesite="Strict", secure=request.secure, max_age=8*3600, path="/api/admin")
        return response

    async def logout(request):
        store.logout(request.cookies.get(ADMIN_COOKIE, ""))
        response = web.json_response({"ok": True})
        response.del_cookie(ADMIN_COOKIE, path="/api/admin")
        return response

    async def codes(request):
        if request.method == "POST":
            return web.json_response({"items": store.create_codes(await body(request))}, status=201)
        return web.json_response(store.list_codes(request.query.get("q", ""), request.query.get("status", ""), int(request.query.get("page", "1")), request.query.get("sort", "created_desc")))

    async def update(request):
        code_id = request.match_info["code_id"]
        result = store.update_code(code_id, await body(request))
        await on_code_changed(code_id)
        return web.json_response(result)

    async def reveal(request):
        return web.json_response({"code": store.reveal(request.match_info["code_id"])})

    async def overview(request):
        return web.json_response(store.overview(request.query.get("start", ""), request.query.get("end", "")))

    async def events(request):
        return web.json_response(store.events(request.query.get("code_id", ""), int(request.query.get("page", "1")), request.query.get("kind", "")))

    async def alerts(request):
        return web.json_response(store.alerts(request.query.get("state", "open"), int(request.query.get("page", "1"))))

    async def acknowledge(request):
        store.acknowledge(int(request.match_info["alert_id"]))
        return web.json_response({"ok": True})

    app.router.add_get("/admin", asset)
    app.router.add_get("/admin/", asset)
    app.router.add_get("/admin/{filename}", asset)
    app.router.add_get("/api/admin/session", session)
    app.router.add_post("/api/admin/setup", login)
    app.router.add_post("/api/admin/login", login)
    app.router.add_post("/api/admin/logout", logout)
    app.router.add_get("/api/admin/codes", codes)
    app.router.add_post("/api/admin/codes", codes)
    app.router.add_patch("/api/admin/codes/{code_id}", update)
    app.router.add_post("/api/admin/codes/{code_id}/reveal", reveal)
    app.router.add_get("/api/admin/overview", overview)
    app.router.add_get("/api/admin/events", events)
    app.router.add_get("/api/admin/alerts", alerts)
    app.router.add_post("/api/admin/alerts/{alert_id}/acknowledge", acknowledge)
