#!/usr/bin/env python3
"""tests/test_browse_redaction.py — Security tests for browse.core.redaction.

Covers the 15 required security cases from the WBS-102 / Issue #427 Opus
security decision:

  1.  Bearer token in message redacted.
  2.  URL/query token in route dropped.
  3.  Windows path username redacted.
  4.  Unix path username redacted.
  5.  Nested dict/list attrs dropped.
  6.  Env-var-like keys dropped.
  7.  Unserializable object fail-closed.
  8.  Raw prompt/messages dropped.
  9.  Host profile token-bearing dict dropped.
  10. Raw file content under unknown key dropped; message truncation respected.
  11. Unknown kind coerced to `generic`.
  12. Non-dict attrs coerced to `{}`.
  13. Allowlisted attr string carrying JWT redacted.
  14. Malformed session_uuid dropped.
  15. `route` with query string dropped.
"""

import os
import sys
import unittest
from unittest.mock import patch

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browse.core.redaction import redact_entry  # noqa: E402


def _entry(**kwargs) -> dict:
    """Return a minimal valid BrowseDebugEntry with any extra fields merged in."""
    base: dict = {"idx": 0, "kind": "generic"}
    base.update(kwargs)
    return base


class TestBrowseRedactionCase01BearerToken(unittest.TestCase):
    """Case 1: Bearer token in message must be redacted."""

    def test_bearer_in_message_redacted(self) -> None:
        result = redact_entry(_entry(message="Authorization: Bearer supersecrettoken123"))
        msg = result.get("message", "")
        self.assertNotIn("supersecrettoken123", msg, "Bearer token must not appear in output")
        self.assertIn("[REDACTED]", msg, "Placeholder must be present")
        self.assertTrue(result["redacted"], "redacted flag must be True")

    def test_bearer_case_insensitive(self) -> None:
        result = redact_entry(_entry(message="auth: bearer abc.def.ghi"))
        self.assertNotIn("abc.def.ghi", result.get("message", ""))
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase02URLQueryTokenInRoute(unittest.TestCase):
    """Case 2: URL/query token in attrs.route must be dropped."""

    def test_route_with_token_query_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"route": "/api/v1?token=secretvalue"}))
        self.assertNotIn("route", result["attrs"], "route with query must be dropped")
        self.assertTrue(result["redacted"])

    def test_route_with_api_key_query_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"route": "/search?api_key=xyz789"}))
        self.assertNotIn("route", result["attrs"])
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase03WindowsPath(unittest.TestCase):
    """Case 3: Windows path usernames in message must be redacted."""

    def test_windows_path_username_redacted(self) -> None:
        result = redact_entry(_entry(message=r"C:\Users\alice\documents\report.pdf"))
        msg = result.get("message", "")
        self.assertNotIn("alice", msg, "Username must not appear in output")
        self.assertIn("[REDACTED]", msg, "Placeholder must be present")
        self.assertTrue(result["redacted"])

    def test_windows_path_forward_slash_redacted(self) -> None:
        result = redact_entry(_entry(message="C:/Users/bobsmith/workspace"))
        msg = result.get("message", "")
        self.assertNotIn("bobsmith", msg)
        self.assertIn("[REDACTED]", msg)


class TestBrowseRedactionCase04UnixPath(unittest.TestCase):
    """Case 4: Unix path usernames in message must be redacted."""

    def test_unix_path_username_redacted(self) -> None:
        result = redact_entry(_entry(message="/home/alice/projects/app.py"))
        msg = result.get("message", "")
        self.assertNotIn("alice", msg, "Username must not appear in output")
        self.assertIn("[REDACTED]", msg, "Placeholder must be present")
        self.assertTrue(result["redacted"])

    def test_unix_path_preserves_subpath(self) -> None:
        result = redact_entry(_entry(message="/home/charlie/src/main.py"))
        msg = result.get("message", "")
        self.assertNotIn("charlie", msg)
        # Path after username should still be intact in the scrubbed version
        self.assertIn("/home/[REDACTED]", msg)


