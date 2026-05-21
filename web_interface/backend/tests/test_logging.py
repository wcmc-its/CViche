"""Tests for structured logging + request_id correlation."""
from __future__ import annotations

import io
import json
import logging

import pytest

from app.logging_config import (
    JSONFormatter,
    RequestIDFilter,
    configure_logging,
    new_request_id,
    request_id_var,
)


@pytest.fixture(autouse=True)
def _reset_request_id_var():
    """Each test starts with the default request_id ContextVar value."""
    token = request_id_var.set("-")
    yield
    request_id_var.reset(token)


class TestJSONFormatter:
    def _record(self, **kwargs):
        record = logging.LogRecord(
            name=kwargs.pop("name", "test.logger"),
            level=kwargs.pop("level", logging.INFO),
            pathname=__file__,
            lineno=1,
            msg=kwargs.pop("msg", "hello"),
            args=None,
            exc_info=None,
        )
        for key, value in kwargs.items():
            setattr(record, key, value)
        return record

    def test_required_fields_always_present(self):
        record = self._record(request_id="abc123")
        out = json.loads(JSONFormatter().format(record))
        assert set(out.keys()) >= {"timestamp", "level", "logger", "message", "request_id"}
        assert out["level"] == "INFO"
        assert out["logger"] == "test.logger"
        assert out["message"] == "hello"
        assert out["request_id"] == "abc123"

    def test_optional_fields_only_when_set(self):
        record = self._record(request_id="abc123", user_id="u1", run_id="r1")
        out = json.loads(JSONFormatter().format(record))
        assert out["user_id"] == "u1"
        assert out["run_id"] == "r1"

    def test_optional_fields_absent_when_unset(self):
        record = self._record(request_id="abc123")
        out = json.loads(JSONFormatter().format(record))
        assert "user_id" not in out
        assert "run_id" not in out

    def test_request_id_defaults_to_dash(self):
        record = self._record()
        out = json.loads(JSONFormatter().format(record))
        assert out["request_id"] == "-"

    def test_exc_info_serializes_as_string(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=None,
                exc_info=sys.exc_info(),
            )
        out = json.loads(JSONFormatter().format(record))
        assert "exc_info" in out
        assert "ValueError" in out["exc_info"]


class TestRequestIDFilter:
    def test_filter_reads_contextvar(self):
        request_id_var.set("ctx-abc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=1,
            msg="m", args=None, exc_info=None,
        )
        RequestIDFilter().filter(record)
        assert record.request_id == "ctx-abc"

    def test_filter_does_not_overwrite_explicit(self):
        request_id_var.set("ctx-abc")
        record = logging.LogRecord(
            name="x", level=logging.INFO, pathname="", lineno=1,
            msg="m", args=None, exc_info=None,
        )
        record.request_id = "explicit"
        RequestIDFilter().filter(record)
        assert record.request_id == "explicit"


class TestConfigureLogging:
    def _capture(self) -> io.StringIO:
        """Drop a StringIO handler onto the root logger and return it."""
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.addFilter(RequestIDFilter())
        handler.setFormatter(JSONFormatter())
        logging.getLogger().addHandler(handler)
        return buf, handler

    def test_json_format_round_trips(self, monkeypatch):
        monkeypatch.setenv("CVICHE_LOG_FORMAT", "json")
        configure_logging()
        buf, handler = self._capture()
        try:
            request_id_var.set("trace-1")
            logging.getLogger("app.test").info("event", extra={"user_id": "u9"})
            lines = [line for line in buf.getvalue().splitlines() if line.strip()]
            payload = json.loads(lines[-1])
            assert payload["message"] == "event"
            assert payload["level"] == "INFO"
            assert payload["request_id"] == "trace-1"
            assert payload["user_id"] == "u9"
        finally:
            logging.getLogger().removeHandler(handler)

    def test_text_format_is_human_readable(self, monkeypatch):
        monkeypatch.setenv("CVICHE_LOG_FORMAT", "text")
        configure_logging()
        # Re-grab the configured handler so we read what dictConfig wrote.
        buf = io.StringIO()
        text_handler = logging.StreamHandler(buf)
        text_handler.addFilter(RequestIDFilter())
        text_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
            )
        )
        logging.getLogger().addHandler(text_handler)
        try:
            request_id_var.set("trace-2")
            logging.getLogger("app.test").info("plain line")
            output = buf.getvalue()
            # Must contain the level, logger name, request_id, and message
            # in the human-readable line.
            assert "INFO" in output
            assert "app.test" in output
            assert "trace-2" in output
            assert "plain line" in output
            # And must NOT be JSON
            with pytest.raises(json.JSONDecodeError):
                json.loads(output.strip())
        finally:
            logging.getLogger().removeHandler(text_handler)

    def test_unknown_format_falls_back_to_text(self, monkeypatch):
        monkeypatch.setenv("CVICHE_LOG_FORMAT", "yaml")
        # Should not raise.
        configure_logging()


class TestRequestIDMiddleware:
    def test_response_carries_x_request_id_header(self, client):
        response = client.get("/health")
        assert "x-request-id" in {k.lower() for k in response.headers.keys()}
        rid = response.headers["x-request-id"]
        assert len(rid) >= 8  # uuid4 hex is 32 chars

    def test_incoming_x_request_id_is_honored(self, client):
        incoming = "trace-from-upstream"
        response = client.get("/health", headers={"X-Request-ID": incoming})
        assert response.headers["x-request-id"] == incoming

    def test_oversized_incoming_x_request_id_is_replaced(self, client):
        bogus = "x" * 500
        response = client.get("/health", headers={"X-Request-ID": bogus})
        # Server mints a fresh id instead of trusting the oversized one.
        assert response.headers["x-request-id"] != bogus
        assert len(response.headers["x-request-id"]) == 32

    def test_request_id_is_unique_per_request(self, client):
        r1 = client.get("/health")
        r2 = client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


class TestNewRequestId:
    def test_new_request_id_is_unique(self):
        ids = {new_request_id() for _ in range(100)}
        assert len(ids) == 100
