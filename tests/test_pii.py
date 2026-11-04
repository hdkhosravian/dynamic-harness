"""Personal-data scanner (spec §Business governance "Personal data"): tags only, never the values."""
from __future__ import annotations

import os

import pytest

from harness_core import pii


@pytest.mark.parametrize("text, tag", [
    ("write to sara@shop.example about it", "email"),
    ("call 0912 345 6789", "phone"),
    ("call +98 912 345 6789", "phone"),
    ("شماره ۰۹۱۲۳۴۵۶۷۸۹ را بگیر", "phone"),  # Persian digits
    ("national id 0499370899", "national_id"),  # valid Iranian checksum
    ("کد ملی ۰۴۹۹۳۷۰۸۹۹", "national_id"),
    ("sheba IR062960000000100324200001", "iban"),  # valid mod-97
    ("card 4111 1111 1111 1111", "card_number"),  # Luhn-valid
])
def test_patterns_are_tagged(text, tag):
    assert tag in pii.scan(text)


@pytest.mark.parametrize("text, tag", [
    ("national id 0499370898", "national_id"),  # checksum fails
    ("sheba IR062960000000100324200002", "iban"),  # mod-97 fails
    ("card 4111 1111 1111 1112", "card_number"),  # Luhn fails
    ("invoice 1111111111 total", "national_id"),  # all-same digits are not an id
    ("version 2.1.296", "phone"),
])
def test_lookalikes_are_not_tagged(text, tag):
    assert tag not in pii.scan(text)


def test_csv_header_names_tag_columns(tmp_path):
    f = tmp_path / "staff.csv"
    f.write_text("نام,حقوق,کد ملی\nعلی,1000,x\n", encoding="utf-8")
    tags = pii.scan(f)
    assert {"person_name", "salary", "national_id"} <= set(tags)


def test_english_header_names(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("Customer Name,Email,Mobile,Amount Owed\n", encoding="utf-8")
    assert {"person_name", "email", "phone"} <= set(pii.scan(f))


def test_scan_never_returns_the_matched_values():
    out = pii.scan("mail sara@shop.example")
    assert all("sara" not in t for t in out)


def test_a_symlinked_file_is_not_followed(tmp_path):
    (tmp_path / "real.csv").write_text("email\n")
    (tmp_path / "link.csv").symlink_to(tmp_path / "real.csv")
    assert pii.scan(tmp_path / "link.csv") == []


def test_large_files_are_read_only_up_to_the_cap(tmp_path):
    f = tmp_path / "big.txt"
    f.write_bytes(b"x" * (pii.MAX_BYTES + 10) + b" sara@shop.example")
    assert "email" not in pii.scan(f)


def test_missing_or_unreadable_file_gives_no_tags(tmp_path):
    assert pii.scan(tmp_path / "missing.csv") == []
    f = tmp_path / "locked.csv"
    f.write_text("email\n")
    os.chmod(f, 0)
    try:
        assert pii.scan(f) == []
    finally:
        os.chmod(f, 0o600)


def test_redact_replaces_values_with_tags():
    out = pii.redact("mail sara@shop.example or call 09123456789")
    assert "sara@shop.example" not in out and "09123456789" not in out
    assert "[email]" in out and "[phone]" in out


def test_scan_is_linear_on_adversarial_input():
    import time
    t = time.monotonic()
    for s in ("x" * pii.MAX_BYTES, "a." * (pii.MAX_BYTES // 2), "+1 " * (pii.MAX_BYTES // 3), "9" * pii.MAX_BYTES):
        pii.scan(s)
        pii.redact(s)
    assert time.monotonic() - t < 1.0
