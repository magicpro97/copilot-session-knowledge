"""Auto bug detection rule — postToolUse for edit/create.

Detects five categories of bug-fix patterns in code change payloads and
records them via a ``learn.py --mistake`` subprocess call.

Categories
----------
  error-handling — try/except, .catch(), raise ErrorType added to new code
  null-safety    — None/null guard, optional-chaining, nullish-coalescing added
  guard-clause   — early-return guard added at function entry point
  async-fix      — ``await`` or ``async def/function`` added where absent
  type-fix       — Python type annotation added in an edit (excluded on create)

Create-path support
-------------------
  ``create`` payloads lack an ``old_str`` reference, so most categories are too
  noisy to enable.  The following categories are supported on ``create`` at a
  reduced confidence (0.62) because their patterns are specific enough to be
  credible as intentional safety additions even without prior-code context:

    - ``null-safety`` (0.62) — None/null guards and optional-chaining are
      specific enough that their presence in a brand-new file suggests defensive
      null handling was the intent.
    - ``async-fix`` (0.62) — async/await in a new file credibly indicates an
      async handler or wrapper created to address a missing-await bug.

  ``error-handling``, ``guard-clause``, and ``type-fix`` remain excluded on
  ``create`` because any file with try/except, guard patterns, or type
  annotations would trigger them spuriously.

5-minute occurrence semantics
------------------------------
Same file + same category within the same 5-minute time bucket share a
bucketed title string, e.g.::

    [auto-detect] null-safety: foo.py (bucket 5723456)

Because ``learn.py`` deduplicates by ``(category, title)`` and increments
``occurrence_count`` on repeat calls with the same title, every detection
within the window routes through the upsert path — counts accumulate rather
than being silently dropped.  When the bucket rolls over a new title is used,
starting a fresh detection window.

Fail-open
---------
Any exception inside ``evaluate`` is caught and ``None`` is returned so that
the hook runner dispatch chain is never interrupted.
"""

import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import Rule
from .common import CODE_EXTENSIONS, MARKERS_DIR, TOOLS_DIR, info, is_session_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 5-minute detection window (seconds).
BUCKET_SECONDS = 300

# learn.py lives in the tools directory (two levels up from hooks/rules/).
_LEARN_PY = TOOLS_DIR / "learn.py"

# Detections below this confidence threshold are silently discarded.
MIN_CONFIDENCE = 0.60

# YAML files stay in the general code-extension allowlist for other categories,
# but `type-fix` is too ambiguous there (`timeout: int` is usually config, not
# a Python annotation).
_TYPE_FIX_SKIPPED_EXTENSIONS = {".yaml", ".yml"}

_ERROR_INDICATOR_RE = re.compile(
    r"\btry\s*[:{]"
    r"|\bexcept\s+\w"
    r"|\.catch\s*\("
    r"|\braise\s+\w+Error\b"
    r"|\bthrow\s+new\s+\w*Error\b"
)

_TYPE_ANNOTATION_TOKENS = (
    "int",
    "str",
    "float",
    "bool",
    "bytes",
    "list",
    "dict",
    "set",
    "tuple",
    "Optional[",
    "Union[",
    "List[",
    "Dict[",
    "Tuple[",
    "Any",
)


# ---------------------------------------------------------------------------
# Bucket helper
# ---------------------------------------------------------------------------


def _bucket_id() -> int:
    """Current 5-minute bucket index (seconds since epoch // BUCKET_SECONDS)."""
    return int(time.time() / BUCKET_SECONDS)


# ---------------------------------------------------------------------------
# Pattern definitions and detection helpers
# ---------------------------------------------------------------------------
# Each spec dict has:
#   category        — one of the five issue-86 categories
#   new_pat         — compiled regex; must match new_str (or file_text for create)
#   old_antipat     — compiled regex; if it matches old_str the detection is skipped
#                     (the pattern was already present before the edit)
#   check_fn        — optional callable(text) -> bool; when present, overrides
#                     new_pat/old_antipat entirely (used for patterns that need
#                     per-line logic, e.g. to skip comment-only lines)
#   confidence      — confidence score for edit detections
#   create_conf     — confidence score for create detections (0.0 → excluded)


