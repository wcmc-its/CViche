"""Tests for unified_pipeline.log_redaction (#639): the label a WARNING-or-
above log line uses for an exception in place of its raw text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.log_redaction import redacted_error_label  # noqa: E402

_CV_TEXT_SENTINEL = "Jane Q. Example, 12 Synthetic Lane"


def test_label_is_the_class_name_and_never_the_exception_text() -> None:
    assert redacted_error_label(ValueError(_CV_TEXT_SENTINEL)) == "ValueError"


def test_label_brackets_the_caller_supplied_safe_detail() -> None:
    label = redacted_error_label(KeyError(_CV_TEXT_SENTINEL), "line 3, column 5")
    assert label == "KeyError[line 3, column 5]"


# --- the codebase-wide gate (#639) ------------------------------------------
#
# A line grep missed 25 multi-line call sites, so this walks the AST. Inside an
# `except ... as NAME` handler it flags a `logger.<level>(...)` call above
# DEBUG whose arguments reach NAME other than through `type(NAME)` or
# `redacted_error_label(NAME)`, and a `logger.exception(...)` (its traceback
# ends in str(NAME)). Out of scope here, and named in #639's residual: a bare
# `except:` handler's `logger.exception` and `exc_info=True` above DEBUG.

import ast  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_GATED_ROOTS = ("src/unified_pipeline", "web_interface/backend/app")
_LOG_LEVELS_ABOVE_DEBUG = frozenset({"info", "warning", "warn", "error", "critical", "exception"})
_LABEL_CALLS = frozenset({"type", "redacted_error_label", "_redacted_error_label"})

#: (repo path, enclosing function) -> why the raw-looking reference is safe.
_ALLOWED = {
    ("web_interface/backend/app/services/inbound_autorun.py", "_run_item"):
        "logs HTTPException.detail['error'], a fixed error code, or the status code",
}


def _reaches_raw(expr: ast.AST, name: str) -> bool:
    if isinstance(expr, ast.Call):
        fn = expr.func
        callee = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if callee in _LABEL_CALLS:
            return False
    if isinstance(expr, ast.Name):
        return expr.id == name
    return any(_reaches_raw(child, name) for child in ast.iter_child_nodes(expr))


def _is_logger_call(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    receiver = node.func.value
    receiver_name = receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", "")
    return node.func.attr in _LOG_LEVELS_ABOVE_DEBUG and "log" in receiver_name.lower()


def _enclosing_function(tree: ast.Module) -> dict[ast.ExceptHandler, str]:
    """Each named except handler -> the name of its innermost enclosing function."""
    owner: dict[ast.ExceptHandler, str] = {}

    def visit(node: ast.AST, func_name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.ExceptHandler) and child.name:
                owner[child] = func_name
            visit(child, func_name)

    visit(tree, "")
    return owner


def _raw_exception_log_sites(source: str, rel: str) -> list[str]:
    sites = []
    for handler, func_name in _enclosing_function(ast.parse(source)).items():
        if (rel, func_name) in _ALLOWED:
            continue
        for node in (n for stmt in handler.body for n in ast.walk(stmt)):
            if not _is_logger_call(node):
                continue
            if node.func.attr == "exception" or any(
                    _reaches_raw(arg, handler.name)
                    for arg in [*node.args, *(k.value for k in node.keywords if k.arg != "exc_info")]):
                sites.append(f"{rel}:{node.lineno}")
    return sites


def test_no_log_line_above_debug_carries_raw_exception_text() -> None:
    sites = sorted({
        site
        for root in _GATED_ROOTS
        for path in (_REPO / root).rglob("*.py")
        if "tests" not in path.relative_to(_REPO).parts
        for site in _raw_exception_log_sites(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix())
    })
    assert sites == [], (
        "log the class at this level (redacted_error_label(e), or type(e).__name__ "
        "in a backend module that doesn't import unified_pipeline) and str(e) at "
        "DEBUG on its own line (CODING_STANDARDS §4.7): " + ", ".join(sites))


def test_the_gate_sees_a_multi_line_call_and_passes_a_label() -> None:
    source = (
        "def f():\n    try:\n        pass\n    except OSError as e:\n"
        "        logger.warning(\n            'x %s',\n            e,\n        )\n"
        "        logger.warning('x %s', redacted_error_label(e))\n"
        "        logger.debug('x %s', e)\n")
    assert _raw_exception_log_sites(source, "probe.py") == ["probe.py:5"]


# The other half of the policy: a handler that logs only the class above DEBUG
# keeps the raw text at DEBUG (a logger.debug naming the bound exception, or
# one with exc_info), unless the line above DEBUG carries exc_info itself.

#: (repo path, enclosing function) -> why the class alone is enough there.
_CLASS_ONLY_ALLOWED = {
    ("src/unified_pipeline/stage_5b_institution_enrichment.py", "run_stage5b"):
        "§3.2a ratchet: one more line raises the oversized-function debt (#1888)",
    ("src/unified_pipeline/repair/protected_data.py", "repair_and_report"):
        "logs the class by design; predates #639",
    ("web_interface/backend/app/services/review_loop_service.py", "record_corrected_docx"):
        "logs the class by design; predates #639",
    ("web_interface/backend/app/services/batch_service.py", "_live_workers"):
        "the type tells an outage from a bug; predates #639",
    ("web_interface/backend/app/services/mailer.py", "send"):
        "logs the class by design; predates #639",
}


def _labels(expr: ast.AST, name: str) -> bool:
    for node in ast.walk(expr):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name) \
                and node.args[0].id == name:
            fn = node.func
            if (fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")) in _LABEL_CALLS:
                return True
    return False


def _class_only_sites(source: str, rel: str) -> list[str]:
    sites = []
    for handler, func_name in _enclosing_function(ast.parse(source)).items():
        if (rel, func_name) in _CLASS_ONLY_ALLOWED:
            continue
        nodes = [n for stmt in handler.body for n in ast.walk(stmt)]
        labelled = [n for n in nodes if _is_logger_call(n)
                    and any(_labels(arg, handler.name) for arg in n.args)]
        if not labelled or any(k.arg == "exc_info" for n in labelled for k in n.keywords):
            continue
        has_detail = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "debug"
            and (any(k.arg == "exc_info" for k in n.keywords)
                 or any(isinstance(x, ast.Name) and x.id == handler.name
                        for arg in n.args for x in ast.walk(arg)))
            for n in nodes)
        if not has_detail:
            sites.append(f"{rel}:{labelled[0].lineno}")
    return sites


def test_a_class_only_log_line_keeps_the_raw_text_at_debug() -> None:
    sites = sorted({
        site
        for root in _GATED_ROOTS
        for path in (_REPO / root).rglob("*.py")
        if "tests" not in path.relative_to(_REPO).parts
        for site in _class_only_sites(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix())
    })
    assert sites == [], (
        "this handler logs only the exception class; add logger.debug(..., e) on its own "
        "line so the detail survives at DEBUG (CODING_STANDARDS §4.7): " + ", ".join(sites))


def test_the_debug_half_flags_a_missing_detail_line() -> None:
    source = (
        "def f():\n    try:\n        pass\n    except OSError as e:\n"
        "        logger.warning('x %s', redacted_error_label(e))\n"
        "def g():\n    try:\n        pass\n    except OSError as e:\n"
        "        logger.warning('x %s', type(e).__name__)\n        logger.debug('x %s', e)\n")
    assert _class_only_sites(source, "probe.py") == ["probe.py:5"]
