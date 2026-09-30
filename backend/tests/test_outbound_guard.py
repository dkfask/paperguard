"""出站 URL 校验（SSRF 防护）的回归测试。

背景：AI 功能允许每个用户自填 `base_url`，服务端代其发起请求。修复前
任何已登录用户（学生即可）把 base_url 指向内网地址即可让服务端代打，
实测可命中本地监听器并把论文正文发过去。

这里锁死的不只是「拒绝 127.0.0.1」，还包括**各种绕过表示法**——
因为只做字符串检查的实现会全部漏掉它们（见下方 bypass 用例的原理说明）。
"""

from __future__ import annotations

import socket

import pytest
from fastapi import HTTPException

from app.services.outbound_guard import assert_safe_outbound_url


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK",
        "PAPERGUARD_AI_ALLOWED_HOSTS",
        "PAPERGUARD_AI_ALLOW_ANY_PORT",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def public_dns(monkeypatch):
    """把任意主机名解析到公网地址。

    需要它的用例用的是 .example.com 这类**故意不可解析**的假域名，
    不打桩的话守卫会在解析阶段就 400，测不到真正要验证的那一步。
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return fake_getaddrinfo


def _blocked(url: str) -> str:
    with pytest.raises(HTTPException) as exc:
        assert_safe_outbound_url(url)
    assert exc.value.status_code == 400, f"应当是 400，实际 {exc.value.status_code}"
    return str(exc.value.detail)


# --------------------------------------------------------------------------
# 协议
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/_x",
        "ftp://example.com/x",
        "dict://127.0.0.1:11211/stat",
    ],
)
def test_rejects_non_http_schemes(url: str) -> None:
    detail = _blocked(url)
    assert "协议" in detail


def test_rejects_missing_scheme() -> None:
    _blocked("127.0.0.1:8000")


# --------------------------------------------------------------------------
# 回环 / 私网 / 元数据端点
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/v1",
        "http://localhost:8000",
        "http://[::1]:8000",
        "http://0.0.0.0:8000",
        "http://[::]:8000",
    ],
)
def test_rejects_loopback_and_unspecified(url: str) -> None:
    _blocked(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/v1",
        "http://172.16.5.4/v1",
        "http://192.168.1.1/v1",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_rejects_private_and_link_local(url: str) -> None:
    detail = _blocked(url)
    assert "元数据" in detail or "私网" in detail


def test_metadata_endpoint_message_mentions_the_known_target() -> None:
    """元数据端点要能被运维一眼认出来，而不是只说「私网地址」。"""
    detail = _blocked("http://169.254.169.254/latest/meta-data/")
    assert "169.254.169.254" in detail


# --------------------------------------------------------------------------
# 绕过表示法 —— 这些是本测试存在的核心理由
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "host",
    [
        "2130706433",   # 十进制的 127.0.0.1
        "0x7f000001",   # 十六进制的 127.0.0.1
        "127.1",        # 短写法
    ],
)
def test_rejects_alternate_notation_loopback(host: str) -> None:
    """这些写法在 ipaddress.ip_address() 里会解析失败，只能当主机名走 DNS。

    而 socket.getaddrinfo() **会**把它们解析成 127.0.0.1（已实测）。
    所以守卫必须解析到 IP 层逐个检查——只做字符串检查的实现会全部漏掉。
    """
    # 先确认前提：这个主机名确实会被解析成回环地址
    resolved = {info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    assert "127.0.0.1" in resolved, "前提不成立：getaddrinfo 已不再解析该写法，测试需重新评估"

    detail = _blocked(f"http://{host}:8000/v1")
    assert "回环" in detail


def test_rejects_hostname_resolving_to_private_address(monkeypatch) -> None:
    """公网名字解析到内网 IP 同样要拦——只看主机名形如域名是不够的。"""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    detail = _blocked("https://totally-normal.example.com/v1")
    assert "私网" in detail


def test_checks_every_resolved_address_not_just_the_first(monkeypatch) -> None:
    """多 A 记录里只要有一个是内网就能通，因此必须逐个检查。"""
    addresses = [("93.184.216.34", 80), ("192.168.5.5", 80)]

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80)) for ip, _port in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    detail = _blocked("https://multi-a-record.example.com/v1")
    assert "私网" in detail


# --------------------------------------------------------------------------
# 端口
# --------------------------------------------------------------------------

def test_rejects_unexpected_port() -> None:
    detail = _blocked("http://8.8.8.8:31337/v1")
    assert "端口" in detail


def test_allows_standard_ports() -> None:
    assert assert_safe_outbound_url("https://8.8.8.8:443/v1") == "https://8.8.8.8:443/v1"
    assert assert_safe_outbound_url("http://8.8.8.8:80/v1") == "http://8.8.8.8:80/v1"


def test_allows_custom_port_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_ALLOW_ANY_PORT", "1")
    assert assert_safe_outbound_url("https://8.8.8.8:31337/v1")


# --------------------------------------------------------------------------
# 合法的私网自建模型服务
# --------------------------------------------------------------------------

def test_private_network_allowed_when_explicitly_opted_in(monkeypatch) -> None:
    """学校自建推理服务是正当需求，不能一刀切拦死。"""
    monkeypatch.setenv("PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK", "1")
    assert assert_safe_outbound_url("http://10.0.12.7:8080/v1") == "http://10.0.12.7:8080/v1"


def test_private_opt_in_still_rejects_non_http_schemes(monkeypatch) -> None:
    """放行私网不等于放行任意协议。"""
    monkeypatch.setenv("PAPERGUARD_AI_ALLOW_PRIVATE_NETWORK", "1")
    _blocked("file:///etc/passwd")


# --------------------------------------------------------------------------
# 主机白名单（严格环境把出口收敛到采购的模型服务）
# --------------------------------------------------------------------------

def test_host_allowlist_rejects_unlisted(monkeypatch, public_dns) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_ALLOWED_HOSTS", "api.vendor-a.com,api.vendor-b.com")
    _blocked("https://someone-else.example.com/v1")


def test_host_allowlist_admits_listed(monkeypatch, public_dns) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_ALLOWED_HOSTS", "api.vendor-a.com,api.vendor-b.com")
    assert assert_safe_outbound_url("https://api.vendor-b.com/v1")


def test_host_allowlist_is_case_insensitive(monkeypatch, public_dns) -> None:
    monkeypatch.setenv("PAPERGUARD_AI_ALLOWED_HOSTS", "api.vendor-a.com")
    assert assert_safe_outbound_url("https://API.Vendor-A.com/v1")


# --------------------------------------------------------------------------
# 输入健壮性
# --------------------------------------------------------------------------

def test_rejects_empty() -> None:
    _blocked("")


def test_rejects_missing_host() -> None:
    _blocked("https:///v1")


def test_host_allowlist_does_not_bypass_ip_check(monkeypatch) -> None:
    """白名单只回答「信任哪个供应商」，不回答「它能不能指向内网」。

    没有这条的话，实现很容易写成「配了白名单就跳过 IP 校验」——
    而被列入白名单的域名其 DNS 记录仍可被劫持或 rebinding 到内网，
    等于在严格环境里反而开了一个洞。白名单与 IP 校验是**两道独立的闸**。
    """
    monkeypatch.setenv("PAPERGUARD_AI_ALLOWED_HOSTS", "api.vendor-a.com")

    def fake_getaddrinfo(host, port, *args, **kwargs):
        # 白名单内的域名解析到内网
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.10.5", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    detail = _blocked("https://api.vendor-a.com/v1")
    assert "私网" in detail


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "127.1"])
def test_save_time_also_blocks_ip_shorthand(host: str) -> None:
    """保存阶段（resolve_dns=False）也必须拦下简写 IP。

    这类写法不需要 DNS 查询——getaddrinfo 走的是本地 inet_aton 转换。
    若保存阶段放过它，用户会看到「保存成功」，却在真正调用时才被拦；
    更糟的是让守卫看上去比实际弱。
    """
    detail = _blocked(f"http://{host}:8080/v1")
    assert "回环" in detail


def test_save_time_still_allows_unresolvable_hostname() -> None:
    """普通主机名在保存阶段不查 DNS——否则保存成败取决于 DNS 可用性。"""
    # 刻意用一个无法解析的名字：不应抛错
    assert_safe_outbound_url("https://api.some-vendor.invalid/v1", resolve_dns=False)


def test_save_time_still_blocks_ip_literal_private() -> None:
    """主机本身就是私网 IP 字面量时，保存阶段就该拦。"""
    detail = _blocked("http://192.168.1.1:8080/v1")
    assert "私网" in detail