def _trimmed_code_part(line: str) -> str:
    """Return the executable portion of *line* with trailing comments removed."""
    trimmed = line.lstrip()
    if trimmed.startswith("#") or trimmed.startswith("//") or trimmed.startswith("*"):
        return ""
    code_part = trimmed
    hash_pos = code_part.find("#")
    if hash_pos >= 0:
        code_part = code_part[:hash_pos]
    dslash_pos = code_part.find("//")
    if dslash_pos >= 0:
        code_part = code_part[:dslash_pos]
    return code_part.rstrip()


def _has_error_indicator(text: str) -> bool:
    """Return True if *text* contains an error-handling pattern in real code."""
    for line in text.splitlines():
        code_part = _trimmed_code_part(line)
        if code_part and _ERROR_INDICATOR_RE.search(code_part):
            return True
    return False


def _has_null_safety_indicator(text: str) -> bool:
    """Return True if *text* contains a null-safety pattern in a real guard context.

    Rules:
    - ``.unwrap_or(``, ``.ok_or(``, ``?.``, and ``??`` are checked on the
      comment-stripped code portion of each line so comment-only occurrences do
      NOT fire.
    - ``is None``, ``is not None``, ``== null``, ``!= null``, ``=== null``,
      ``!== null`` all require a leading ``if`` on the trimmed line — mirrors
      Rust's ``auto_bug_has_null_safety_indicator`` structured-form requirement.
    """
    # Per-line scan for null-safety indicators and structured `if` null-comparisons.
    null_cmp = ("== null", "=== null", "!= null", "!== null")
    for line in text.splitlines():
        code_part = _trimmed_code_part(line)
        if not code_part:
            continue
        # Simple null-safety indicators on code portion only.
        if ".unwrap_or(" in code_part or ".ok_or(" in code_part or "?." in code_part or "??" in code_part:
            return True
        # Structured `if` forms also need comment stripping so trailing comments
        # like `if ready:  # check if x is None` do not spuriously count.
        if code_part.startswith("if ") or code_part.startswith("if("):
            if "is None" in code_part or "is not None" in code_part:
                return True
            for cmp in null_cmp:
                if cmp in code_part:
                    return True
    return False


def _has_type_annotation(text: str) -> bool:
    """Return True if *text* contains a type annotation in real code context."""
    for line in text.splitlines():
        code_part = _trimmed_code_part(line)
        if not code_part:
            continue
        search_start = 0
        while True:
            colon_pos = code_part.find(":", search_start)
            if colon_pos < 0:
                break
            before = code_part[:colon_pos].rstrip()
            if not before.endswith(("'", '"')):
                after_colon = code_part[colon_pos + 1 :].lstrip()
                for token in _TYPE_ANNOTATION_TOKENS:
                    if after_colon.startswith(token):
                        after_token = after_colon[len(token) :]
                        if not after_token or (not after_token[0].isalnum() and after_token[0] != "_"):
                            return True
            search_start = colon_pos + 1
    return False


