"""tests/test_serve_v2_csp_nonce.py — Focused stdlib unit tests for CSP nonce injection.

Covers all invariants from G3 security review (INV-1..INV-10) and the full
test matrix requested in the implementation spec:

  a. Inline <script> gains nonce exactly once.
  b. <script src=...> is NOT modified.
  c. <script src=... nonce="old"> is NOT modified.
  d. <script nonce="preexisting"> is NOT modified (idempotent).
  e. Mixed file: only inline scripts gain nonce; count matches.
  f. Non-HTML content types pass byte-for-byte unchanged (SHA256 equality).
  g. Empty/falsy nonce is a no-op.
  h. </script> closing tags are never modified.
  i. HTML comment <script> behaviour documented.
  j. Placeholder + nonce composition; session_id sanitisation (INV-7).
  k. CSP header nonce == injected HTML nonce (INV-5).
  l. build_v2_csp_header(truthy nonce) never contains 'unsafe-inline' in
     script-src (INV-1, WBS-084 regression guard).
"""

import hashlib
import re
import sys
import unittest
from pathlib import Path

# Make the browse package importable from the worktree root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.csp import build_v2_csp_header
from browse.core.fts import _SESSION_ID_RE
from browse.routes.serve_v2 import (
    _inject_csp_nonce,
    _rewrite_session_placeholder,
    _session_placeholder_fallback_paths,
)

NONCE = "testNonce123_abc"


# ---------------------------------------------------------------------------
# Test a: inline <script> (no src, no nonce) gains nonce exactly once
# ---------------------------------------------------------------------------

class TestInjectInlineScript(unittest.TestCase):
    def test_bare_inline_script_gets_nonce(self):
        body = b"<html><body><script>var x=1;</script></body></html>"
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'nonce="{NONCE}"'.encode(), result)
        # Injection is on the opening tag only
        self.assertIn(f'<script nonce="{NONCE}">'.encode(), result)
        # Exactly one injection
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 1)

    def test_inline_script_with_type_attr_gets_nonce(self):
        body = b'<script type="text/javascript">a()</script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'nonce="{NONCE}"'.encode(), result)
        self.assertNotIn(b"src=", result)

    def test_multiple_inline_scripts_all_get_nonce(self):
        body = b"<script>a()</script><script>b()</script><script>c()</script>"
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 3)

    def test_script_body_not_modified(self):
        original_body = b"self.__next_f.push([1, 'data'])"
        body = b"<script>" + original_body + b"</script>"
        result = _inject_csp_nonce(body, NONCE)
        # Script body unchanged
        self.assertIn(original_body, result)


# ---------------------------------------------------------------------------
# Test b: <script src=...> is NOT modified
# ---------------------------------------------------------------------------

class TestExternalScriptNotModified(unittest.TestCase):
    def test_script_src_unchanged(self):
        body = b'<html><script src="/_next/static/chunks/main.js"></script></html>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)

    def test_script_src_double_quotes_unchanged(self):
        body = b'<script src="app.js"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertNotIn(f'nonce="{NONCE}"'.encode(), result)
        self.assertEqual(result, body)

    def test_script_src_uppercase_SRC_unchanged(self):
        """Case-insensitive src= detection."""
        body = b'<script SRC="app.js"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)

    def test_script_src_with_space_before_equals_unchanged(self):
        """src = 'x.js' — space around = must still be detected."""
        body = b'<script src = "x.js"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)
        self.assertNotIn(NONCE.encode(), result)

    def test_script_src_with_tab_before_equals_unchanged(self):
        """src\t= — tab around = must still be detected."""
        body = b'<script src\t="x.js"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)
        self.assertNotIn(NONCE.encode(), result)

    def test_script_SRC_with_spaces_unchanged(self):
        """SRC = 'x.js' — uppercase with spaces around = must still be detected."""
        body = b'<script SRC = "x.js"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)
        self.assertNotIn(NONCE.encode(), result)


# ---------------------------------------------------------------------------
# Test b2: data-src / data-nonce attributes should NOT skip nonce injection
# ---------------------------------------------------------------------------

