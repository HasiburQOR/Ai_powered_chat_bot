"""LLM adapter tests — the layer every other test mocks away.

Regression suite for the 2026-09-13 production outage: the module-level
variable `_session` shadowed the `_session()` accessor of the same name, so
the accessor returned the FUNCTION instead of a requests.Session and every
provider call died instantly with "AttributeError: 'function' object has no
attribute 'post'" — 100% of chat messages fell back while the bot-engine
tests stayed green, because they all mock get_adapter. These tests execute
the REAL adapter code against a real (localhost) HTTP server, no mocks.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import requests

from django.test import SimpleTestCase

from llm.adapters import LLMProviderError, OpenAICompatibleAdapter, _session


class _StubOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-shaped /chat/completions responder."""

    def do_POST(self):
        body = json.dumps(
            {"choices": [{"message": {"content": "pong"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


class _StubUnauthorizedHandler(BaseHTTPRequestHandler):
    """HTTP 401 + a provider-style error body: auth/quota problems are only
    diagnosable if that body survives into the raised error (and the log)."""

    def do_POST(self):
        body = b'{"error": {"message": "Invalid API key provided"}}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SessionHelperTests(SimpleTestCase):
    def test_session_helper_returns_a_real_pooled_session(self):
        """Regression for the production outage: `_session` the VARIABLE used
        to shadow `_session()` the function — the helper returned itself and
        every adapter call raised AttributeError instead of making an HTTP
        request."""
        first = _session()
        second = _session()
        self.assertIsInstance(first, requests.Session)
        self.assertIs(first, second)  # pooled, not rebuilt per call


class OpenAICompatibleAdapterTests(SimpleTestCase):
    """The REAL adapter code against a REAL local HTTP server — the code path
    that silently never worked in production."""

    def _start_stub(self, handler):
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        return server

    def _config(self, port):
        return SimpleNamespace(
            api_base_url=f"http://127.0.0.1:{port}/v1",
            api_key="test-key", model_name="stub-model",
            temperature=0.7, max_tokens=100)

    def test_adapter_completes_a_real_http_round_trip(self):
        server = self._start_stub(_StubOpenAIHandler)
        reply = OpenAICompatibleAdapter().send(
            [{"role": "user", "content": "ping"}],
            self._config(server.server_port))
        self.assertEqual(reply, "pong")

    def test_http_error_surfaces_the_provider_body(self):
        server = self._start_stub(_StubUnauthorizedHandler)
        with self.assertRaises(LLMProviderError) as ctx:
            OpenAICompatibleAdapter().send(
                [{"role": "user", "content": "ping"}],
                self._config(server.server_port))
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertIn("Invalid API key provided", str(ctx.exception))