_CATEGORY_PATTERNS = [
    {
        "category": "error-handling",
        "check_fn": _has_error_indicator,
        "confidence": 0.85,
        "create_conf": 0.0,  # excluded: any file with try/except would trigger
    },
    {
        "category": "null-safety",
        # Uses check_fn for per-line logic so comment-only `??` / `?.` lines
        # do NOT spuriously match (the simple regex cannot skip comment lines).
        "check_fn": _has_null_safety_indicator,
        "confidence": 0.78,
        "create_conf": 0.62,  # enabled: null-safety patterns specific enough on create
    },
    {
        "category": "guard-clause",
        "new_pat": re.compile(
            r"if\s+[^\n:]+:\s*\n\s+return\b"
            r"|if\s+not\s+\w[\w.]*\s*[:\n]",
            re.MULTILINE,
        ),
        "old_antipat": re.compile(
            r"if\s+[^\n:]+:\s*\n\s+return\b"
            r"|if\s+not\s+\w[\w.]*\s*[:\n]",
            re.MULTILINE,
        ),
        "confidence": 0.73,
        "create_conf": 0.0,  # excluded
    },
    {
        "category": "async-fix",
        "new_pat": re.compile(
            r"\bawait\s+\w"
            r"|\basync\s+def\s+\w"
            r"|\basync\s+function\s+\w"
        ),
        "old_antipat": re.compile(
            r"\bawait\s+\w"
            r"|\basync\s+def\s+\w"
            r"|\basync\s+function\s+\w"
        ),
        "confidence": 0.78,
        "create_conf": 0.62,  # enabled: async wrapper/handler credible as bug fix on create
    },
    {
        "category": "type-fix",
        # Uses per-line parsing so we can:
        # - skip comment-only / inline-comment occurrences,
        # - treat compact annotations like `name:str` as valid,
        # - ignore string-keyed dict literals even when whitespace appears before `:`.
        "check_fn": _has_type_annotation,
        "confidence": 0.65,
        "create_conf": 0.0,  # excluded per design: proactive typing vs. bug fix
    },
]


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_categories_edit(old_str: str, new_str: str) -> list:
    """Return list of (category, confidence) tuples for an edit diff.

    A category fires when the new code introduces a pattern that was absent in
    the old code, indicating the change added the safety/fix behaviour.

    Specs that supply a ``check_fn`` field use that callable instead of the
    compiled ``new_pat`` / ``old_antipat`` regexes.  This supports categories
    (like ``null-safety``) that need per-line logic to skip comment-only lines.
    """
    detections = []
    for spec in _CATEGORY_PATTERNS:
        if spec["confidence"] < MIN_CONFIDENCE:
            continue
        check_fn = spec.get("check_fn")
        if check_fn is not None:
            new_has = check_fn(new_str)
            old_has = check_fn(old_str)
        else:
            new_has = bool(spec["new_pat"].search(new_str))
            old_has = bool(spec["old_antipat"].search(old_str))
        if new_has and not old_has:
            detections.append((spec["category"], spec["confidence"]))
    return detections


def _detect_categories_create(file_text: str) -> list:
    """Return list of (category, confidence) tuples for a create payload.

    More conservative than edit because there is no ``old_str`` reference.
    Only ``null-safety`` and ``async-fix`` are enabled for create (at 0.62);
    all other categories remain at 0.0 to avoid spurious detections.

    Specs that supply a ``check_fn`` field use that callable instead of
    ``new_pat``.
    """
    detections = []
    for spec in _CATEGORY_PATTERNS:
        create_conf = spec.get("create_conf", 0.0)
        if create_conf < MIN_CONFIDENCE:
            continue
        check_fn = spec.get("check_fn")
        if check_fn is not None:
            has_pattern = check_fn(file_text)
        else:
            has_pattern = bool(spec["new_pat"].search(file_text))
        if has_pattern:
            detections.append((spec["category"], create_conf))
    return detections


def _filter_detections_for_path(path: str, detections: list) -> list:
    """Apply path-specific suppression for categories that are too ambiguous."""
    if Path(path).suffix.lower() in _TYPE_FIX_SKIPPED_EXTENSIONS:
        return [(category, confidence) for category, confidence in detections if category != "type-fix"]
    return detections


# ---------------------------------------------------------------------------
# learn.py caller + learn-done marker
# ---------------------------------------------------------------------------


