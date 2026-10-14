"""HTTP regression tests; credentials, DNS, and provider requests are synthetic."""

import importlib
import json
import os
import socket
import threading
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import requests


# Do not load the workspace's .env or use credentials inherited by the runner.
with patch("dotenv.load_dotenv"), patch.dict(
    os.environ,
    {
        "OPENAI_API_URL": "https://configured.example/v1",
        "OPENAI_API_KEY": "synthetic-server-key",
        "OPENAI_MODEL": "configured-model",
        "FLASK_DEBUG": "false",
    },
    clear=True,
):
    backend = importlib.import_module("app")


SERVER_KEY = "synthetic-server-key"
DRAFT_KEY = "synthetic-draft-key"
DRAFT_SETTINGS = {
    "base_url": "https://draft.example/v1",
    "api_key": DRAFT_KEY,
    "model": "draft-model",
}
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def completion(content="translated", finish_reason="stop"):
    return SimpleNamespace(
        status_code=200,
        json=lambda: {
            "choices": [
                {"message": {"content": content}, "finish_reason": finish_reason}
            ]
        },
        text="",
    )


def provider_error(status_code):
    # Providers may echo secrets; public errors must not relay this body.
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: {"error": {"message": DRAFT_KEY}},
        text=DRAFT_KEY,
        headers={"Retry-After": "0"},
    )


def translation_request(kwargs):
    """Read the outgoing provider protocol, without replacing application logic."""
    messages = kwargs["json"]["messages"]
    system = "\n".join(item["content"] for item in messages if item["role"] == "system")
    user = next(item["content"] for item in messages if item["role"] == "user")
    if "语言检测" in system:
        return "detection", []
    if "多语言分组翻译" in system:
        return "group", list(json.loads(user)["target_languages"])
    for code, name in backend.SUPPORTED_LANGUAGES.items():
        if "翻译成" + name in user.split("\n\n", 1)[0]:
            return "single", [code]
    return "connection", []


def grouped_completion(translations, fenced=False):
    content = json.dumps({"translations": translations}, ensure_ascii=False)
    if fenced:
        content = "```json\n" + content + "\n```"
    return completion(content)