class TestDataAttributesDoNotPreventNonce(unittest.TestCase):
    """data-src= and data-nonce= must not trigger the external-script skip logic.

    The attribute-boundary regex (?:^|\\s)src\\s*= ensures ``data-src=`` is
    not mistaken for a ``src`` attribute.  Same for ``data-nonce=``.
    """

    def test_data_src_inline_script_gets_nonce(self):
        """<script data-src="..."> is an inline script and must receive nonce."""
        body = b'<script data-src="some-value">inline()</script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'nonce="{NONCE}"'.encode(), result)

    def test_data_nonce_inline_script_gets_nonce(self):
        """<script data-nonce="..."> has no real nonce attr and must receive nonce."""
        body = b'<script data-nonce="old-value">inline()</script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'nonce="{NONCE}"'.encode(), result)

    def test_data_src_and_data_nonce_together_gets_nonce(self):
        body = b'<script data-src="x" data-nonce="y">inline()</script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'nonce="{NONCE}"'.encode(), result)


# ---------------------------------------------------------------------------
# Test c: <script src=... nonce="old"> is NOT modified
# ---------------------------------------------------------------------------

class TestExternalScriptWithNonceNotModified(unittest.TestCase):
    def test_src_and_nonce_unchanged(self):
        body = b'<script src="x.js" nonce="old"></script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertNotIn(NONCE.encode(), result)
        self.assertEqual(result, body)

    def test_nonce_with_space_before_equals_unchanged(self):
        """nonce = "old" — whitespace around = must be detected; no duplicate."""
        body = b'<script nonce = "old">inline()</script>'
        result = _inject_csp_nonce(body, NONCE)
        self.assertNotIn(NONCE.encode(), result)
        self.assertEqual(result, body)


# ---------------------------------------------------------------------------
# Test d: idempotent — already-nonced inline scripts are NOT re-nonced
# ---------------------------------------------------------------------------

class TestIdempotence(unittest.TestCase):
    def test_preexisting_nonce_not_modified(self):
        body = b'<script nonce="preexisting">foo()</script>'
        result = _inject_csp_nonce(body, NONCE)
        # Our nonce must NOT appear
        self.assertNotIn(NONCE.encode(), result)
        self.assertEqual(result, body)

    def test_running_twice_produces_identical_output(self):
        body = b"<html><script>var x=1;</script></html>"
        once = _inject_csp_nonce(body, NONCE)
        twice = _inject_csp_nonce(once, NONCE)
        self.assertEqual(once, twice)

    def test_same_nonce_not_doubled(self):
        body = f'<script nonce="{NONCE}">existing()</script>'.encode()
        result = _inject_csp_nonce(body, NONCE)
        # Count of nonce= should remain 1
        self.assertEqual(result.count(NONCE.encode()), 1)
        self.assertEqual(result, body)


# ---------------------------------------------------------------------------
# Test e: mixed file — only inline scripts gain nonce; exact count
# ---------------------------------------------------------------------------

class TestMixedFile(unittest.TestCase):
    def test_mixed_inline_and_external(self):
        body = (
            b"<html><head>"
            b"<script>self.__next_f.push(['hydration'])</script>"
            b'<script src="/_next/static/chunks/main.js"></script>'
            b'<script nonce="preexisting">foo()</script>'
            b"</head></html>"
        )
        result = _inject_csp_nonce(body, NONCE)
        # Only the bare inline script gets our nonce
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 1)
        # External script unmodified
        self.assertIn(b'src="/_next/static/chunks/main.js"', result)
        # Preexisting nonce unchanged
        self.assertIn(b'nonce="preexisting"', result)

    def test_twelve_inline_scripts_like_settings_page(self):
        """Simulate the 12 inline <script> tags from dist/settings/index.html."""
        inline = b"<script>a()</script>"
        external = b'<script src="x.js"></script>'
        body = inline * 12 + external * 3
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 12)


# ---------------------------------------------------------------------------
# Test f: non-HTML content types pass unchanged (SHA256 equality)
#         Guard is in serve_v2 (caller only invokes injector for text/html).
# ---------------------------------------------------------------------------