def _call_learn(file_path: str, category: str, confidence: float, bucket: int) -> bool:
    """Invoke ``learn.py --mistake`` to record the detection.

    Uses a 5-minute bucketed title so that repeated calls for the same
    file+category within the same window share the title and cause learn.py
    to increment ``occurrence_count`` rather than creating a new entry.

    Returns True on success, False on any failure (fail-open).
    """
    if not _LEARN_PY.is_file():
        return False

    title = f"[auto-detect] {category}: {Path(file_path).name} (bucket {bucket})"
    description = (
        f"Auto-detected {category} pattern in {file_path}. "
        f"Confidence: {confidence:.2f}. "
        f"5-minute detection bucket: {bucket}."
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_LEARN_PY),
                "--mistake",
                title,
                description,
                "--confidence",
                str(round(confidence, 2)),
                "--tags",
                f"auto-detect,{category}",
                "--wing",
                "shared",
                "--room",
                "hook-rules",
                "--skip-gate",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_learn_done_marker() -> None:
    """Write the ``learn-done`` marker so the enforce-learn gate knows learn ran.

    The learn-gate (``EnforceLearnRule``) checks for this marker before
    blocking ``git commit`` / ``task_complete``.  Auto-detected bugs should
    count as a learn event.

    Uses the same fallback pattern as ``learn_reminder.py``: import
    ``marker_auth.sign_marker`` with a plain-touch fallback.
    """
    try:
        from marker_auth import sign_marker  # type: ignore[import]
    except ImportError:

        def sign_marker(p, n):  # type: ignore[misc]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()

    try:
        learn_done = MARKERS_DIR / "learn-done"
        sign_marker(learn_done, "learn-done")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rule class
# ---------------------------------------------------------------------------


class AutoBugDetectorRule(Rule):
    """Detect bug-fix patterns in edit/create diffs; log via learn.py.

    Fires on ``postToolUse`` for ``edit`` and ``create`` tools.  Applies
    regex pattern matching across five categories (error-handling,
    null-safety, guard-clause, async-fix, type-fix).

    Each detection calls ``learn.py --mistake`` with a 5-minute bucketed
    title so that repeated hits within the same window increment
    ``occurrence_count`` rather than being silently dropped.

    Informational-only.  Fail-open on every exception.
    """

    name = "auto-bug-detector"
    events = ["postToolUse"]
    tools = ["edit", "create"]

    def evaluate(self, event, data):
        try:
            return self._evaluate_inner(data)
        except Exception:
            return None

    def _evaluate_inner(self, data):
        tool_name = data.get("toolName", "")
        tool_args = data.get("toolArgs", {})
        if not isinstance(tool_args, dict):
            return None

        if tool_name == "edit":
            return self._handle_edit(tool_args)
        if tool_name == "create":
            return self._handle_create(tool_args)
        return None

    def _handle_edit(self, tool_args):
        path = tool_args.get("path", "") or ""
        old_str = tool_args.get("old_str", "") or ""
        new_str = tool_args.get("new_str", "") or ""
        if not path or not new_str or is_session_path(path) or Path(path).suffix.lower() not in CODE_EXTENSIONS:
            return None
        detections = _filter_detections_for_path(path, _detect_categories_edit(old_str, new_str))
        if not detections:
            return None
        return self._record(path, detections)

    def _handle_create(self, tool_args):
        path = tool_args.get("path", "") or ""
        file_text = tool_args.get("file_text", "") or ""
        if not path or not file_text or is_session_path(path) or Path(path).suffix.lower() not in CODE_EXTENSIONS:
            return None
        detections = _filter_detections_for_path(path, _detect_categories_create(file_text))
        if not detections:
            return None
        return self._record(path, detections)

    def _record(self, file_path, detections):
        bucket = _bucket_id()

        def _run_one(cat_conf):
            category, confidence = cat_conf
            return _call_learn(file_path, category, confidence, bucket)

        # Launch all learn subprocess calls concurrently so multiple detected
        # categories do not stack latency linearly (worst-case stays O(1) per
        # evaluation rather than O(n * timeout)).
        with ThreadPoolExecutor(max_workers=len(detections) or 1) as executor:
            futures = [executor.submit(_run_one, d) for d in detections]
        # All futures complete when the executor exits the `with` block.
        results = [f.result() for f in futures]

        messages = []
        for (category, confidence), ok in zip(detections, results, strict=True):
            if ok:
                messages.append(
                    f"  \U0001f41b Auto-detected {category} in {Path(file_path).name} (confidence: {confidence:.0%})"
                )
        if messages:
            _write_learn_done_marker()  # Write once if any detection succeeded
            header = "  \U0001f50d Auto bug detector:"
            return info("\n" + header + "\n" + "\n".join(messages) + "\n")
        return None
