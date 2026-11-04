"""目标授权管理：压测目标必须先通过授权确认。"""
import ipaddress
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

from app.services.logger import log
from app.services.settings import settings
from app.ui.i18n import L


def normalize_host(target: str) -> str:
    """提取规范化主机名，支持 URL、域名端口和 IPv4/IPv6。"""
    t = (target or "").strip()
    if not t:
        return ""

    has_scheme = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t))
    if not has_scheme:
        authority = re.split(r"[/\?#]", t, maxsplit=1)[0].strip()
        # 裸 IPv6（如 ::1 / 2001:db8::1）没有方括号，urlsplit 会把它
        # 误判为 host:port；先按 IP 地址直接解析。
        if authority and not authority.startswith("[") and authority.count(":") >= 2:
            try:
                return str(ipaddress.ip_address(authority)).lower()
            except ValueError:
                return ""

    try:
        parsed = urlsplit(t if has_scheme else f"//{t}")
        host = parsed.hostname or ""
        # 访问 port 属性会校验非法端口（如 example.com:abc / :70000）。
        # 只取 hostname 会把这类输入误判成有效目标。
        _ = parsed.port
    except ValueError:
        return ""
    return host.rstrip(".").lower().strip()


def build_http_url(target: str, host: str, port: int, protocol: str) -> str:
    """构造压测 URL；保留路径/查询和显式端口，裸 IPv6 自动加方括号。"""
    raw = (target or "").strip()
    scheme = "https" if str(protocol).upper() == "HTTPS" else "http"
    default_port = 443 if scheme == "https" else 80

    # The selected protocol is authoritative.  Previously a complete URL was
    # returned unchanged, so choosing HTTPS for ``http://host/path`` still
    # sent plain HTTP, and changing the port had no effect when the target
    # already contained a scheme.
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        parsed = urlsplit(raw)
        explicit_port = parsed.port
        if explicit_port is not None:
            authority = parsed.netloc
        else:
            authority = f"[{host}]" if ":" in host else host
            if int(port) != default_port:
                authority += f":{int(port)}"
        return urlunsplit((scheme, authority, parsed.path,
                           parsed.query, parsed.fragment))

    # 裸 IPv6 不能直接交给 urlsplit("//...") 判断端口；先单独识别，
    # 同时保留其后的 path/query/fragment。
    match = re.match(r"^([^/?#]*)(.*)$", raw)
    raw_authority, tail = match.groups() if match else (raw, "")
    if raw_authority and not raw_authority.startswith("[") \
            and raw_authority.count(":") >= 2:
        try:
            ipv6 = str(ipaddress.ip_address(raw_authority))
            suffix = f":{int(port)}" if int(port) != default_port else ""
            return f"{scheme}://[{ipv6}]{suffix}{tail}"
        except ValueError:
            pass

    parsed = urlsplit(f"//{raw}")
    explicit_port = parsed.port  # normalize_host 已校验；这里保留显式端口
    if explicit_port is not None:
        authority = parsed.netloc
    else:
        authority = f"[{host}]" if ":" in host else host
        if int(port) != default_port:
            authority += f":{int(port)}"
    return urlunsplit((scheme, authority, parsed.path, parsed.query, parsed.fragment))


def is_authorized(host: str) -> bool:
    return any(a.get("host") == host for a in settings.authorized)


def add_authorized(host: str, note: str = "") -> bool:
    """Record a confirmed target; True when it is (now) authorized.

    Callers treat a falsy result as "could not save", so an already
    authorized host must report True rather than None.
    """
    if not host:
        return False
    if is_authorized(host):
        return True
    items = list(settings.authorized)
    items.append({"id": uuid.uuid4().hex[:8], "host": host,
                  "note": note, "ts": time.time()})
    if not settings.set("authorized", items):
        log.error(L(f"授权目标保存失败: {host}", f"Failed to save authorized target: {host}"))
        return False
    log.info(L(f"新增授权目标: {host}", f"Target authorized: {host}"))
    return True


def remove_authorized(host: str):
    items = [a for a in settings.authorized if a.get("host") != host]
    if not settings.set("authorized", items):
        log.error(L(f"授权目标移除保存失败: {host}",
                    f"Failed to persist target authorization removal: {host}"))
        return False
    log.info(L(f"移除授权目标: {host}", f"Target authorization removed: {host}"))
    return True


def is_private_target(host: str) -> bool:
    """本机/内网地址视为低风险目标。"""
    host = normalize_host(host)
    if host == "localhost" or host.endswith(".local") or host.endswith(".lan"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_unspecified)
    except ValueError:
        return False