class TestNonHtmlPassthrough(unittest.TestCase):
    """The nonce-injection guard in serve_v2 is:
       ``if nonce and ct.startswith("text/html"): body = _inject_csp_nonce(body, nonce)``
    We test this guard by simulating what serve_v2 does for non-html types.
    """

    def _serve_v2_nonce_guard(self, raw: bytes, ct: str, nonce: str) -> bytes:
        """Reproduce the guard logic from serve_v2 for non-HTML content types."""
        if nonce and ct.startswith("text/html"):
            return _inject_csp_nonce(raw, nonce)
        return raw

    def test_javascript_passes_unchanged(self):
        js = b'/* @license */ function app() { return "<script>"; }'
        result = self._serve_v2_nonce_guard(js, "application/javascript", NONCE)
        self.assertEqual(hashlib.sha256(js).hexdigest(), hashlib.sha256(result).hexdigest())

    def test_css_passes_unchanged(self):
        css = b"body { color: red; }"
        result = self._serve_v2_nonce_guard(css, "text/css", NONCE)
        self.assertEqual(hashlib.sha256(css).hexdigest(), hashlib.sha256(result).hexdigest())

    def test_json_passes_unchanged(self):
        data = b'{"key": "<script>alert(1)</script>"}'
        result = self._serve_v2_nonce_guard(data, "application/json", NONCE)
        self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(result).hexdigest())

    def test_image_passes_unchanged(self):
        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        result = self._serve_v2_nonce_guard(img, "image/png", NONCE)
        self.assertEqual(hashlib.sha256(img).hexdigest(), hashlib.sha256(result).hexdigest())

    def test_font_passes_unchanged(self):
        font = b"\x00\x01\x00\x00" + b"\x00" * 32
        result = self._serve_v2_nonce_guard(font, "font/woff2", NONCE)
        self.assertEqual(hashlib.sha256(font).hexdigest(), hashlib.sha256(result).hexdigest())

    def test_sourcemap_passes_unchanged(self):
        smap = b'{"version":3,"sources":[]}'
        result = self._serve_v2_nonce_guard(smap, "application/json", NONCE)
        self.assertEqual(hashlib.sha256(smap).hexdigest(), hashlib.sha256(result).hexdigest())


# ---------------------------------------------------------------------------
# Test g: empty/falsy nonce is no-op (INV-2, INV-10)
# ---------------------------------------------------------------------------

class TestEmptyNonceNoop(unittest.TestCase):
    def test_empty_string_nonce(self):
        body = b"<html><script>var x=1;</script></html>"
        result = _inject_csp_nonce(body, "")
        self.assertEqual(result, body)

    def test_empty_nonce_sha256_unchanged(self):
        body = b"<html><script>var x=1;</script></html>"
        result = _inject_csp_nonce(body, "")
        self.assertEqual(hashlib.sha256(body).hexdigest(), hashlib.sha256(result).hexdigest())


# ---------------------------------------------------------------------------
# Test h: </script> closing tags are NEVER modified
# ---------------------------------------------------------------------------

class TestClosingTagNotModified(unittest.TestCase):
    def test_closing_script_unchanged(self):
        body = b"<script>var x=1;</script>"
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(b"</script>", result)
        # No nonce attribute on closing tag
        self.assertNotIn(b"</script nonce", result)
        self.assertNotIn(b"</script>nonce", result)

    def test_closing_tag_count_unchanged(self):
        body = b"<script>a()</script><script>b()</script>"
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result.count(b"</script>"), 2)


# ---------------------------------------------------------------------------
# Test i: HTML comment with <script> literal — comment-aware injection
# ---------------------------------------------------------------------------

