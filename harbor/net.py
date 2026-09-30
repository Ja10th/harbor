"""URL checks for pages Harbor reads. SMTP is separate."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def is_public_url(url: str) -> bool:
    """True for ordinary public http(s) pages. Does not resolve DNS."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 80, 443):
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "0.0.0.0"} or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return _public_ip(ip)


def resolves_public(host: str) -> bool:
    """False if any resolved address is private, local, or link-local."""
    host = (host or "").lower().rstrip(".")
    if not host or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        addresses = [info[4][0] for info in infos]
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not _public_ip(ip):
            return False
    return True


def smtp_host_ok(host: str) -> bool:
    """Block link-local metadata addresses. A local mail server is allowed."""
    host = (host or "").strip().lower().rstrip(".")
    if not host or host.startswith("169.254.") or host == "metadata.google.internal":
        return False
    return True


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
