"""出站 HTTP 目标校验（SSRF 防护）。

为什么需要它：AI 功能允许每个用户自己填 `base_url`，服务端会代其发起请求。
没有出口校验时，任何已登录用户（学生即可）把 base_url 指向内网地址，
就能让服务端替他探测内网端口、访问云元数据端点、触达仅内网可达的接口——
而响应差异还能用来做端口扫描。这是多租户 SaaS 里最典型的 SSRF 载体。

设计要点：

1. **必须校验到 IP 层**。只检查字符串里的主机名形如 `example.com` 是不够的：
   `http://127.0.0.1`、`http://[::1]`、`http://2130706433`（十进制形式的
   127.0.0.1）、以及指向内网的公网域名都会绕过字符串检查。
   所以要解析 DNS，**逐个检查解析出来的每一个地址**。
   只检查第一个地址是不够的——多 A 记录里只要有一个是内网就能通。

2. **必须在每次请求前校验，不能只在保存配置时校验一次**。
   DNS 记录会变，TOCTOU 下「保存时安全、请求时不安全」是真实可利用的。
   调用方应在发起请求前调用本模块。

3. **私网自建模型服务是合法需求**。学校很可能在内网部署自己的推理服务，
   直接硬拦会打断这条正路。因此提供 `PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK=1`
   显式开关，并把默认行为保持为「拒绝私网」。

4. **严格环境可用主机白名单**。设 `PAPERGUARD_AI_ALLOWED_HOSTS=api.x.com,api.y.com`
   后只有这些主机放行，适合把出口收敛到企业采购的模型服务。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import ssl
from urllib.parse import urlsplit

import httpcore
import httpx
from fastapi import HTTPException


class OutboundBlocked(HTTPException):
    """出站被守卫拒绝。

    单独一个类型，是为了让调用方能**精确**区分「请求根本没发出去」与
    「发出去了但上游报错」——前者应归还调用配额，后者不应（上游可能已计费）。
    只按状态码 400 判断是不行的：参数校验失败也是 400。
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=400, detail=detail)

# 允许的协议。刻意不含 file: / gopher: / ftp: 等
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# 允许的端口。0 表示不限制（部分私有化模型服务用非标准端口）。
_ALLOWED_PORTS = frozenset({80, 443, 8080, 8000, 11434})


def _allow_private_network() -> bool:
    return os.environ.get("PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK", "").strip() in {"1", "true", "yes"}


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("PAPERGUARD_AI_ALLOWED_HOSTS", "").strip()
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _blocked_ip_reason(addr: ipaddress._BaseAddress) -> str | None:
    """返回拒绝原因；允许则返回 None。"""
    if addr.is_unspecified:
        return "未指定地址 (0.0.0.0 / ::)"
    if addr.is_loopback:
        return "回环地址"
    if addr.is_link_local:
        # 含 169.254.0.0/16，即云主机元数据端点 169.254.169.254 所在网段
        return "链路本地地址 (含 169.254.169.254 元数据端点)"
    if addr.is_private:
        return "私网地址"
    if addr.is_multicast:
        return "组播地址"
    if addr.is_reserved:
        return "保留地址"
    if not addr.is_global:
        return "非公网地址"
    return None


class _ValidatedNetworkBackend(httpcore.NetworkBackend):
    """Resolve once, validate all addresses, connect to an IP (not the DNS name).

    httpcore still holds the original origin, so HTTP Host and TLS SNI/certificate
    verification use the provider hostname. No proxy/env resolver can bypass this
    connection boundary, and there is no retry to a guessed completion endpoint.
    """
    def __init__(self) -> None:
        self.backend = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        host = host.decode('ascii') if isinstance(host, bytes) else host
        authority = f"[{host}]" if ':' in host else host
        assert_safe_outbound_url(f"https://{authority}:{port}", resolve_dns=False)
        literal = _parse_ip_literal(host)
        addresses = [literal] if literal else _resolve(host)
        if not _allow_private_network():
            for address in addresses:
                if _blocked_ip_reason(address):
                    raise OutboundBlocked("连接地址不属于允许的公网出口")
        return self.backend.connect_tcp(
            str(addresses[0]), port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(self, *args, **kwargs):
        raise OutboundBlocked("AI 出口禁止 Unix socket")

    def sleep(self, seconds):
        return self.backend.sleep(seconds)


class GuardedTransport(httpx.HTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False, retries=0)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_ValidatedNetworkBackend(), retries=0,
        )