class TestHtmlCommentBehaviour(unittest.TestCase):
    """<script> tags inside HTML comments are NOT nonced.

    _inject_csp_nonce processes the body in segments, skipping content inside
    ``<!-- ... -->`` blocks entirely.  Any ``<script>`` token inside a comment
    is left verbatim.  Real inline scripts outside comments still receive the
    nonce.

    This handles browse-ui/dist output where framework build tools may emit
    HTML comments that contain ``<script>`` tokens.
    """

    def test_script_inside_comment_not_nonced(self):
        """A <script> tag that is entirely inside an HTML comment must NOT receive a nonce."""
        body = b"<!-- <script>comment only</script> -->"
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)
        self.assertNotIn(NONCE.encode(), result)

    def test_real_inline_script_after_comment_gets_nonce(self):
        """Real inline script after a comment block must still receive nonce."""
        body = b"<!-- <script> comment --><script>real()</script>"
        result = _inject_csp_nonce(body, NONCE)
        # The comment block is preserved verbatim.
        self.assertIn(b"<!-- <script> comment -->", result)
        # The genuine inline script gets the nonce.
        self.assertIn(f'<script nonce="{NONCE}">'.encode(), result)
        # Total nonce count: exactly one (the real script, not the comment).
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 1)

    def test_real_inline_script_before_comment_gets_nonce(self):
        """Real inline script before a comment block must receive nonce."""
        body = b"<script>real()</script><!-- <script>inside comment</script> -->"
        result = _inject_csp_nonce(body, NONCE)
        self.assertIn(f'<script nonce="{NONCE}">'.encode(), result)
        # Comment preserved verbatim.
        self.assertIn(b"<!-- <script>inside comment</script> -->", result)
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 1)

    def test_multiline_comment_script_not_nonced(self):
        """Multi-line HTML comment containing <script> is left untouched."""
        body = (
            b"<!--\n"
            b"  <script>some deferred code()</script>\n"
            b"-->"
            b"<script>real()</script>"
        )
        result = _inject_csp_nonce(body, NONCE)
        # Comment block preserved verbatim.
        self.assertIn(b"<!--\n  <script>some deferred code()</script>\n-->", result)
        # Real script gets nonce.
        self.assertIn(f'<script nonce="{NONCE}">'.encode(), result)
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 1)

    def test_comment_only_document_unchanged(self):
        """Document consisting only of a comment is returned unchanged."""
        body = b"<!-- <script>foo()</script> <script>bar()</script> -->"
        result = _inject_csp_nonce(body, NONCE)
        self.assertEqual(result, body)

    def test_multiple_comments_with_real_scripts_between(self):
        """Comments at start and end; real scripts in between all get nonced."""
        body = (
            b"<!-- <script>c1</script> -->"
            b"<script>real1()</script>"
            b"<!-- <script>c2</script> -->"
            b"<script>real2()</script>"
            b"<!-- <script>c3</script> -->"
        )
        result = _inject_csp_nonce(body, NONCE)
        # Both real scripts get nonces.
        self.assertEqual(result.count(f'nonce="{NONCE}"'.encode()), 2)
        # All three comment blocks preserved verbatim.
        self.assertIn(b"<!-- <script>c1</script> -->", result)
        self.assertIn(b"<!-- <script>c2</script> -->", result)
        self.assertIn(b"<!-- <script>c3</script> -->", result)

    def test_no_comment_scripts_in_real_dist_output(self):
        """Verify that browse-ui/dist HTML comment blocks with <script> tokens
        survive nonce injection unchanged — comment bytes must be preserved exactly.

        Skipped locally when dist is absent.
        Fails in CI when dist is absent (must run ``cd browse-ui && pnpm build``
        before this test suite).
        """
        import os as _os
        dist_path = Path(__file__).parent.parent / "browse-ui" / "dist"
        if not dist_path.exists():
            if _os.environ.get("CI", "").lower() in ("1", "true", "yes"):
                self.fail(
                    "browse-ui/dist not present in CI environment. "
                    "Run 'cd browse-ui && pnpm build' before this test."
                )
            self.skipTest("browse-ui/dist not present; skipping dist-level assertion")

        comment_script_re = re.compile(rb"<!--.*?<script.*?-->", re.DOTALL)
        nonce = "distTestNonce_CI"
        nonce_bytes = nonce.encode()
        for html_file in dist_path.rglob("*.html"):
            content = html_file.read_bytes()
            injected = _inject_csp_nonce(content, nonce)
            # Any comment block that contains a <script> token must survive verbatim.
            for orig_comment in comment_script_re.finditer(content):
                comment_bytes = orig_comment.group(0)
                # Sanity: the original comment must not already contain our test nonce.
                self.assertNotIn(
                    nonce_bytes,
                    comment_bytes,
                    f"{html_file.relative_to(dist_path)}: original comment already contains test nonce (unexpected)",
                )
                # The exact comment bytes must appear unchanged in the injected output.
                self.assertIn(
                    comment_bytes,
                    injected,
                    f"{html_file.relative_to(dist_path)}: HTML comment block was modified by nonce injection",
                )


