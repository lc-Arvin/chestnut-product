"""Explicit local/cloud settings and gateway trust, with no secret logging."""
import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiohttp import web


@dataclass(frozen=True)
class Deployment:
    cloud: bool = False
    database: str = "sqlite"
    public_origin: str = ""
    admin_enabled: bool = False
    trusted_proxies: tuple = ()
    transcript_storage: str = "local"

    @classmethod
    def from_env(cls):
        mode = os.environ.get("CHESTNUT_ENV", "local")
        if mode not in {"local", "cloud"}:
            raise RuntimeError("CHESTNUT_ENV must be local or cloud")
        cloud = mode == "cloud"
        database = os.environ.get("CHESTNUT_DATABASE_BACKEND", "mysql" if cloud else "sqlite")
        if database not in {"sqlite", "mysql"} or (cloud and database != "mysql"):
            raise RuntimeError("Cloud requires CHESTNUT_DATABASE_BACKEND=mysql; local supports sqlite/mysql")
        origin = os.environ.get("CHESTNUT_PUBLIC_ORIGIN", "").strip().rstrip("/")
        parsed = urlsplit(origin)
        if origin and (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                       or parsed.path or parsed.query or parsed.fragment):
            raise RuntimeError("CHESTNUT_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        if cloud and not origin:
            raise RuntimeError("Cloud requires CHESTNUT_PUBLIC_ORIGIN")
        if cloud and len(os.environ.get("CHESTNUT_AUTH_SECRET", "").strip()) < 32:
            raise RuntimeError("Cloud requires CHESTNUT_AUTH_SECRET with at least 32 characters")
        admin = os.environ.get("CHESTNUT_ADMIN_ENABLED", "1" if cloud else "0")
        if admin not in {"0", "1"}:
            raise RuntimeError("CHESTNUT_ADMIN_ENABLED must be 0 or 1")
        try:
            proxies = tuple(ipaddress.ip_network(value.strip()) for value in
                            os.environ.get("CHESTNUT_TRUSTED_PROXY_CIDRS", "").split(",") if value.strip())
        except ValueError:
            raise RuntimeError("CHESTNUT_TRUSTED_PROXY_CIDRS contains an invalid network") from None
        storage = os.environ.get("CHESTNUT_TRANSCRIPT_STORAGE", "auto")
        if storage == "auto":
            storage = "cos" if os.environ.get("CHESTNUT_COS_BUCKET") else "mysql" if database == "mysql" else "local"
        if storage not in {"local", "mysql", "cos"} or (cloud and storage == "local"):
            raise RuntimeError("Cloud transcripts require mysql or cos; local also supports local files")
        if storage == "mysql" and database != "mysql":
            raise RuntimeError("MySQL transcripts require CHESTNUT_DATABASE_BACKEND=mysql")
        if storage == "cos" and not os.environ.get("CHESTNUT_COS_BUCKET"):
            raise RuntimeError("COS storage requires CHESTNUT_COS_BUCKET")
        return cls(cloud, database, origin, admin == "1", proxies, storage)


DEPLOYMENT_KEY = web.AppKey("deployment", Deployment)


def deployment(request):
    return request.app.get(DEPLOYMENT_KEY, Deployment())


def trusted_proxy(request):
    try:
        peer = ipaddress.ip_address(request.remote or "")
        return any(peer in network for network in deployment(request).trusted_proxies)
    except ValueError:
        return False


def client_ip(request):
    if trusted_proxy(request):
        # Walk right to left: an incoming client-supplied prefix is untrusted.
        chain = request.headers.get("X-Forwarded-For", "").split(",")
        for value in reversed(chain):
            try:
                address = ipaddress.ip_address(value.strip())
            except ValueError:
                break
            if not any(address in network for network in deployment(request).trusted_proxies):
                return str(address)
    return request.remote or "unknown"


def wechat_openid(request):
    # Local developer tools historically emulate WeChat headers. On cloud,
    # accept platform identities only through explicitly trusted gateways.
    if deployment(request).cloud and not trusted_proxy(request):
        return ""
    return request.headers.get("X-WX-OpenID", "").strip()


def secure_cookie(request):
    return deployment(request).cloud or request.secure


def admin_origin(request):
    return deployment(request).public_origin if deployment(request).cloud else f"{request.scheme}://{request.host}"
