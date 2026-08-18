"""Tests for app.client_ip.get_client_ip."""
from starlette.requests import Request

from app.client_ip import get_client_ip


def _make_request(headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None) -> Request:
    scope = {
        "type": "http",
        "headers": headers,
        "client": client,
    }
    return Request(scope)


def test_multi_entry_xff_returns_last_entry_not_first():
    request = _make_request(
        headers=[(b"x-forwarded-for", b"1.2.3.4, 10.0.0.9")],
        client=("203.0.113.5", 12345),
    )
    assert get_client_ip(request) == "10.0.0.9"


def test_xff_absent_falls_back_to_request_client_host():
    request = _make_request(
        headers=[],
        client=("203.0.113.5", 12345),
    )
    assert get_client_ip(request) == "203.0.113.5"


def test_xff_present_but_empty_falls_back_to_request_client_host():
    request = _make_request(
        headers=[(b"x-forwarded-for", b"")],
        client=("203.0.113.5", 12345),
    )
    assert get_client_ip(request) == "203.0.113.5"


def test_no_client_and_no_xff_returns_none():
    request = _make_request(
        headers=[],
        client=None,
    )
    assert get_client_ip(request) is None
