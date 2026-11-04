from __future__ import annotations

import pytest

from harness_core import egress
from harness_core.errors import Denied
from harness_core.safety import RESEARCHER, Ctx


def _ctx(tmp_path, **kw):
    kw.setdefault("allowlist", ["docs.python.org", "example.org"])
    return Ctx(workspace=tmp_path, **kw)


@pytest.mark.parametrize(
    "url",
    ["https://docs.python.org/3/library/", "https://api.example.org/x", "HTTPS://EXAMPLE.ORG./a"],
)
def test_allowlisted(tmp_path, url):
    egress.check(url, _ctx(tmp_path))


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/",
        "https://docs.python.org.evil.com/",
        "https://evilexample.org/",
        "https://example.org@evil.com/",
        "https://evil.com\\@example.org/",
        "ftp://example.org/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "not a url",
        "https://exa mple.org/",
    ],
)
def test_denied(tmp_path, url):
    with pytest.raises(Denied):
        egress.check(url, _ctx(tmp_path))


def test_default_allowlist_loaded_from_control(tmp_path):
    with pytest.raises(Denied):
        egress.check("https://somewhere.example/", Ctx(workspace=tmp_path))


def test_grant_only_for_researcher(tmp_path):
    grant = {"domains": ["news.example.ir"]}
    egress.check("https://news.example.ir/a", _ctx(tmp_path, agent_type=RESEARCHER, grant=grant))
    with pytest.raises(Denied):
        egress.check("https://news.example.ir/a", _ctx(tmp_path, grant=grant))
    with pytest.raises(Denied):  # grant domains match exactly, not subdomains
        egress.check("https://leak.news.example.ir/a", _ctx(tmp_path, agent_type=RESEARCHER, grant=grant))


def test_host_of():
    assert egress.host_of("https://Docs.Python.org:443/x") == "docs.python.org"
