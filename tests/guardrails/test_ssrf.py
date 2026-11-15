"""SSRF guard (ADR-0018, points 1 to 4). No real network, no real DNS: resolvers are injected and
servers listen on loopback."""

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field

import httpcore
import httpx
import pytest

from dzdia_newsroom.guardrails.ssrf import (
    BlockedUrlError,
    GuardedBackend,
    guarded_client,
    is_allowed_address,
)

REFUSED_ADDRESSES = [
    "127.0.0.1",
    "10.0.0.5",
    "172.17.0.2",
    "192.168.1.1",
    "169.254.169.254",
    "0.0.0.0",
    "100.64.0.1",  # carrier-grade NAT
    "224.0.0.1",  # multicast, is_global on some interpreters
    "ff02::1",
    "::1",
    "fe80::1",
    "fc00::1",
    "64:ff9b::7f00:1",  # NAT64, is_global on some interpreters
    "::7f00:1",  # IPv4-compatible, embeds 127.0.0.1
    "::808:808",  # IPv4-compatible, refused whatever it embeds (here 8.8.8.8)
    "::ffff:0:7f00:1",  # IPv4-translated, embeds 127.0.0.1
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "2002:7f00:1::",  # 6to4
]
ACCEPTED_ADDRESSES = ["8.8.8.8", "93.184.216.34", "2606:4700::1111", "::ffff:8.8.8.8"]
PUBLIC = "93.184.216.34"


@pytest.mark.parametrize("address", REFUSED_ADDRESSES)
def test_non_global_addresses_are_refused(address: str) -> None:
    assert is_allowed_address(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", ACCEPTED_ADDRESSES)
def test_global_unicast_addresses_are_accepted(address: str) -> None:
    assert is_allowed_address(ipaddress.ip_address(address)) is True


def test_blocked_url_error_is_not_an_httpx_or_httpcore_error() -> None:
    assert not issubclass(BlockedUrlError, (httpx.HTTPError, httpcore.NetworkError))
    assert BlockedUrlError.__mro__[1] is Exception


# --- backend, with a fake inner backend ---


class FakeStream(httpcore.AsyncNetworkStream):
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return b""

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return None

    async def aclose(self) -> None:
        return None


@dataclass
class FakeInner(httpcore.AsyncNetworkBackend):
    """Records every connect_tcp call; addresses in `failing` raise a connection error."""

    failing: set[str] = field(default_factory=set)
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.calls.append((host, port))
        if host in self.failing:
            raise httpcore.ConnectError(f"cannot connect to {host}")
        return FakeStream()

    async def sleep(self, seconds: float) -> None:
        return None


@dataclass
class FakeResolver:
    answers: dict[str, list[str]]
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.answers.get(host, [])


def _backend(
    answers: dict[str, list[str]], *, failing: set[str] | None = None
) -> tuple[GuardedBackend, FakeResolver, FakeInner]:
    resolver = FakeResolver(answers)
    inner = FakeInner(failing=failing or set())
    return GuardedBackend(resolver=resolver, inner=inner), resolver, inner


async def test_a_public_hostname_connects_to_the_resolved_address() -> None:
    backend, resolver, inner = _backend({"example.org": [PUBLIC]})
    await backend.connect_tcp("example.org", 443)
    assert inner.calls == [(PUBLIC, 443)]  # pinned: the inner backend never sees the hostname
    assert resolver.calls == [("example.org", 443)]


async def test_one_private_answer_refuses_the_whole_hostname() -> None:
    backend, _, inner = _backend({"mixed.example": [PUBLIC, "10.0.0.7"]})
    with pytest.raises(BlockedUrlError) as excinfo:
        await backend.connect_tcp("mixed.example", 443)
    assert inner.calls == []
    assert "10.0.0.7" not in str(excinfo.value)  # the message keeps the host only
    assert "mixed.example" in str(excinfo.value)


@pytest.mark.parametrize("service", ["postgres", "api"])
async def test_docker_service_names_are_refused(service: str) -> None:
    backend, _, inner = _backend({"postgres": ["172.18.0.2"], "api": ["172.18.0.3"]})
    with pytest.raises(BlockedUrlError):
        await backend.connect_tcp(service, 80)
    assert inner.calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "::1"])
async def test_refused_ip_literals_never_reach_the_resolver(host: str) -> None:
    backend, resolver, inner = _backend({})
    with pytest.raises(BlockedUrlError):
        await backend.connect_tcp(host, 443)
    assert resolver.calls == [] and inner.calls == []


async def test_a_public_ip_literal_is_accepted_without_resolution() -> None:
    backend, resolver, inner = _backend({})
    await backend.connect_tcp("[2606:4700::1111]", 443)
    await backend.connect_tcp(PUBLIC, 80)
    assert resolver.calls == []
    assert inner.calls == [("2606:4700::1111", 443), (PUBLIC, 80)]