def _looks_like_ip_shorthand(host: str) -> bool:
    """主机是否是「十进制 / 十六进制写法的 IP」，例如 2130706433、0x7f000001。

    这类写法 ipaddress.ip_address() 解析不了，只会被当成主机名。
    但它们**不需要 DNS 查询**——getaddrinfo 走的是本地 inet_aton 转换。
    所以保存阶段（resolve_dns=False）也应当把它们查掉，
    否则用户会看到「保存成功」却在真正使用时才被拦。
    """
    candidate = host.strip("[]").lower()
    if candidate.isdigit():
        return True
    if candidate.startswith("0x"):
        return True
    # 短写法 127.1 / 0177.0.0.1：全是数字与点，且首段不以 0 开头视为短写法
    if candidate.count(".") in (1, 2) and all(
        part.isdigit() for part in candidate.split(".") if part
    ):
        return True
    return False


def _parse_ip_literal(host: str) -> ipaddress._BaseAddress | None:
    """主机本身就是 IP 字面量时返回该地址，否则 None。"""
    candidate = host.strip("[]")
    # 去掉 IPv6 zone id，例如 fe80::1%eth0
    candidate = candidate.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _resolve(host: str) -> list[ipaddress._BaseAddress]:
    """解析主机名，返回所有地址。解析失败时抛 400。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundBlocked(f"无法解析主机名: {host}") from exc
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise OutboundBlocked(f"主机名未解析到任何地址: {host}")
    return addresses


def assert_safe_outbound_url(
    url: str,
    *,
    label: str = "Base URL",
    resolve_dns: bool = True,
) -> str:
    """校验出站目标可安全访问；不合法时抛 HTTPException(400)。返回规范化后的 URL。

    `resolve_dns` 区分两个调用场景，**安全边界只在 resolve_dns=True 那一侧**：

    - `True`（请求前，安全边界）：解析 DNS 并逐个检查每个地址。
      保存配置时做过检查**不能**代替它——DNS 记录会变，
      「保存时安全、请求时不安全」在 TOCTOU 下真实可利用。
    - `False`（保存时，仅为尽早反馈）：只做语法检查，并检查**主机本身就是
      IP 字面量**的情况（不需要 DNS）。主机名若解析不了**不阻止保存**——
      否则保存成败就取决于 DNS 可用性，瞬时抖动会让用户存不进配置，
      而且测试会被绑死在真实 DNS 上。真正的拦截发生在请求时。
    """

    raw = (url or "").strip()
    if not raw:
        raise OutboundBlocked(f"{label} 不能为空")

    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise OutboundBlocked(f"{label} 协议只允许 http 或 https，当前为 {scheme or '(空)'}")

    try:
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        # 端口非法（例如 :99999）
        raise OutboundBlocked(f"{label} 端口不合法") from exc

    if not host:
        raise OutboundBlocked(f"{label} 缺少主机名")

    host = host.lower().rstrip(".")

    # 端口策略：0 表示不限制
    allow_any_port = os.environ.get("PAPERGUARD_AI_ALLOW_ANY_PORT", "").strip() in {"1", "true", "yes"}
    if not allow_any_port and port is not None and port not in _ALLOWED_PORTS:
        allowed = ", ".join(str(p) for p in sorted(_ALLOWED_PORTS))
        raise OutboundBlocked(
            f"{label} 端口 {port} 不被允许（允许 {allowed}；"
            "如确需自定义端口请设置 PAPERGUARD_AI_ALLOW_ANY_PORT=1）"
        )

    whitelist = _allowed_hosts()
    if whitelist and host not in whitelist:
        raise OutboundBlocked(
            f"{label} 主机 {host} 不在允许清单内（已配置 PAPERGUARD_AI_ALLOWED_HOSTS）"
        )

    if _allow_private_network():
        # 显式放行私网：学校自建推理服务的合法场景
        return raw

    literal = _parse_ip_literal(host)
    if literal is not None:
        addresses = [literal]
    elif not resolve_dns and not _looks_like_ip_shorthand(host):
        # 保存阶段：普通主机名无法在不查询 DNS 的前提下判定安全性，
        # 交给请求前的完整校验。简写 IP 形式不走这条——它不需要 DNS，
        # getaddrinfo 走的是本地 inet_aton 转换。
        return raw
    else:
        addresses = _resolve(host)

    for addr in addresses:
        reason = _blocked_ip_reason(addr)
        if reason:
            raise OutboundBlocked(
                f"{label} 指向内网地址已被拒绝（{reason}: {addr}）。"
                "如为学校自建模型服务，请设置 PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK=1"
            )

    return raw