class TestBrowseRedactionMacOSPath(unittest.TestCase):
    """macOS /Users/<name>/... path usernames in message must be redacted.

    Regression test for the finding that _redact_text lacked a macOS pattern
    and paths like /Users/alice/Documents/file.txt leaked the username.
    """

    def test_macos_path_username_redacted(self) -> None:
        result = redact_entry(_entry(message="/Users/alice/Documents/file.txt"))
        msg = result.get("message", "")
        self.assertNotIn("alice", msg, "macOS username must not appear in output")
        self.assertIn("[REDACTED]", msg, "Placeholder must be present")
        self.assertTrue(result["redacted"])

    def test_macos_path_preserves_subpath_prefix(self) -> None:
        result = redact_entry(_entry(message="/Users/charlie/src/main.py"))
        msg = result.get("message", "")
        self.assertNotIn("charlie", msg)
        self.assertIn("/Users/[REDACTED]", msg)

    def test_macos_path_mixed_case_username(self) -> None:
        result = redact_entry(_entry(message="/Users/BobSmith/workspace/project"))
        msg = result.get("message", "")
        self.assertNotIn("BobSmith", msg)
        self.assertIn("[REDACTED]", msg)

    def test_macos_path_in_attrs_string(self) -> None:
        result = redact_entry(_entry(attrs={"model": "/Users/dave/model.bin"}))
        val = result["attrs"].get("model", "")
        self.assertNotIn("dave", val)
        self.assertIn("[REDACTED]", val)
        self.assertTrue(result["redacted"])

    def test_windows_users_path_not_double_redacted(self) -> None:
        """Windows C:/Users/alice/... should be handled by WIN_PATH_RE, not double-scrubbed."""
        result = redact_entry(_entry(message="C:/Users/alice/docs/file.txt"))
        msg = result.get("message", "")
        self.assertNotIn("alice", msg)
        self.assertIn("[REDACTED]", msg)


class TestBrowseRedactionCase05NestedAttrs(unittest.TestCase):
    """Case 5: Nested dict/list values in attrs must be dropped."""

    def test_nested_dict_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"model": {"name": "gpt-4", "version": 4}}))
        self.assertNotIn("model", result["attrs"], "Nested dict must be dropped")
        self.assertTrue(result["redacted"])

    def test_nested_list_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"event_count": [1, 2, 3]}))
        self.assertNotIn("event_count", result["attrs"], "List value must be dropped")
        self.assertTrue(result["redacted"])

    def test_scalar_allowed(self) -> None:
        result = redact_entry(_entry(attrs={"event_count": 42}))
        self.assertEqual(result["attrs"].get("event_count"), 42, "Scalar int must pass through")