class TranslatorApiTestCase(unittest.TestCase):
    def setUp(self):
        backend.app.config.update(TESTING=True)
        self.client = backend.app.test_client()
        for name, value in (
            ("OPENAI_COMPATIBLE_API_URL", "https://configured.example/v1"),
            ("OPENAI_API_KEY", SERVER_KEY),
            ("OPENAI_MODEL", "configured-model"),
        ):
            patcher = patch.object(backend, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        dns = patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
        self.dns = dns.start()
        self.addCleanup(dns.stop)
        provider = patch("app.requests.post", return_value=completion())
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def assert_json_error(self, response, status=None):
        if status is not None:
            self.assertEqual(response.status_code, status)
        else:
            self.assertGreaterEqual(response.status_code, 400)
        self.assertTrue(response.is_json)
        body = response.get_json()
        self.assertIsInstance(body, dict)
        self.assertIsInstance(body.get("error"), str)
        self.assertTrue(body["error"])
        self.assertNotIn(SERVER_KEY, response.get_data(as_text=True))
        self.assertNotIn(DRAFT_KEY, response.get_data(as_text=True))
        return body


class TranslatorApiTests(TranslatorApiTestCase):
    def test_translation_routes_reject_invalid_json_and_text_without_requests(self):
        for route in ("/translate", "/translate_all"):
            for payload in (
                ["not", "an", "object"],
                "not an object",
                {"text": 123},
                {"text": " \n\t "},
                {"text": "a" * 5001},
            ):
                with self.subTest(route=route, payload_type=type(payload).__name__):
                    self.assert_json_error(self.client.post(route, json=payload), 400)
        self.provider.assert_not_called()

    def test_batch_requires_a_nonempty_supported_language_list(self):
        for languages in ("en", [], ["unknown"], ["en", "unknown"], [123]):
            with self.subTest(languages=languages):
                self.assert_json_error(
                    self.client.post(
                        "/translate_all", json={"text": "hello", "languages": languages}
                    ),
                    400,
                )
        self.provider.assert_not_called()

    def test_settings_are_trimmed_before_provider_request(self):
        response = self.client.post(
            "/translate",
            json={
                "text": "hello",
                "target_language": "zh",
                "api_settings": {
                    "base_url": "  https://draft.example/v1/  ",
                    "api_key": "  " + DRAFT_KEY + "  ",
                    "model": "  draft-model  ",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        args, kwargs = self.provider.call_args
        self.assertEqual(args[0], "https://draft.example/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + DRAFT_KEY)
        self.assertEqual(kwargs["json"]["model"], "draft-model")
        self.assertIs(kwargs["allow_redirects"], False)

    def test_settings_reject_invalid_types(self):
        for settings in (
            ["not a settings object"],
            {"base_url": 123},
            {"api_key": ["bad"]},
            {"model": 123},
        ):
            with self.subTest(settings=settings):
                self.assert_json_error(
                    self.client.post(
                        "/translate",
                        json={"text": "hello", "target_language": "zh", "api_settings": settings},
                    ),
                    400,
                )
        self.provider.assert_not_called()

    def test_keyless_custom_url_falls_back_instead_of_inheriting_server_credentials(self):
        # The browser drops the key each session while base_url and model persist,
        # so a stored endpoint routinely arrives without its key.
        for key in (None, "", "   "):
            settings = {"base_url": DRAFT_SETTINGS["base_url"], "model": "draft-model"}
            if key is not None:
                settings["api_key"] = key
            with self.subTest(key=key):
                self.provider.reset_mock()
                response = self.client.post(
                    "/translate",
                    json={"text": "hello", "target_language": "zh", "api_settings": settings},
                )
                self.assertEqual(response.status_code, 200)
                args, kwargs = self.provider.call_args
                # The incomplete endpoint is dropped together with its model.
                self.assertEqual(args[0], "https://configured.example/v1/chat/completions")
                self.assertEqual(kwargs["json"]["model"], "configured-model")
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SERVER_KEY)
        # The server credential never reaches the client-supplied endpoint.
        for call in self.provider.call_args_list:
            self.assertNotIn("draft.example", call.args[0])

    def test_keyless_custom_url_is_rejected_when_no_server_endpoint_exists(self):
        with patch.object(backend, "OPENAI_COMPATIBLE_API_URL", ""):
            self.assert_json_error(
                self.client.post(
                    "/translate",
                    json={
                        "text": "hello",
                        "target_language": "zh",
                        "api_settings": {"base_url": DRAFT_SETTINGS["base_url"]},
                    },
                ),
                400,
            )
            # Blank settings cannot be resolved either, but must stay a clean 400.
            self.assert_json_error(
                self.client.post("/translate", json={"text": "hello", "target_language": "zh"}),
                400,
            )
        self.provider.assert_not_called()

    def test_blank_client_settings_fall_back_to_the_server_configuration(self):
        for settings in (
            None,
            {},
            {"base_url": "", "api_key": "", "model": ""},
            {"base_url": "   ", "api_key": "   ", "model": "   "},
            # An endpoint equal to the configured one is not a custom endpoint.
            {"base_url": "https://configured.example/v1/", "api_key": "", "model": ""},
        ):
            payload = {"text": "hello", "target_language": "zh"}
            if settings is not None:
                payload["api_settings"] = settings
            with self.subTest(settings=settings):
                self.provider.reset_mock()
                response = self.client.post("/translate", json=payload)
                self.assertEqual(response.status_code, 200)
                args, kwargs = self.provider.call_args
                self.assertEqual(args[0], "https://configured.example/v1/chat/completions")
                self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SERVER_KEY)
                self.assertEqual(kwargs["json"]["model"], "configured-model")

    def test_model_alone_overrides_the_server_model_on_the_server_endpoint(self):
        response = self.client.post(
            "/translate",
            json={
                "text": "hello",
                "target_language": "zh",
                "api_settings": {"base_url": "", "api_key": "", "model": "override-model"},
            },
        )
        self.assertEqual(response.status_code, 200)
        args, kwargs = self.provider.call_args
        self.assertEqual(args[0], "https://configured.example/v1/chat/completions")
        self.assertEqual(kwargs["json"]["model"], "override-model")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SERVER_KEY)

    def test_keyless_custom_url_also_falls_back_for_batch_and_connection_routes(self):
        settings = {"base_url": DRAFT_SETTINGS["base_url"], "api_key": "", "model": "draft-model"}
        for route, payload in (
            ("/translate_all", {"text": "hello", "languages": ["zh"], "api_settings": settings}),
            ("/test_connection", {"api_settings": settings}),
        ):
            with self.subTest(route=route):
                self.provider.reset_mock()
                response = self.client.post(route, json=payload)
                self.assertEqual(response.status_code, 200)
                for call in self.provider.call_args_list:
                    self.assertEqual(
                        call.args[0], "https://configured.example/v1/chat/completions"
                    )
                    self.assertEqual(call.kwargs["json"]["model"], "configured-model")

    def test_custom_url_with_its_own_key_still_bypasses_the_server_configuration(self):
        response = self.client.post(
            "/translate",
            json={"text": "hello", "target_language": "zh", "api_settings": DRAFT_SETTINGS},
        )
        self.assertEqual(response.status_code, 200)
        args, kwargs = self.provider.call_args
        self.assertEqual(args[0], "https://draft.example/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + DRAFT_KEY)
        self.assertEqual(kwargs["json"]["model"], "draft-model")
        # Falling back must not weaken SSRF screening for real custom endpoints.
        self.dns.assert_called()

    def test_invalid_header_keys_are_rejected_for_draft_and_server_settings(self):
        for key in ("synthetic-非ASCII-key", "synthetic\x7fkey", "synthetic key"):
            with self.subTest(source="draft", key=key):
                self.assert_json_error(
                    self.client.post(
                        "/test_connection",
                        json={"api_settings": {**DRAFT_SETTINGS, "api_key": key}},
                    ),
                    400,
                )
            with self.subTest(source="server", key=key), patch.object(
                backend, "OPENAI_API_KEY", key
            ):
                self.assert_json_error(
                    self.client.post(
                        "/translate", json={"text": "Hello", "target_language": "zh"}
                    ),
                    400,
                )
        self.provider.assert_not_called()

    def test_connection_uses_the_unsaved_draft_settings(self):
        response = self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.is_json)
        self.assertIs(response.get_json().get("ok"), True)
        self.provider.assert_called_once()
        args, kwargs = self.provider.call_args
        self.assertEqual(args[0], DRAFT_SETTINGS["base_url"] + "/chat/completions")
        self.assertEqual(kwargs["json"]["model"], DRAFT_SETTINGS["model"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + DRAFT_KEY)
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertNotIn(SERVER_KEY, str(self.provider.call_args))

    def test_connection_reports_provider_credential_and_rate_limit_errors(self):
        for provider_status in (401, 429):
            with self.subTest(provider_status=provider_status):
                self.provider.return_value = provider_error(provider_status)
                response = self.client.post(
                    "/test_connection", json={"api_settings": DRAFT_SETTINGS}
                )
                self.assert_json_error(response)

    def test_connection_reports_timeouts_without_exposing_exception_details(self):
        self.provider.side_effect = requests.Timeout("timeout includes " + DRAFT_KEY)
        self.assert_json_error(
            self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
        )

    def test_connection_rejects_invalid_or_empty_provider_content(self):
        responses = (
            SimpleNamespace(status_code=200, json=lambda: {}, text=""),
            completion(" \n "),
            completion(None),
            SimpleNamespace(
                status_code=200,
                json=lambda: (_ for _ in ()).throw(ValueError(DRAFT_KEY)),
                text=DRAFT_KEY,
            ),
        )
        for index, result in enumerate(responses):
            with self.subTest(response=index):
                self.provider.return_value = result
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
                )

    def test_connection_accepts_nonempty_truncated_output_but_rejects_empty_output(self):
        self.provider.return_value = completion("OK", finish_reason="length")
        response = self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.get_json()["ok"], True)

        self.provider.return_value = completion("  \n", finish_reason="length")
        self.assert_json_error(
            self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
            502,
        )

    def test_reasoning_only_output_proves_connection_but_is_not_a_translation(self):
        reasoning_text = "synthetic private reasoning that must not reach the browser"
        for content in (None, ""):
            for reasoning_field in ("reasoning_content", "reasoning"):
                with self.subTest(content=content, reasoning_field=reasoning_field):
                    payload = {
                        "choices": [
                            {
                                "message": {"content": content, reasoning_field: reasoning_text},
                                "finish_reason": "length",
                            }
                        ]
                    }
                    self.provider.return_value = SimpleNamespace(
                        status_code=200, json=lambda: payload, text=""
                    )
                    response = self.client.post(
                        "/test_connection", json={"api_settings": DRAFT_SETTINGS}
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIs(response.get_json()["ok"], True)
                    self.assertNotIn(reasoning_text, response.get_data(as_text=True))
                    self.assert_json_error(
                        self.client.post(
                            "/translate",
                            json={"text": "Hello", "target_language": "zh"},
                        ),
                        502,
                    )
        for reasoning_value in (None, "", " \n ", 123):
            with self.subTest(invalid_reasoning=reasoning_value):
                payload = {
                    "choices": [
                        {
                            "message": {"content": None, "reasoning_content": reasoning_value},
                            "finish_reason": "length",
                        }
                    ]
                }
                self.provider.return_value = SimpleNamespace(
                    status_code=200, json=lambda: payload, text=""
                )
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
                    502,
                )

    def test_spanish_detection_produces_an_english_translation(self):
        self.provider.side_effect = [completion("es"), completion("Hello, friend")]
        response = self.client.post(
            "/translate_all", json={"text": "Hola amigo", "languages": ["en"]}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["source_language"], "es")
        self.assertEqual(self.provider.call_count, 2)
        result = body["results"][0]
        self.assertEqual(result["language"], "en")
        self.assertIs(result["ok"], True)
        self.assertIs(result["is_original"], False)
        self.assertEqual(result["translated_text"], "Hello, friend")
        self.assertEqual((body["success_count"], body["failed_count"]), (1, 0))

    def test_failed_latin_detection_does_not_skip_the_english_translation(self):
        self.provider.side_effect = [provider_error(401), completion("Hello, friend")]
        response = self.client.post(
            "/translate_all", json={"text": "Hola amigo", "languages": ["en"]}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["source_language"], "unknown")
        self.assertEqual(self.provider.call_count, 2)
        self.assertIs(body["results"][0]["is_original"], False)
        self.assertEqual(body["results"][0]["translated_text"], "Hello, friend")

    def test_partial_batch_success_preserves_language_order_and_result_errors(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            if kind == "group":
                return grouped_completion({"fr": "Bonjour"})
            if languages == ["es"]:
                return provider_error(429)
            return completion("Bonjour")

        self.provider.side_effect = answer
        response = self.client.post(
            "/translate_all", json={"text": "Hello", "languages": ["fr", "en", "es"]}
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([item["language"] for item in body["results"]], ["fr", "en", "es"])
        self.assertEqual((body["success_count"], body["failed_count"]), (2, 1))
        french, english, spanish = body["results"]
        self.assertIs(french["ok"], True)
        self.assertEqual(french["translated_text"], "Bonjour")
        self.assertIs(english["ok"], True)
        self.assertIs(english["is_original"], True)
        self.assertEqual(english["translated_text"], "Hello")
        self.assertIs(spanish["ok"], False)
        self.assertIs(spanish["is_original"], False)
        self.assertTrue(spanish["error"])
        self.assertNotIn(DRAFT_KEY, response.get_data(as_text=True))

    def test_all_failed_batch_keeps_individual_results(self):
        def answer(url, **kwargs):
            prompt = "\n".join(message["content"] for message in kwargs["json"]["messages"])
            return completion("zh") if "检测" in prompt else provider_error(401)

        self.provider.side_effect = answer
        response = self.client.post(
            "/translate_all", json={"text": "你好", "languages": ["en", "fr"]}
        )
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.is_json)
        body = response.get_json()
        self.assertEqual([item["language"] for item in body["results"]], ["en", "fr"])
        self.assertEqual((body["success_count"], body["failed_count"]), (0, 2))
        for result in body["results"]:
            self.assertIs(result["ok"], False)
            self.assertIs(result["is_original"], False)
            self.assertTrue(result["error"])

    def test_truncated_translation_is_an_error(self):
        self.provider.return_value = completion("partial translation", finish_reason="length")
        self.assert_json_error(
            self.client.post("/translate", json={"text": "Hello", "target_language": "zh"}),
            502,
        )

    def test_custom_internal_urls_are_rejected_before_provider_requests(self):
        for url in ("http://127.0.0.1/v1", "http://10.0.0.1/v1", "http://[::1]/v1"):
            with self.subTest(url=url):
                address = urlsplit(url).hostname
                self.dns.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))
                ]
                settings = {**DRAFT_SETTINGS, "base_url": url}
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": settings}),
                    400,
                )
        self.provider.assert_not_called()

    def test_hostname_resolving_to_internal_ip_is_rejected(self):
        self.dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 443))
        ]
        self.assert_json_error(
            self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
            400,
        )
        self.provider.assert_not_called()

    def test_ascii_host_is_screened_as_the_host_that_is_requested(self):
        # urllib3 percent-decodes unreserved octets in a host and percent-encodes
        # illegal ones, so an ASCII name can also be screened in one form and
        # connected to in another: admin%2Einternal resolves to admin.internal.
        for host in ("x%2Dy.attacker.test", "admin%2Einternal.attacker.test",
                     "a^b.attacker.test", "a|b.attacker.test", "a`b.attacker.test"):
            with self.subTest(host=host):
                settings = {**DRAFT_SETTINGS, "base_url": "https://" + host + "/v1"}
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": settings}),
                    400,
                )
        self.dns.assert_not_called()
        self.provider.assert_not_called()

    def test_accepted_hosts_survive_the_encoding_requests_applies(self):
        # The guard is only sound if every accepted host is already in the form
        # urllib3 emits; otherwise a rewrite could still reintroduce a mismatch.
        for host, expected in (
            ("api.openai.com", "api.openai.com"),
            ("API.Example.COM", "api.example.com"),
            ("example.com.", "example.com."),
            ("faß.example.com", "xn--fa-hia.example.com"),
            # An ASCII label keeps characters UTS-46 would reject, matching the
            # per-label encoding urllib3 performs.
            ("api_v1.münchen.de", "api_v1.xn--mnchen-3ya.de"),
            ("_acme.faß.de", "_acme.xn--fa-hia.de"),
        ):
            with self.subTest(host=host):
                normalized = backend.validate_base_url("https://" + host + "/v1")
                screened = urlsplit(normalized).hostname
                self.assertEqual(screened, expected)
                prepared = requests.Request("POST", normalized).prepare()
                self.assertEqual(urlsplit(prepared.url).hostname, screened)

    def test_ipv6_literal_that_is_not_an_address_is_rejected(self):
        self.assert_json_error(
            self.client.post("/test_connection", json={"api_settings": {
                **DRAFT_SETTINGS, "base_url": "https://[not::an::address]/v1"}}),
            400,
        )
        self.provider.assert_not_called()

    def test_unicode_host_is_screened_as_the_host_that_is_requested(self):
        # socket.getaddrinfo applies IDNA2003 nameprep (ß becomes ss) while requests
        # encodes with UTS-46 (ß is kept). Screening one name and requesting another
        # would let a crafted domain resolve past the private-address check.
        settings = {**DRAFT_SETTINGS, "base_url": "https://faß.example.com/v1"}
        response = self.client.post("/test_connection", json={"api_settings": settings})
        self.assertEqual(response.status_code, 200)
        screened_host = self.dns.call_args.args[0]
        requested_host = urlsplit(self.provider.call_args.args[0]).hostname
        self.assertEqual(screened_host, "xn--fa-hia.example.com")
        self.assertEqual(screened_host, requested_host)
        # An ASCII A-label is passed through unchanged, so no later encoding step
        # can reintroduce the mismatch.
        prepared = requests.Request("POST", self.provider.call_args.args[0]).prepare()
        self.assertEqual(urlsplit(prepared.url).hostname, screened_host)

    def test_unicode_host_is_screened_for_the_batch_and_translate_routes(self):
        settings = {**DRAFT_SETTINGS, "base_url": "https://faß.example.com/v1"}
        for route, payload in (
            ("/translate", {"text": "hello", "target_language": "zh", "api_settings": settings}),
            ("/translate_all", {"text": "hello", "languages": ["zh"], "api_settings": settings}),
        ):
            with self.subTest(route=route):
                self.provider.reset_mock()
                self.assertEqual(self.client.post(route, json=payload).status_code, 200)
                for call in self.provider.call_args_list:
                    self.assertEqual(
                        urlsplit(call.args[0]).hostname, "xn--fa-hia.example.com"
                    )

    def test_hostnames_that_cannot_be_encoded_are_rejected_without_requests(self):
        for host in ("​.example.com", "ß" * 200 + ".com", "example.ـ"):
            with self.subTest(host=host):
                settings = {**DRAFT_SETTINGS, "base_url": "https://" + host + "/v1"}
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": settings}),
                    400,
                )
        self.dns.assert_not_called()
        self.provider.assert_not_called()

    def test_nat64_address_embedding_an_internal_target_is_rejected(self):
        # is_global reports a NAT64 address as public, but it tunnels to the
        # embedded IPv4 destination.
        for address in ("64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "64:ff9b::c0a8:102"):
            with self.subTest(address=address):
                self.dns.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))
                ]
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
                    400,
                )
        self.provider.assert_not_called()

    def test_nat64_address_embedding_a_public_target_is_allowed(self):
        self.dns.return_value = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::5db8:d822", 443, 0, 0))
        ]
        response = self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
        self.assertEqual(response.status_code, 200)
        self.provider.assert_called_once()

    def test_ipv6_forms_carrying_an_internal_ipv4_target_are_rejected(self):
        # ipaddress reports each of these as global even though the IPv4 address
        # they carry is not reachable from the internet.
        for address, note in (
            ("::ffff:100.64.0.1", "ipv4-mapped CGNAT"),
            ("::ffff:0:7f00:1", "ipv4-translated loopback"),
            ("::7f00:1", "ipv4-compatible loopback"),
            ("::ffff:0:a9fe:a9fe", "ipv4-translated link-local"),
            ("2002:7f00:1::", "6to4 loopback"),
        ):
            with self.subTest(note=note):
                self.dns.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))
                ]
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
                    400,
                )
        self.provider.assert_not_called()

    def test_multicast_and_relay_anycast_targets_are_rejected(self):
        # Multicast is outside is_private, and 6to4 relay anycast forwards on the
        # caller's behalf, so neither is covered by the is_global check alone.
        for family, address, note in (
            (socket.AF_INET, "224.0.0.1", "all-hosts multicast"),
            (socket.AF_INET, "239.255.255.250", "SSDP discovery"),
            (socket.AF_INET, "192.88.99.1", "6to4 relay anycast"),
            (socket.AF_INET6, "ff02::1", "IPv6 link-local multicast"),
        ):
            with self.subTest(note=note):
                sockaddr = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
                self.dns.return_value = [(family, socket.SOCK_STREAM, 6, "", sockaddr)]
                self.assert_json_error(
                    self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS}),
                    400,
                )
        self.provider.assert_not_called()

    def test_public_ipv6_targets_are_still_allowed(self):
        for address in ("2606:4700:4700::1111", "::ffff:8.8.8.8"):
            with self.subTest(address=address):
                self.provider.reset_mock()
                self.dns.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))
                ]
                response = self.client.post(
                    "/test_connection", json={"api_settings": DRAFT_SETTINGS}
                )
                self.assertEqual(response.status_code, 200)
                self.provider.assert_called_once()

    def test_effective_configuration_reports_the_server_as_the_source(self):
        # Blank page fields are the case users misread as "the server config is
        # ignored", so the response has to name what was actually used.
        response = self.client.post("/test_connection", json={"api_settings": {
            "base_url": "", "api_key": "", "model": ""}})
        self.assertEqual(response.status_code, 200)
        effective = response.get_json()["effective"]
        self.assertEqual(effective["base_url"], "https://configured.example/v1")
        self.assertEqual(effective["model"], "configured-model")
        self.assertEqual(effective["base_url_source"], "server")
        self.assertEqual(effective["key_source"], "server")
        self.assertEqual(effective["model_source"], "server")
        self.assertFalse(effective["fell_back_to_server"])

    def test_effective_configuration_reports_the_page_as_the_source(self):
        response = self.client.post("/test_connection", json={"api_settings": DRAFT_SETTINGS})
        self.assertEqual(response.status_code, 200)
        effective = response.get_json()["effective"]
        self.assertEqual(effective["base_url"], "https://draft.example/v1")
        self.assertEqual(effective["model"], "draft-model")
        for field in ("base_url_source", "key_source", "model_source"):
            self.assertEqual(effective[field], "page")
        self.assertFalse(effective["fell_back_to_server"])

    def test_effective_configuration_flags_the_silent_fallback(self):
        # The browser drops its key each session, so this is what a returning
        # user actually submits; the substitution must not stay invisible.
        response = self.client.post("/test_connection", json={"api_settings": {
            "base_url": "https://draft.example/v1", "api_key": "", "model": "draft-model"}})
        self.assertEqual(response.status_code, 200)
        effective = response.get_json()["effective"]
        self.assertTrue(effective["fell_back_to_server"])
        self.assertEqual(effective["base_url"], "https://configured.example/v1")
        self.assertEqual(effective["model"], "configured-model")
        self.assertEqual(effective["key_source"], "server")

    def test_effective_configuration_is_reported_when_the_endpoint_rejects_it(self):
        # A 401 from the provider is the exact moment the configuration in use
        # needs to be visible, so the failure branch carries it too.
        self.provider.return_value = provider_error(401)
        response = self.client.post("/test_connection", json={"api_settings": {
            "base_url": "", "api_key": "", "model": ""}})
        body = self.assert_json_error(response, 502)
        self.assertEqual(body["effective"]["base_url"], "https://configured.example/v1")
        self.assertEqual(body["effective"]["key_source"], "server")

    def test_effective_configuration_fingerprints_the_key_without_disclosing_it(self):
        for settings, expected_key in (
            ({"base_url": "", "api_key": "", "model": ""}, SERVER_KEY),
            (DRAFT_SETTINGS, DRAFT_KEY),
        ):
            with self.subTest(key=expected_key):
                response = self.client.post(
                    "/test_connection", json={"api_settings": settings}
                )
                self.assertEqual(response.status_code, 200)
                hint = response.get_json()["effective"]["api_key_hint"]
                # Enough to spot a duplicated prefix or a truncated paste...
                self.assertTrue(hint.startswith(expected_key[:6]))
                self.assertIn(str(len(expected_key)), hint)
                # ...but never a credential anyone could replay.
                self.assertNotIn(expected_key, hint)
                self.assertNotIn(expected_key, response.get_data(as_text=True))
                self.assertLess(len(hint.split("…")[0]), len(expected_key))

    def test_language_api_and_page_share_the_text_length_limit(self):
        response = self.client.get("/languages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["max_text_length"], 5000)

        class TextareaParser(HTMLParser):
            source_text_attributes = None

            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                if tag == "textarea" and attributes.get("id") == "source-text":
                    self.source_text_attributes = attributes

        # A changed server limit must also reach the page; a hardcoded fallback
        # would hide a disagreement between browser and API validation.
        with patch.object(backend, "MAX_TEXT_LENGTH", 4321):
            api_limit = self.client.get("/languages").get_json()["max_text_length"]
            page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        parser = TextareaParser()
        parser.feed(page.get_data(as_text=True))
        self.assertIsNotNone(parser.source_text_attributes)
        self.assertEqual(
            parser.source_text_attributes.get("data-max-text-length"), str(api_limit)
        )
        self.provider.assert_not_called()


    def test_page_assets_have_content_versions_and_valid_content_types(self):
        class AssetParser(HTMLParser):
            urls = []

            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                url = attributes.get('href' if tag == 'link' else 'src', '')
                if url.startswith('/static/'):
                    self.urls.append(url)

        parser = AssetParser()
        parser.feed(self.client.get('/').get_data(as_text=True))
        self.assertEqual(len(parser.urls), 2)
        for url in parser.urls:
            with self.subTest(url=url):
                self.assertIn('?v=', url)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(response.mimetype, ('text/css', 'text/javascript'))
                self.assertGreater(len(response.get_data()), 1000)
                response.close()
        self.provider.assert_not_called()


class GroupedTranslationTests(TranslatorApiTestCase):
    targets = ["en", "es", "pt", "fr", "ja", "it", "de"]

    def answer_successfully(self, url, **kwargs):
        kind, languages = translation_request(kwargs)
        if kind == "detection":
            return completion("zh")
        if kind == "group":
            return grouped_completion({code: "translated-" + code for code in languages})
        return completion("translated-" + languages[0])

    def translation_calls(self):
        return [
            (kind, languages, call.kwargs["json"]["max_tokens"])
            for call in self.provider.call_args_list
            for kind, languages in [translation_request(call.kwargs)]
            if kind in ("group", "single")
        ]

    def post_batch(self, languages, text="你好"):
        return self.client.post("/translate_all", json={"text": text, "languages": languages})

    def test_seven_targets_need_three_translation_requests(self):
        self.provider.side_effect = self.answer_successfully
        response = self.post_batch(self.targets)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        calls = self.translation_calls()
        self.assertEqual(len(calls), 3)
        self.assertEqual(sorted(len(codes) for _, codes, _ in calls), [1, 3, 3])
        self.assertEqual(self.provider.call_count, 4)  # One detection plus three translations.
        self.assertEqual([item["language"] for item in body["results"]], self.targets)
        self.assertEqual((body["success_count"], body["failed_count"]), (7, 0))
        for result in body["results"]:
            self.assertEqual(result["translated_text"], "translated-" + result["language"])

    def test_confirmed_original_is_excluded_from_group_requests(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            return grouped_completion({code: "translated-" + code for code in languages})

        self.provider.side_effect = answer
        response = self.post_batch(["fr", "en", "es", "pt"], text="Hello")
        self.assertEqual(response.status_code, 200)
        calls = self.translation_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], ["fr", "es", "pt"])
        original = response.get_json()["results"][1]
        self.assertIs(original["is_original"], True)
        self.assertEqual(original["translated_text"], "Hello")

    def test_missing_language_is_the_only_single_language_fallback(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            if kind == "group":
                return grouped_completion({"fr": "group-fr", "pt": "group-pt"})
            return completion("fallback-" + languages[0])

        self.provider.side_effect = answer
        response = self.post_batch(["fr", "es", "pt"], text="Hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(kind, codes) for kind, codes, _ in self.translation_calls()],
            [("group", ["fr", "es", "pt"]), ("single", ["es"])],
        )
        self.assertEqual(
            [item["translated_text"] for item in response.get_json()["results"]],
            ["group-fr", "fallback-es", "group-pt"],
        )

    def test_invalid_group_json_falls_back_once_for_each_language(self):
        for content in ("not JSON", "[]", "null", '{"translations": []}'):
            with self.subTest(content=content):
                self.provider.reset_mock()

                def answer(url, **kwargs):
                    kind, languages = translation_request(kwargs)
                    if kind == "detection":
                        return completion("en")
                    if kind == "group":
                        return completion(content)
                    return completion("fallback-" + languages[0])

                self.provider.side_effect = answer
                response = self.post_batch(["fr", "es"], text="Hello")
                self.assertEqual(response.status_code, 200)
                calls = self.translation_calls()
                self.assertEqual(len(calls), 3)
                self.assertEqual(
                    Counter(code for kind, codes, _ in calls if kind == "single" for code in codes),
                    Counter({"fr": 1, "es": 1}),
                )
                self.assertEqual(response.get_json()["success_count"], 2)

    def test_fenced_group_json_is_accepted_without_fallback(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            return grouped_completion({code: "fenced-" + code for code in languages}, fenced=True)

        self.provider.side_effect = answer
        response = self.post_batch(["fr", "es"], text="Hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.translation_calls()), 1)
        self.assertEqual(
            [item["translated_text"] for item in response.get_json()["results"]],
            ["fenced-fr", "fenced-es"],
        )

    def test_empty_or_invalid_items_retry_only_failed_languages(self):
        for invalid in (None, "", " \n ", {"unexpected": "object"}, 123):
            with self.subTest(invalid=invalid):
                self.provider.reset_mock()

                def answer(url, **kwargs):
                    kind, languages = translation_request(kwargs)
                    if kind == "detection":
                        return completion("en")
                    if kind == "group":
                        return grouped_completion({"fr": "group-fr", "es": invalid})
                    return completion("fallback-" + languages[0])

                self.provider.side_effect = answer
                response = self.post_batch(["fr", "es"], text="Hello")
                self.assertEqual(response.status_code, 200)
                calls = self.translation_calls()
                self.assertEqual([(kind, codes) for kind, codes, _ in calls], [
                    ("group", ["fr", "es"]), ("single", ["es"])
                ])
                self.assertEqual(response.get_json()["results"][0]["translated_text"], "group-fr")

    def test_truncated_group_output_falls_back_without_using_partial_translations(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            if kind == "group":
                return completion(
                    json.dumps({"translations": {code: "partial" for code in languages}}),
                    finish_reason="length",
                )
            return completion("complete-" + languages[0])

        self.provider.side_effect = answer
        response = self.post_batch(["fr", "es"], text="Hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.translation_calls()), 3)
        self.assertEqual(
            [item["translated_text"] for item in response.get_json()["results"]],
            ["complete-fr", "complete-es"],
        )

    def test_failed_fallback_is_not_repeated_and_partial_result_order_is_preserved(self):
        def answer(url, **kwargs):
            kind, languages = translation_request(kwargs)
            if kind == "detection":
                return completion("en")
            if kind == "group":
                return completion("not JSON")
            if languages == ["fr"]:
                return provider_error(500)
            return completion("complete-es")

        self.provider.side_effect = answer
        response = self.post_batch(["fr", "es"], text="Hello")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([item["language"] for item in body["results"]], ["fr", "es"])
        self.assertEqual((body["success_count"], body["failed_count"]), (1, 1))
        self.assertIs(body["results"][0]["ok"], False)
        self.assertTrue(body["results"][0]["error"])
        self.assertEqual(len(self.translation_calls()), 3)
        self.assertNotIn(DRAFT_KEY, response.get_data(as_text=True))

    def test_permanent_group_errors_do_not_fan_out_into_single_requests(self):
        for error in (provider_error(401), provider_error(403), provider_error(404),
                      completion("blocked", finish_reason="content_filter")):
            with self.subTest(error_status=error.status_code):
                self.provider.reset_mock()

                def answer(url, **kwargs):
                    kind, _ = translation_request(kwargs)
                    return completion("en") if kind == "detection" else error

                self.provider.side_effect = answer
                response = self.post_batch(["fr", "es", "pt"], text="Hello")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.translation_calls()), 1)
                body = response.get_json()
                self.assertEqual((body["success_count"], body["failed_count"]), (0, 3))
                self.assertTrue(all(not result["ok"] for result in body["results"]))
                self.assertNotIn(DRAFT_KEY, response.get_data(as_text=True))

    def test_transient_group_errors_get_one_single_fallback_per_language(self):
        for error in (provider_error(429), provider_error(500),
                      requests.ConnectionError("synthetic connection failure " + DRAFT_KEY),
                      requests.Timeout("synthetic timeout " + DRAFT_KEY)):
            with self.subTest(error_type=type(error).__name__):
                self.provider.reset_mock()

                def answer(url, **kwargs):
                    kind, languages = translation_request(kwargs)
                    if kind == "detection":
                        return completion("en")
                    if kind == "group":
                        if isinstance(error, Exception):
                            raise error
                        return error
                    return completion("fallback-" + languages[0])

                self.provider.side_effect = answer
                response = self.post_batch(["fr", "es"], text="Hello")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(self.translation_calls()), 3)
                self.assertEqual(response.get_json()["success_count"], 2)

    def test_single_target_retries_a_transient_error_only_once(self):
        self.provider.side_effect = [completion("en"), provider_error(500), completion("complete-fr")]
        response = self.post_batch(["fr"], text="Hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.translation_calls()), 2)
        self.assertEqual(response.get_json()["results"][0]["translated_text"], "complete-fr")

        self.provider.reset_mock()
        self.provider.side_effect = [completion("en"), provider_error(500), provider_error(500)]
        response = self.post_batch(["fr"], text="Hello")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(self.translation_calls()), 2)

    def test_long_text_reduces_groups_and_never_exceeds_output_budget(self):
        self.provider.side_effect = self.answer_successfully
        for length, expected_sizes in ((1800, [1, 2, 2]), (4800, [1, 1, 1, 1, 1])):
            with self.subTest(length=length):
                self.provider.reset_mock()
                response = self.post_batch(self.targets[:5], text="a" * length)
                self.assertEqual(response.status_code, 200)
                calls = self.translation_calls()
                self.assertEqual(sorted(len(codes) for _, codes, _ in calls), expected_sizes)
                self.assertTrue(all(0 < budget <= 12000 for _, _, budget in calls))
                self.assertEqual(response.get_json()["success_count"], 5)

    def test_same_and_cross_batch_provider_concurrency_is_at_most_two(self):
        for batch_count in (1, 2):
            with self.subTest(batch_count=batch_count):
                self.provider.reset_mock()
                lock = threading.Lock()
                release = threading.Event()
                two_translations_blocked = threading.Event()
                overflow = threading.Event()
                ready = threading.Barrier(batch_count + 1)
                state = {"active": 0, "translations": 0, "peak": 0}

                def answer(url, **kwargs):
                    kind, languages = translation_request(kwargs)
                    with lock:
                        state["active"] += 1
                        state["peak"] = max(state["peak"], state["active"])
                        if state["active"] > 2:
                            overflow.set()
                        if kind != "detection":
                            state["translations"] += 1
                            if state["translations"] >= 2:
                                two_translations_blocked.set()
                    try:
                        if kind == "detection":
                            return completion("zh")
                        if not release.wait(5):
                            raise AssertionError("Test failed to release blocked provider requests")
                        if kind == "group":
                            return grouped_completion({code: "translated-" + code for code in languages})
                        return completion("translated-" + languages[0])
                    finally:
                        with lock:
                            state["active"] -= 1
                            if kind != "detection":
                                state["translations"] -= 1

                def send_batch():
                    with backend.app.test_client() as client:
                        ready.wait(5)
                        return client.post("/translate_all", json={
                            "text": "你好", "languages": self.targets
                        })

                self.provider.side_effect = answer
                with ThreadPoolExecutor(max_workers=batch_count) as clients:
                    futures = [clients.submit(send_batch) for _ in range(batch_count)]
                    try:
                        ready.wait(5)
                        self.assertTrue(two_translations_blocked.wait(5))
                        # Both slots stay occupied while further groups and the
                        # second HTTP batch compete for admission. No sleeps.
                        self.assertFalse(overflow.wait(0.2))
                    finally:
                        release.set()
                    responses = [future.result(timeout=5) for future in futures]
                self.assertEqual(state["peak"], 2)
                self.assertEqual(state["active"], 0)
                self.assertTrue(all(response.status_code == 200 for response in responses))


INDEX_SETTINGS = {"provider": "index"}


class IndexTranslateProviderTests(TranslatorApiTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (
            ("TRANSLATION_PROVIDER", "openai"),
            ("INDEX_TRANSLATE_API_URL", "https://index.example/v1"),
            ("INDEX_TRANSLATE_MODEL", "Index-Translate-35B-A3B"),
        ):
            patcher = patch.object(backend, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_single_translation_uses_the_index_protocol_without_credentials(self):
        self.provider.return_value = completion("Hello, world.")
        response = self.client.post("/translate", json={
            "text": "你好，世界。", "target_language": "en",
            "api_settings": {**INDEX_SETTINGS, "base_url": "https://draft.example/v1",
                             "api_key": DRAFT_KEY, "model": "draft-model"},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["translated_text"], "Hello, world.")
        args, kwargs = self.provider.call_args
        self.assertEqual(args[0], "https://index.example/v1/chat/completions")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["User-Agent"], backend.INDEX_TRANSLATE_USER_AGENT)
        payload = kwargs["json"]
        # A model saved for the other provider is not sent to Index-Translate.
        self.assertEqual(payload["model"], "Index-Translate-35B-A3B")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(payload["messages"], [{
            "role": "user",
            "content": "请将以下文本翻译为英语，直接输出翻译结果，不要进行任何解释。\n\n你好，世界。",
        }])
        self.assertNotIn(DRAFT_KEY, json.dumps(kwargs, default=str))

    def test_index_model_from_the_page_is_used(self):
        self.client.post("/translate", json={
            "text": "hi", "target_language": "zh",
            "api_settings": {**INDEX_SETTINGS, "model": "Index-Translate-9B"},
        })
        self.assertEqual(self.provider.call_args.kwargs["json"]["model"], "Index-Translate-9B")

    def test_server_default_provider_applies_to_blank_page_settings(self):
        with patch.object(backend, "TRANSLATION_PROVIDER", "index"):
            self.client.post("/translate", json={"text": "hi", "target_language": "zh"})
            languages = self.client.get("/languages").get_json()
        self.assertEqual(self.provider.call_args.args[0], "https://index.example/v1/chat/completions")
        self.assertEqual(languages["default_provider"], "index")

    def test_unknown_provider_is_rejected_without_requests(self):
        response = self.client.post("/translate", json={
            "text": "hi", "target_language": "zh", "api_settings": {"provider": "other"},
        })
        self.assert_json_error(response, 400)
        self.provider.assert_not_called()

    def test_think_block_is_removed_from_the_translation(self):
        self.provider.return_value = completion("<think>\n\n</think>\n\nHello")
        response = self.client.post("/translate", json={
            "text": "你好", "target_language": "en", "api_settings": INDEX_SETTINGS,
        })
        self.assertEqual(response.get_json()["translated_text"], "Hello")

    def test_batch_sends_one_request_per_language_and_skips_ai_detection(self):
        def answer(*args, **kwargs):
            prompt = kwargs["json"]["messages"][0]["content"]
            return completion("译文:" + prompt.split("，", 1)[0])

        self.provider.side_effect = answer
        response = self.client.post("/translate_all", json={
            "text": "Hola mundo", "languages": ["en", "ja", "fr"], "api_settings": INDEX_SETTINGS,
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["source_language"], "unknown")
        self.assertIs(body["source_confirmed"], False)
        self.assertEqual([item["language"] for item in body["results"]], ["en", "ja", "fr"])
        self.assertEqual(body["results"][1]["translated_text"], "译文:请将以下文本翻译为日语")
        self.assertEqual(self.provider.call_count, 3)
        for call in self.provider.call_args_list:
            self.assertEqual(len(call.kwargs["json"]["messages"]), 1)

    def test_connection_reports_the_index_configuration(self):
        response = self.client.post("/test_connection", json={"api_settings": INDEX_SETTINGS})
        self.assertEqual(response.status_code, 200)
        effective = response.get_json()["effective"]
        self.assertEqual(effective["provider"], "index")
        self.assertEqual(effective["base_url"], "https://index.example/v1")
        self.assertEqual(effective["api_key_hint"], "")
        self.assertIn("翻译为英语", self.provider.call_args.kwargs["json"]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
