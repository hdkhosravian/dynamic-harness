"""Egress allowlist and per-job research grant (spec §Irreversible-action control)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .errors import Denied
from .protected import load_control

RESEARCHER = "dynamic-harness:web-researcher"
_BAD = re.compile(r"[\s\\\x00-\x1f\x7f]")


def host_of(url) -> str:
    """Lower-case IDNA host of an http(s) URL; Denied for anything ambiguous."""
    if not isinstance(url, str) or _BAD.search(url):
        raise Denied("egress: malformed URL")
    try:
        u = urlsplit(url)
        u.port  # raises ValueError on a bad port
    except ValueError as e:
        raise Denied(f"egress: malformed URL ({e})") from e
    if u.scheme.lower() not in ("http", "https"):
        raise Denied("egress: only http(s) is allowed")
    if u.username is not None or u.password is not None:
        raise Denied("egress: credentials in URL")
    host = (u.hostname or "").rstrip(".")
    if not host:
        raise Denied("egress: no host")
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as e:
        raise Denied("egress: bad host name") from e


def _default_port(url) -> int:
    return 443 if urlsplit(url).scheme.lower() == "https" else 80


def _entry(d: str):
    """``host`` or ``host:port`` allowlist/grant entry -> (host, port or None)."""
    d = d.lower().strip()
    m = re.fullmatch(r"(.*?):(\d{1,5})", d)
    host, port = (m.group(1), int(m.group(2))) if m else (d, None)
    return host.lstrip("*").lstrip(".").rstrip("."), port


def _port_ok(url, port) -> bool:
    """The URL's port must equal the entry's; an entry without one admits only the scheme default (443/80)."""
    return (urlsplit(url).port or _default_port(url)) == (port or _default_port(url))


def _suffix_match(url: str, domains) -> bool:
    host = host_of(url)
    for d in domains or ():
        d, port = _entry(d)
        if d and (host == d or host.endswith("." + d)) and _port_ok(url, port):
            return True
    return False


def allowlist(ctx) -> list:
    al = getattr(ctx, "allowlist", None)
    return list(al) if al is not None else load_control("egress_allowlist.json", ctx).get("domains", [])


def on_allowlist(url, ctx) -> bool:
    return _suffix_match(url, allowlist(ctx))


def grant_covers(url, ctx) -> bool:
    """Researcher lane only; grant domains match exactly (no subdomain channel)."""
    grant = getattr(ctx, "grant", None)
    if getattr(ctx, "agent_type", None) != RESEARCHER or not grant:
        return False
    host = host_of(url)
    return any(host == d and _port_ok(url, port) for d, port in (_entry(x) for x in grant.get("domains", [])))


def check(url, ctx) -> None:
    if on_allowlist(url, ctx) or grant_covers(url, ctx):
        return
    raise Denied(f"egress: {host_of(url)} is not on the allowlist or a research grant")