class TestBrowseRedactionCase06EnvKeys(unittest.TestCase):
    """Case 6: Env-var-like and other forbidden keys must be dropped."""

    def test_env_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"env": "PATH=/usr/bin:/bin"}))
        self.assertNotIn("env", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_environment_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"environment": "production"}))
        self.assertNotIn("environment", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_cwd_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"cwd": "/home/user/project"}))
        self.assertNotIn("cwd", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_http_prefix_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"http_host": "localhost:8000"}))
        self.assertNotIn("http_host", result["attrs"])
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase07FailClosed(unittest.TestCase):
    """Case 7: Any unexpected error inside redact_entry must fail closed."""

    def test_internal_exception_returns_sentinel(self) -> None:
        with patch(
            "browse.core.redaction._redact_entry_impl",
            side_effect=RuntimeError("simulated internal error"),
        ):
            result = redact_entry({"idx": 7, "kind": "tool_call"})

        self.assertTrue(result["redacted"], "Sentinel must have redacted=True")
        self.assertEqual(result["kind"], "generic", "Sentinel kind must be generic")
        self.assertEqual(result["level"], "error", "Sentinel level must be error")
        self.assertIsInstance(result["attrs"], dict, "Sentinel attrs must be dict")

    def test_non_dict_entry_does_not_raise(self) -> None:
        result = redact_entry("not a dict at all")
        self.assertIsInstance(result, dict)
        self.assertTrue(result["redacted"])

    def test_none_entry_does_not_raise(self) -> None:
        result = redact_entry(None)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase08RawPrompt(unittest.TestCase):
    """Case 8: Raw prompt/messages keys in attrs must be dropped."""

    def test_prompt_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"prompt": "What is 2 + 2?"}))
        self.assertNotIn("prompt", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_messages_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"messages": [{"role": "user", "content": "hello"}]}))
        self.assertNotIn("messages", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_completion_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"completion": "The answer is 4."}))
        self.assertNotIn("completion", result["attrs"])
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase09HostProfile(unittest.TestCase):
    """Case 9: host_profile token-bearing dicts in attrs must be dropped."""

    def test_host_profile_dict_dropped(self) -> None:
        result = redact_entry(
            _entry(
                attrs={
                    "host_profile": {"token": "bearer_abc", "url": "https://example.com"},
                }
            )
        )
        self.assertNotIn("host_profile", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_token_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"token": "abc123"}))
        self.assertNotIn("token", result["attrs"])
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase10FileContentAndTruncation(unittest.TestCase):
    """Case 10: Unknown key dropped; message truncated to MESSAGE_MAX."""

    def test_unknown_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"file_content": "a" * 1000}))
        self.assertNotIn("file_content", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_message_capped_at_2048(self) -> None:
        result = redact_entry(_entry(message="x" * 4096))
        self.assertLessEqual(len(result.get("message", "")), 2048, "message must be capped")

    def test_payload_key_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"payload": "sensitive data"}))
        self.assertNotIn("payload", result["attrs"])
        self.assertTrue(result["redacted"])


class TestBrowseRedactionCase11UnknownKind(unittest.TestCase):
    """Case 11: Unknown kind value must be coerced to 'generic'."""

    def test_unknown_kind_coerced(self) -> None:
        result = redact_entry(_entry(kind="foobar_event"))
        self.assertEqual(result["kind"], "generic")
        self.assertTrue(result["redacted"])

    def test_known_kind_preserved(self) -> None:
        result = redact_entry(_entry(kind="tool_call"))
        self.assertEqual(result["kind"], "tool_call")

    def test_missing_kind_defaults_to_generic(self) -> None:
        result = redact_entry({"idx": 0})
        self.assertEqual(result["kind"], "generic")


class TestBrowseRedactionCase12NonDictAttrs(unittest.TestCase):
    """Case 12: Non-dict attrs must be coerced to {}."""

    def test_string_attrs_coerced(self) -> None:
        result = redact_entry(_entry(attrs="not a dict"))
        self.assertEqual(result["attrs"], {})
        self.assertTrue(result["redacted"])

    def test_list_attrs_coerced(self) -> None:
        result = redact_entry(_entry(attrs=[{"key": "val"}]))
        self.assertEqual(result["attrs"], {})
        self.assertTrue(result["redacted"])

    def test_int_attrs_coerced(self) -> None:
        result = redact_entry(_entry(attrs=42))
        self.assertEqual(result["attrs"], {})
        self.assertTrue(result["redacted"])

    def test_none_attrs_allowed(self) -> None:
        # Include a valid source so that only the attrs=None case is under test.
        result = redact_entry(_entry(attrs=None, source="cli"))
        # None means attrs not supplied — should not set redacted just for this
        self.assertEqual(result["attrs"], {})
        self.assertFalse(result["redacted"], "attrs=None must not set redacted True")


class TestBrowseRedactionCase13AllowlistedAttrJWT(unittest.TestCase):
    """Case 13: Allowlisted attr string carrying a JWT must be redacted."""

    _JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"

    def test_jwt_in_model_attr_redacted(self) -> None:
        result = redact_entry(_entry(attrs={"model": self._JWT}))
        model_val = result["attrs"].get("model", "")
        self.assertNotEqual(model_val, self._JWT, "Original JWT must not pass through")
        self.assertIn("[REDACTED]", model_val, "Placeholder must be present")
        self.assertTrue(result["redacted"])

    def test_jwt_in_error_category_redacted(self) -> None:
        result = redact_entry(_entry(attrs={"error_category": self._JWT}))
        val = result["attrs"].get("error_category", "")
        self.assertNotEqual(val, self._JWT)
        self.assertIn("[REDACTED]", val)


