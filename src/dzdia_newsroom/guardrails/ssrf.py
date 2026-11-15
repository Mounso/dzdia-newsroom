"""SSRF guard placed under the HTTP client (ADR-0018, points 1 to 4).

The guard is an httpcore network backend: on every new TCP connection it resolves the hostname
once, checks every resolved address, and connects to a checked address. The URL is never
rewritten, so SNI, certificate verification and the `Host` header keep the original hostname,
and a second DNS answer cannot change the target (DNS rebinding).
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable

import httpcore
import httpx

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type Resolver = Callable[[str, int], Awaitable[list[str]]]

DEFAULT_ALLOWED_PORTS = frozenset({80, 443})
# IPv6 prefixes that embed an IPv4 address in their last 32 bits (ADR-0018, point 2): NAT64,
# IPv4-compatible and IPv4-translated. Refused whatever IPv4 address they embed.
_EMBEDDED_IPV4_PREFIXES = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("::/96"),
    ipaddress.IPv6Network("::ffff:0:0:0/96"),
)

# httpx's own default pool limits (`httpx.AsyncHTTPTransport` with `DEFAULT_LIMITS`), kept for the
# replacement pool.
_MAX_CONNECTIONS = 100
_MAX_KEEPALIVE_CONNECTIONS = 20
_KEEPALIVE_EXPIRY_S = 5.0


class BlockedUrlError(Exception):
    """A connection refused by the guard.

    Deliberately not an httpx or httpcore exception: httpx maps only httpcore's own types, so this
    one crosses the client unchanged. Its message names the host, never a resolved address.
    """


def is_allowed_address(address: IPAddress) -> bool:
    """ADR-0018, point 2: an IPv4-mapped IPv6 address is first replaced by its IPv4 address; an
    address is then accepted only if it is global, not multicast, and not in an IPv6 prefix that
    embeds an IPv4 address (`64:ff9b::/96`, `::/96`, `::ffff:0:0:0/96`).

    `is_global` alone is true for multicast and, on some interpreters, for those three prefixes.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global or address.is_multicast:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        return not any(address in prefix for prefix in _EMBEDDED_IPV4_PREFIXES)
    return True


async def system_resolver(host: str, port: int) -> list[str]:
    """The distinct addresses of `host`, in the resolver's order."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:  # socket.gaierror is an OSError
        raise httpcore.ConnectError(f"cannot resolve host {host!r}") from exc
    addresses: list[str] = []
    for *_, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.removeprefix("[").removesuffix("]"))
    except ValueError:
        return None


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Wraps a network backend: checks the port and every address before connecting."""

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver
        self._allowed_ports = allowed_ports
        self._inner = inner if inner is not None else httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in self._allowed_ports:
            raise BlockedUrlError(f"port {port} is not allowed (host {host!r})")
        addresses = await self._checked_addresses(host, port, timeout)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = exc
        assert last_error is not None  # addresses is never empty here
        raise last_error

    async def _checked_addresses(self, host: str, port: int, timeout: float | None) -> list[str]:
        literal = _ip_literal(host)
        if literal is not None:
            if not is_allowed_address(literal):
                raise BlockedUrlError(f"host {host!r} is not allowed")
            return [str(literal)]
        try:
            async with asyncio.timeout(timeout):
                addresses = await self._resolver(host, port)
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(f"resolving host {host!r} timed out") from exc
        if not addresses:
            raise httpcore.ConnectError(f"host {host!r} has no address")
        for address in addresses:
            parsed = _ip_literal(address)
            # One refused address refuses the whole hostname (ADR-0009, point 6).
            if parsed is None or not is_allowed_address(parsed):
                raise BlockedUrlError(f"host {host!r} resolves to an address that is not allowed")
        return addresses

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedUrlError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_client(
    *,
    timeout: httpx.Timeout | None = None,
    resolver: Resolver = system_resolver,
    allowed_ports: frozenset[int] = DEFAULT_ALLOWED_PORTS,
    inner: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncClient:
    """The client for every URL that code did not choose (ADR-0018, point 3): guarded backend,
    no proxy, no environment settings, HTTP/1.1 only, no automatic redirects."""
    transport = httpx.AsyncHTTPTransport(trust_env=False, http2=False)
    # ADR-0018, point 4: httpx does not expose the network backend of its transport, so the guard
    # replaces the private `_pool`. If a later httpx renames or changes it, fail closed here; the
    # loopback refusal test in tests/guardrails/test_ssrf.py catches a pool that is ignored.
    pool = getattr(transport, "_pool", None)
    if type(pool) is not httpcore.AsyncConnectionPool:
        raise RuntimeError(
            "the SSRF guard cannot be installed: httpx.AsyncHTTPTransport has no "
            "httpcore.AsyncConnectionPool in `_pool`"
        )
    # The replaced pool is new and has opened no connection: there is nothing to close.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        max_connections=_MAX_CONNECTIONS,
        max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=_KEEPALIVE_EXPIRY_S,
        http1=True,
        http2=False,
        network_backend=GuardedBackend(resolver=resolver, allowed_ports=allowed_ports, inner=inner),
    )
    # `timeout=None` would disable every timeout in httpx: without a value, keep httpx's default.
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout if timeout is not None else httpx.Timeout(5.0),
    )