@pytest.mark.parametrize("port", [8080, 5432])
async def test_ports_other_than_80_and_443_are_refused(port: int) -> None:
    backend, resolver, inner = _backend({"example.org": [PUBLIC]})
    with pytest.raises(BlockedUrlError):
        await backend.connect_tcp("example.org", port)
    assert resolver.calls == [] and inner.calls == []


async def test_an_empty_answer_is_a_connection_error() -> None:
    backend, _, inner = _backend({})
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("nowhere.example", 443)
    assert inner.calls == []


async def test_the_second_address_is_tried_when_the_first_fails() -> None:
    second = "8.8.8.8"
    backend, resolver, inner = _backend({"example.org": [PUBLIC, second]}, failing={PUBLIC})
    await backend.connect_tcp("example.org", 443)
    assert inner.calls == [(PUBLIC, 443), (second, 443)]
    assert resolver.calls == [("example.org", 443)]  # resolved once per connection


async def test_the_last_connection_error_is_raised_when_every_address_fails() -> None:
    second = "8.8.8.8"
    backend, resolver, _ = _backend({"example.org": [PUBLIC, second]}, failing={PUBLIC, second})
    with pytest.raises(httpcore.ConnectError, match=second):
        await backend.connect_tcp("example.org", 443)
    assert len(resolver.calls) == 1


async def test_unix_sockets_are_refused() -> None:
    backend, _, _ = _backend({})
    with pytest.raises(BlockedUrlError):
        await backend.connect_unix_socket("/var/run/docker.sock")


# --- end to end through the public factory, with a loopback server ---


@dataclass
class LoopbackServer:
    port: int
    requests: list[bytes]


@pytest.fixture
async def loopback_server() -> AsyncIterator[LoopbackServer]:
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
            b"Connection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield LoopbackServer(port=port, requests=requests)
    finally:
        server.close()
        await server.wait_closed()


async def test_a_loopback_literal_is_refused_through_the_client(
    loopback_server: LoopbackServer,
) -> None:
    # Fails if a later httpx stops routing connections through the replaced `_pool`.
    port = loopback_server.port
    async with guarded_client(allowed_ports=frozenset({port})) as client:
        with pytest.raises(BlockedUrlError):
            await client.get(f"http://127.0.0.1:{port}/")
    assert loopback_server.requests == []


async def test_a_hostname_resolving_to_loopback_is_refused_through_the_client(
    loopback_server: LoopbackServer,
) -> None:
    port = loopback_server.port
    resolver = FakeResolver({"site.test": ["127.0.0.1"]})
    async with guarded_client(allowed_ports=frozenset({port}), resolver=resolver) as client:
        with pytest.raises(BlockedUrlError):
            await client.get(f"http://site.test:{port}/")
    assert loopback_server.requests == []
    assert resolver.calls == [("site.test", port)]


@dataclass
class LoopbackInner(httpcore.AsyncNetworkBackend):
    """Opens the real loopback connection whatever address it is asked for, and records it."""

    port: int
    calls: list[tuple[str, int]] = field(default_factory=list)
    real: httpcore.AsyncNetworkBackend = field(default_factory=httpcore.AnyIOBackend)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.calls.append((host, port))
        return await self.real.connect_tcp("127.0.0.1", self.port, timeout=timeout)

    async def sleep(self, seconds: float) -> None:
        await self.real.sleep(seconds)


async def test_a_public_hostname_goes_through_with_its_host_header_and_url(
    loopback_server: LoopbackServer,
) -> None:
    port = loopback_server.port
    resolver = FakeResolver({"site.test": [PUBLIC]})
    inner = LoopbackInner(port=port)
    async with guarded_client(
        allowed_ports=frozenset({port}), resolver=resolver, inner=inner, timeout=httpx.Timeout(5.0)
    ) as client:
        response = await client.get(f"http://site.test:{port}/page")
    assert response.status_code == 200 and response.text == "ok"
    assert str(response.url) == f"http://site.test:{port}/page"
    assert inner.calls == [(PUBLIC, port)]
    [request] = loopback_server.requests
    assert f"Host: site.test:{port}".encode() in request.split(b"\r\n")


def test_the_factory_fails_closed_without_the_expected_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_init = httpx.AsyncHTTPTransport.__init__

    def init_without_pool(self: httpx.AsyncHTTPTransport, **kwargs: object) -> None:
        original_init(self, **kwargs)  # type: ignore[arg-type]
        del self._pool

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", init_without_pool)
    with pytest.raises(RuntimeError, match="guard cannot be installed"):
        guarded_client()


def test_the_factory_fails_closed_with_a_pool_of_another_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_init = httpx.AsyncHTTPTransport.__init__

    def init_with_other_pool(self: httpx.AsyncHTTPTransport, **kwargs: object) -> None:
        original_init(self, **kwargs)  # type: ignore[arg-type]
        self._pool = object()  # type: ignore[assignment]

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", init_with_other_pool)
    with pytest.raises(RuntimeError, match="guard cannot be installed"):
        guarded_client()