class TestBrowseRedactionCase14MalformedSessionUUID(unittest.TestCase):
    """Case 14: Malformed session_uuid must be dropped."""

    def test_malformed_uuid_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"session_uuid": "not-a-uuid"}))
        self.assertNotIn("session_uuid", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_partial_uuid_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"session_uuid": "550e8400-e29b-41d4"}))
        self.assertNotIn("session_uuid", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_valid_uuid_kept(self) -> None:
        valid = "550e8400-e29b-41d4-a716-446655440000"
        result = redact_entry(_entry(attrs={"session_uuid": valid}))
        self.assertIn("session_uuid", result["attrs"])
        self.assertEqual(result["attrs"]["session_uuid"], valid)


class TestBrowseRedactionCase15RouteQueryString(unittest.TestCase):
    """Case 15: attrs.route with a query string must be dropped."""

    def test_route_with_query_string_dropped(self) -> None:
        result = redact_entry(_entry(attrs={"route": "/api/sessions?limit=10"}))
        self.assertNotIn("route", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_route_with_fragment_only_kept(self) -> None:
        # Fragment (#) does not constitute a query string — route is allowed
        result = redact_entry(_entry(attrs={"route": "/api/sessions"}))
        self.assertIn("route", result["attrs"])
        self.assertEqual(result["attrs"]["route"], "/api/sessions")

    def test_route_without_query_kept(self) -> None:
        result = redact_entry(_entry(attrs={"route": "/api/v2/debug-log"}, source="cli"))
        self.assertIn("route", result["attrs"])
        self.assertFalse(result["redacted"])


class TestBrowseRedactionValidators(unittest.TestCase):
    """Additional validator coverage for field rules."""

    def test_idx_negative_coerced_to_zero(self) -> None:
        result = redact_entry({"idx": -1, "kind": "generic"})
        self.assertEqual(result["idx"], 0)
        self.assertTrue(result["redacted"])

    def test_idx_string_coerced_to_zero(self) -> None:
        result = redact_entry({"idx": "five", "kind": "generic"})
        self.assertEqual(result["idx"], 0)
        self.assertTrue(result["redacted"])

    def test_invalid_timestamp_becomes_none(self) -> None:
        result = redact_entry(_entry(timestamp="not-a-date"))
        self.assertIsNone(result.get("timestamp"))
        self.assertTrue(result["redacted"])

    def test_valid_iso_timestamp_kept(self) -> None:
        ts = "2024-01-15T10:30:00Z"
        result = redact_entry(_entry(timestamp=ts))
        self.assertEqual(result["timestamp"], ts)

    def test_unknown_level_becomes_none(self) -> None:
        result = redact_entry(_entry(level="verbose", source="cli"))
        self.assertIsNone(result["level"], "Unknown level must become None")
        self.assertTrue(result["redacted"], "Unknown level must set redacted True")

    def test_unknown_source_becomes_unknown(self) -> None:
        result = redact_entry(_entry(source="custom_source"))
        self.assertEqual(result["source"], "unknown")
        self.assertTrue(result["redacted"], "Unknown source must set redacted True")

    def test_invalid_tool_name_dropped(self) -> None:
        result = redact_entry(_entry(tool_name="bad name with spaces"))
        self.assertNotIn("tool_name", result)
        self.assertTrue(result["redacted"])

    def test_tool_name_with_backslash_dropped(self) -> None:
        """Backslash must be rejected — docs advertise ^[a-zA-Z0-9_.-]{1,64}$."""
        result = redact_entry(_entry(tool_name=r"bash\tool"))
        self.assertNotIn("tool_name", result, "Backslash in tool_name must be dropped")
        self.assertTrue(result["redacted"])

    def test_valid_tool_name_kept(self) -> None:
        result = redact_entry(_entry(tool_name="bash_tool"))
        self.assertEqual(result.get("tool_name"), "bash_tool")

    def test_negative_duration_dropped(self) -> None:
        result = redact_entry(_entry(duration_ms=-5))
        self.assertNotIn("duration_ms", result)
        self.assertTrue(result["redacted"])

    def test_valid_span_id_kept(self) -> None:
        result = redact_entry(_entry(span_id="0a1b2c3d4e5f0a1b"))
        self.assertEqual(result.get("span_id"), "0a1b2c3d4e5f0a1b")

    def test_invalid_span_id_dropped(self) -> None:
        result = redact_entry(_entry(span_id="INVALID-SPAN"))
        self.assertNotIn("span_id", result)
        self.assertTrue(result["redacted"])

    def test_unknown_status_omitted_and_redacted(self) -> None:
        result = redact_entry(_entry(status="unknown_status"))
        self.assertNotIn("status", result, "Unknown status must be omitted, not coerced")
        self.assertTrue(result["redacted"])

    def test_unknown_top_level_key_dropped(self) -> None:
        result = redact_entry(_entry(raw_prompt="secret prompt text"))
        self.assertNotIn("raw_prompt", result)
        self.assertTrue(result["redacted"])

    def test_clean_entry_not_flagged(self) -> None:
        result = redact_entry(
            {
                "idx": 1,
                "kind": "tool_call",
                "level": "info",
                "source": "cli",
                "message": "Running bash",
                "tool_name": "bash",
                "duration_ms": 100,
                "status": "ok",
                "attrs": {"event_count": 3, "latency_ms": 42},
            }
        )
        self.assertFalse(result["redacted"], "Clean entry must not be flagged as redacted")


class TestBrowseRedactionWBS102Reconciliation(unittest.TestCase):
    """WBS-102 reconciliation coverage: new kind/source/status/span/duration/level/timestamp rules."""

    # ── kind ──────────────────────────────────────────────────────────────────

    def test_kind_raw_preserved_no_redaction(self) -> None:
        result = redact_entry({"idx": 0, "kind": "raw", "source": "cli"})
        self.assertEqual(result["kind"], "raw")
        self.assertFalse(result["redacted"], "kind=raw with valid source must not be flagged")

    # ── status ────────────────────────────────────────────────────────────────

    def test_status_timeout_omitted_and_redacted(self) -> None:
        result = redact_entry(_entry(status="timeout"))
        self.assertNotIn("status", result, "status=timeout must be omitted")
        self.assertTrue(result["redacted"])

    def test_status_pending_omitted_and_redacted(self) -> None:
        result = redact_entry(_entry(status="pending"))
        self.assertNotIn("status", result, "status=pending must be omitted")
        self.assertTrue(result["redacted"])

    # ── source ────────────────────────────────────────────────────────────────

    def test_source_operator_console_preserved(self) -> None:
        result = redact_entry(_entry(source="operator_console"))
        self.assertEqual(result["source"], "operator_console")

    def test_source_hook_runner_preserved(self) -> None:
        result = redact_entry(_entry(source="hook_runner"))
        self.assertEqual(result["source"], "hook_runner")

    def test_source_sk_watch_preserved(self) -> None:
        result = redact_entry(_entry(source="sk_watch"))
        self.assertEqual(result["source"], "sk_watch")

    def test_missing_source_becomes_unknown_and_redacted(self) -> None:
        result = redact_entry({"idx": 0, "kind": "generic"})  # no source key
        self.assertEqual(result["source"], "unknown")
        self.assertTrue(result["redacted"])

    # ── level ─────────────────────────────────────────────────────────────────

    def test_missing_level_preserves_none_no_redaction_trigger(self) -> None:
        """Missing level must be None and must NOT by itself trigger redacted=True."""
        result = redact_entry({"idx": 0, "kind": "generic", "source": "cli"})
        self.assertIsNone(result["level"])
        self.assertFalse(result["redacted"], "Missing level alone must not set redacted True")

    # ── span_id ───────────────────────────────────────────────────────────────

    def test_span_id_exactly_16_hex_accepted(self) -> None:
        result = redact_entry(_entry(span_id="0123456789abcdef"))
        self.assertEqual(result.get("span_id"), "0123456789abcdef")

    def test_span_id_8_chars_dropped_and_redacted(self) -> None:
        result = redact_entry(_entry(span_id="01234567"))
        self.assertNotIn("span_id", result)
        self.assertTrue(result["redacted"])

    def test_span_id_15_chars_dropped_and_redacted(self) -> None:
        result = redact_entry(_entry(span_id="0123456789abcde"))
        self.assertNotIn("span_id", result)
        self.assertTrue(result["redacted"])

    def test_span_id_17_chars_dropped_and_redacted(self) -> None:
        result = redact_entry(_entry(span_id="0123456789abcdef0"))
        self.assertNotIn("span_id", result)
        self.assertTrue(result["redacted"])

    # ── duration_ms ───────────────────────────────────────────────────────────

    def test_duration_ms_float_accepted(self) -> None:
        result = redact_entry(_entry(duration_ms=12.5, source="cli"))
        self.assertEqual(result.get("duration_ms"), 12.5)

    def test_duration_ms_zero_accepted(self) -> None:
        result = redact_entry(_entry(duration_ms=0, source="cli"))
        self.assertEqual(result.get("duration_ms"), 0)

    def test_duration_ms_bool_rejected(self) -> None:
        result = redact_entry(_entry(duration_ms=True))
        self.assertNotIn("duration_ms", result)
        self.assertTrue(result["redacted"])

    def test_duration_ms_nan_rejected(self) -> None:
        result = redact_entry(_entry(duration_ms=float("nan")))
        self.assertNotIn("duration_ms", result)
        self.assertTrue(result["redacted"])

    def test_duration_ms_inf_rejected(self) -> None:
        result = redact_entry(_entry(duration_ms=float("inf")))
        self.assertNotIn("duration_ms", result)
        self.assertTrue(result["redacted"])

    # ── idx ───────────────────────────────────────────────────────────────────

    def test_idx_bool_rejected(self) -> None:
        result = redact_entry({"idx": True, "kind": "generic"})
        self.assertEqual(result["idx"], 0)
        self.assertTrue(result["redacted"])

    # ── timestamp ─────────────────────────────────────────────────────────────

    def test_naive_iso_timestamp_dropped_and_redacted(self) -> None:
        result = redact_entry(_entry(timestamp="2024-01-15T10:30:00"))  # no TZ
        self.assertIsNone(result.get("timestamp"))
        self.assertTrue(result["redacted"])

    def test_iso_timestamp_with_offset_kept(self) -> None:
        ts = "2024-01-15T10:30:00+05:30"
        result = redact_entry(_entry(timestamp=ts, source="cli"))
        self.assertEqual(result["timestamp"], ts)


class TestBrowseRedactionNonStringMessage(unittest.TestCase):
    """Finding 1: Non-string message must be stringified AND set redacted=True."""

    def test_int_message_stringified_and_redacted(self) -> None:
        result = redact_entry(_entry(message=123))
        self.assertEqual(result.get("message"), "123", "int message must be stringified")
        self.assertTrue(result["redacted"], "int message must set redacted=True")

    def test_dict_message_stringified_and_redacted(self) -> None:
        result = redact_entry(_entry(message={"key": "value"}))
        self.assertIsInstance(result.get("message"), str, "dict message must be stringified")
        self.assertTrue(result["redacted"], "dict message must set redacted=True")

    def test_list_message_stringified_and_redacted(self) -> None:
        result = redact_entry(_entry(message=[1, 2, 3]))
        self.assertIsInstance(result.get("message"), str)
        self.assertTrue(result["redacted"])

    def test_str_message_clean_not_flagged(self) -> None:
        """A plain string message that needs no text-redaction must NOT set redacted."""
        result = redact_entry(_entry(message="hello world", source="cli"))
        self.assertEqual(result.get("message"), "hello world")
        self.assertFalse(result["redacted"], "clean string message must not set redacted")


class TestBrowseRedactionJWTFallback(unittest.TestCase):
    """Finding 2: JWT must be redacted even when redact_secrets is unavailable."""

    _JWT = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )

    def test_jwt_redacted_without_redact_secrets(self) -> None:
        """JWT in message must be redacted by local _JWT_RE when redact_secrets is unavailable."""
        import sys

        # Patch sys.modules so any attempt to import browse.core.operator_console
        # raises ImportError — simulates the module being unavailable.
        with patch.dict(sys.modules, {"browse.core.operator_console": None}):
            result = redact_entry(_entry(message=f"token={self._JWT}"))
        msg = result.get("message", "")
        self.assertNotIn("eyJ", msg, "JWT header must not appear in output")
        self.assertIn("[REDACTED]", msg, "JWT must be replaced with [REDACTED]")
        self.assertTrue(result["redacted"], "JWT in message must set redacted=True")

    def test_jwt_in_message_redacted_normally(self) -> None:
        """JWT in message must be redacted in the normal (no mock) path too."""
        result = redact_entry(_entry(message=f"auth header: {self._JWT}"))
        msg = result.get("message", "")
        self.assertNotIn(self._JWT, msg)
        self.assertIn("[REDACTED]", msg)
        self.assertTrue(result["redacted"])


class TestBrowseRedactionRoutePreservedFromTextRedaction(unittest.TestCase):
    """Finding 3: attrs.route must not be mutated by generic text-redaction patterns,
    but JWT and Bearer tokens embedded in route strings must still be scrubbed.
    """

    def test_route_with_users_path_preserved(self) -> None:
        """/Users/alice/settings is a valid HTTP route; must not trigger redacted."""
        result = redact_entry(_entry(attrs={"route": "/Users/alice/settings"}, source="cli"))
        self.assertIn("route", result["attrs"], "route must be present in output")
        self.assertEqual(
            result["attrs"]["route"],
            "/Users/alice/settings",
            "route value must be unchanged",
        )
        self.assertFalse(result["redacted"], "valid route must not set redacted=True")

    def test_route_without_query_still_kept(self) -> None:
        result = redact_entry(_entry(attrs={"route": "/api/v1/sessions"}, source="cli"))
        self.assertIn("route", result["attrs"])
        self.assertEqual(result["attrs"]["route"], "/api/v1/sessions")
        self.assertFalse(result["redacted"])

    def test_route_with_query_string_still_dropped(self) -> None:
        """Query-string rejection must still work after the route text-redaction bypass."""
        result = redact_entry(_entry(attrs={"route": "/Users/alice/settings?token=abc"}))
        self.assertNotIn("route", result["attrs"])
        self.assertTrue(result["redacted"])

    def test_route_with_jwt_in_path_segment_redacted(self) -> None:
        """JWT embedded in a route path segment must be redacted and redacted=True."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        route = f"/api/reset/{jwt}"
        result = redact_entry(_entry(attrs={"route": route}, source="cli"))
        self.assertIn("route", result["attrs"], "route key must be present (not dropped)")
        self.assertNotIn(jwt, result["attrs"]["route"], "JWT must not appear in route output")
        self.assertIn("[REDACTED]", result["attrs"]["route"], "JWT must be replaced with [REDACTED]")
        self.assertTrue(result["redacted"], "JWT in route must set redacted=True")

    def test_route_with_bearer_token_redacted(self) -> None:
        """Bearer token embedded in a route string must be redacted and redacted=True."""
        route = "/api/Bearer secrettoken123"
        result = redact_entry(_entry(attrs={"route": route}, source="cli"))
        self.assertIn("route", result["attrs"], "route key must be present (not dropped)")
        self.assertNotIn("secrettoken123", result["attrs"]["route"], "Bearer token must not appear in route output")
        self.assertIn("[REDACTED]", result["attrs"]["route"], "Bearer token must be replaced with [REDACTED]")
        self.assertTrue(result["redacted"], "Bearer token in route must set redacted=True")


if __name__ == "__main__":
    unittest.main(verbosity=2)