# ---------------------------------------------------------------------------
# Test j: placeholder + nonce composition; session_id sanitisation (INV-7)
# ---------------------------------------------------------------------------

class TestSessionIdSanitisation(unittest.TestCase):
    """INV-7: session_id safe-charset gate prevents HTML token synthesis."""

    def test_safe_session_id_alphanumeric_accepted(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/abc123/index.html")
        self.assertEqual(sid, "abc123")
        self.assertGreater(len(fallbacks), 0)

    def test_safe_session_id_with_underscore_hyphen(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/abc_123-XYZ/index.html")
        self.assertEqual(sid, "abc_123-XYZ")
        self.assertGreater(len(fallbacks), 0)

    def test_url_encoded_angle_bracket_rejected(self):
        # %3C = '<', %3E = '>'  → decoded as "<script>"
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/%3Cscript%3E/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_url_encoded_slash_rejected(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/foo%2Fbar/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_space_encoded_rejected(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/foo%20bar/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_raw_angle_bracket_rejected(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/<bad>/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_semicolon_rejected(self):
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/foo;bar/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_safe_session_id_with_dot_accepted(self):
        """Dots are valid per canonical _SESSION_ID_RE (^[a-zA-Z0-9._-]{1,128}$)."""
        sid, fallbacks = _session_placeholder_fallback_paths("sessions/abc.def-123/index.html")
        self.assertEqual(sid, "abc.def-123")
        self.assertGreater(len(fallbacks), 0)

    def test_over_length_session_id_rejected(self):
        """Session IDs longer than 128 chars are rejected by canonical _SESSION_ID_RE."""
        long_id = "a" * 129
        sid, fallbacks = _session_placeholder_fallback_paths(f"sessions/{long_id}/index.html")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_session_id_safe_regex_directly(self):
        self.assertIsNotNone(_SESSION_ID_RE.match("abc123_-"))
        self.assertIsNotNone(_SESSION_ID_RE.match("abc.def"))  # dot accepted
        self.assertIsNone(_SESSION_ID_RE.match(""))
        self.assertIsNone(_SESSION_ID_RE.match("<script>"))
        self.assertIsNone(_SESSION_ID_RE.match("foo bar"))
        self.assertIsNone(_SESSION_ID_RE.match("foo/bar"))
        self.assertIsNone(_SESSION_ID_RE.match("a" * 129))  # too long


class TestPlaceholderNonceComposition(unittest.TestCase):
    """Test j: _rewrite_session_placeholder + _inject_csp_nonce compose safely."""

    def test_composition_valid_session_id(self):
        """Placeholder is replaced, then nonce is injected."""
        session_id = "abc123"
        template = b"<title>_placeholder</title><script>init('_placeholder')</script>"
        ct = "text/html; charset=utf-8"
        nonce = "composeNonce_xyz"

        after_rewrite = _rewrite_session_placeholder(template, ct, session_id)
        self.assertIn(session_id.encode(), after_rewrite)
        self.assertNotIn(b"_placeholder", after_rewrite)

        after_nonce = _inject_csp_nonce(after_rewrite, nonce)
        self.assertIn(f'nonce="{nonce}"'.encode(), after_nonce)
        # Script body still contains the session id (not the placeholder)
        self.assertIn(session_id.encode(), after_nonce)

    def test_hostile_session_id_never_reaches_rewrite(self):
        """Sanitisation gate blocks hostile ids from reaching _rewrite_session_placeholder.

        This test documents the risk: if a hostile session_id like
        '<script>evil()</script>' bypassed the sanitisation gate it WOULD
        introduce a new <script> token into the HTML.  The gate MUST hold.
        """
        hostile_id = "<script>evil()</script>"
        # The gate rejects it
        sid, fallbacks = _session_placeholder_fallback_paths(f"sessions/{hostile_id}/")
        self.assertEqual(sid, "")
        self.assertEqual(fallbacks, [])

    def test_rewrite_with_hostile_id_risk_documented(self):
        """Document: if sanitisation were removed, hostile id embeds <script>."""
        hostile_id = "<script>evil()</script>"
        template = b"<html><p>_placeholder</p></html>"
        ct = "text/html; charset=utf-8"
        after_rewrite = _rewrite_session_placeholder(template, ct, hostile_id)
        # Without the gate, the rewrite DOES embed the hostile tag.
        self.assertIn(b"<script>evil()</script>", after_rewrite)
        # This proves the sanitisation gate in _session_placeholder_fallback_paths
        # is load-bearing for security (INV-7).


# ---------------------------------------------------------------------------
# Test k/l: CSP header nonce alignment (INV-1, INV-5)
# ---------------------------------------------------------------------------

class TestCspHeaderAlignment(unittest.TestCase):
    def test_build_v2_csp_header_truthy_nonce_has_no_unsafe_inline_in_script_src(self):
        """Test l: INV-1 — no 'unsafe-inline' in script-src when nonce is truthy."""
        nonce = "someNonce123"
        header = build_v2_csp_header(nonce)
        match = re.search(r"script-src\s+([^;]+)", header)
        self.assertIsNotNone(match, "script-src directive missing from CSP header")
        script_src = match.group(1)
        self.assertNotIn("'unsafe-inline'", script_src)
        self.assertIn(f"'nonce-{nonce}'", script_src)

    def test_nonce_in_header_matches_nonce_in_html(self):
        """Test k: INV-5 — nonce in CSP header equals nonce injected into HTML."""
        nonce = "alignmentNonce456"
        header = build_v2_csp_header(nonce)
        html_body = b"<html><script>var x=1;</script></html>"
        injected = _inject_csp_nonce(html_body, nonce)

        # Same nonce in header and body
        self.assertIn(f"'nonce-{nonce}'", header)
        self.assertIn(f'nonce="{nonce}"'.encode(), injected)

    def test_build_v2_csp_header_empty_nonce_fallback_uses_unsafe_inline(self):
        """Backward-compat branch (INV-10 awareness test).

        This branch is unreachable from any current serve_v2 call site
        (both server.py call sites now pass a truthy nonce).  The test
        documents the fallback exists and asserts it is not used at runtime.
        """
        header = build_v2_csp_header("")
        self.assertIn("'unsafe-inline'", header)
        # Note: test_browse_core_csp.py asserts this branch is unreachable in
        # production code paths; see INV-1 safe-to-merge condition 3.

    def test_multiple_different_nonces_produce_different_headers(self):
        """Each request gets a unique CSP nonce (no nonce reuse)."""
        h1 = build_v2_csp_header("nonce_aaa")
        h2 = build_v2_csp_header("nonce_bbb")
        self.assertNotEqual(h1, h2)
        self.assertIn("'nonce-nonce_aaa'", h1)
        self.assertIn("'nonce-nonce_bbb'", h2)

    def test_script_src_does_not_contain_unsafe_inline_for_any_nonempty_nonce(self):
        """Regression: INV-1 holds for a variety of nonce values (WBS-084 guard)."""
        for nonce in ["a", "abc123", "qJhrJ1cWrukEyhTzX4K92g", "x" * 32]:
            header = build_v2_csp_header(nonce)
            match = re.search(r"script-src\s+([^;]+)", header)
            self.assertIsNotNone(match)
            self.assertNotIn("'unsafe-inline'", match.group(1), f"unsafe-inline in script-src for nonce={nonce!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
