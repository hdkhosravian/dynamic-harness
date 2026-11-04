from __future__ import annotations

import pytest

from harness_core import query_guard
from harness_core.errors import Denied


@pytest.mark.parametrize(
    "text",
    [
        "small shop pricing trends",
        "قیمت لوازم خانگی بازار تهران",
        "https://example.com/news/market-report",
        "https://fa.wikipedia.org/wiki/%D8%A8%D8%A7%D8%B2%D8%A7%D8%B1",  # Persian, percent-encoded
        "https://example.com/search?q=retail+trends&lang=fa",
    ],
)
def test_clean_queries_pass(text):
    query_guard.check(text, [], [])


@pytest.mark.parametrize(
    "text",
    [
        "x" * 65,
        "https://example.com/" + "a" * 65,
        "https://example.com/?d=c2VjcmV0LXRva2VuLWZvci1leGZpbHRyYXRpb24tdGVzdA",  # base64, high entropy
        "https://example.com/" + "%41" * 70,  # percent-encoded long segment
        "invoice 4821 status",
        "شماره ۱۲۳۴۵",  # Persian digits are digits
        "https://example.com/p/987654",
        "aB3$kL9#mN2@pQ7&rS5!",  # 20 distinct chars: entropy > 4.0
    ],
)
def test_bad_queries_denied(text):
    with pytest.raises(Denied):
        query_guard.check(text, [], [])


def test_entropy_on_decoded_text():
    # Encoded Persian is high-entropy as %XX but low-entropy once decoded
    assert query_guard.entropy("%D8%A8%D8%A7%D8%B2%D8%A7%D8%B1") > query_guard.entropy("بازار")
    query_guard.check("https://example.com/%D8%A8%D8%A7%D8%B2%D8%A7%D8%B1", [], [])


def test_double_encoding_is_decoded():
    with pytest.raises(Denied):
        query_guard.check("https://example.com/?q=acme%2520corp", ["Acme Corp"], [])


@pytest.mark.parametrize(
    "text, term",
    [
        ("price list for ACME corp", "Acme Corp"),
        ("قیمت علي رضايي", "علی رضایی"),  # Arabic yeh vs Persian yeh
        ("بانك ملت", "بانک ملت"),  # Arabic kaf vs Persian keheh
        ("https://example.com/?q=acme-corp", "acme corp"),
    ],
)
def test_protected_terms_normalised(text, term):
    with pytest.raises(Denied):
        query_guard.check(text, [term], [])


def test_owner_tokens():
    with pytest.raises(Denied):
        query_guard.check("supplier zarinmehr prices", [], ["Zarinmehr"])
    query_guard.check("supplier prices", [], ["Zarinmehr"])


def test_reason_is_reported():
    with pytest.raises(Denied) as e:
        query_guard.check("order 12345", [], [])
    assert "digit" in e.value.reason
