"""Static guard: test files must not leak widgets via ``deleteLater()``.

``widget.deleteLater()`` followed by ``QApplication.processEvents()`` does not
destroy anything: ``processEvents()`` re-enters the event loop without unwinding
to depth zero, so ``DeferredDelete`` is never delivered.  Every "closed" window
then stays alive, and because ``MainWindow.__init__`` calls
``app.setStyleSheet()`` -- which re-polishes *all* live widgets -- the suite gets
quadratically slower (measured before this guard: 58 minutes; after: ~37 s).

The rule is therefore enforced statically here, per file, rather than trusted:

* a file that calls ``deleteLater()`` must also use ``_qt_cleanup.destroy_widget``;
* a file that never calls ``deleteLater()`` is fine (nothing to flush);
* ``_qt_cleanup.py`` itself is exempt -- it *is* the flushing implementation.

Self-tests build throwaway files under ``tempfile`` so the detector is proven to
catch the violation and to stay quiet on the compliant form.
"""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TESTS_DIR.parent

# This module and the helper it mandates are not subject to the rule.
_EXEMPT_NAMES = {"_qt_cleanup.py", "test_qt_cleanup_guard.py"}
_EXEMPT_DIRS = {"__pycache__", ".pytest_cache"}


def _iter_test_sources(root: Path) -> list[Path]:
    """Yield ``tests/**/*.py`` excluding the helper, this guard and caches."""
    sources: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXEMPT_DIRS]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            if filename in _EXEMPT_NAMES:
                continue
            sources.append(Path(dirpath) / filename)
    return sorted(sources)


def _called_name(node: ast.AST) -> str | None:
    """Return the bare method name for ``x.foo(...)`` / ``foo(...)`` calls."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def find_unarmed_guards(source: str) -> list[tuple[int, str]]:
    """Return ``(line, reason)`` for classes that override ``setUp`` un-chained.

    A class whose base arms the leak guard in ``setUp`` silently disables the
    guard for itself when it overrides ``setUp`` and forgets ``super().setUp()``.
    That is exactly how CI (the unittest path, which has no autouse fixture)
    once ran the GUI suites with no L3 protection at all, so the pattern is
    checked mechanically instead of being left to review.
    """
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        bases = [ast.unparse(b) for b in cls.bases]
        if not bases or bases == ["unittest.TestCase"]:
            continue
        setup = next(
            (
                m
                for m in cls.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "setUp"
            ),
            None,
        )
        if setup is None:
            continue
        source_text = ast.get_source_segment(source, setup) or ""
        if "super().setUp()" not in source_text:
            violations.append(
                (
                    setup.lineno,
                    f"{cls.name}.setUp overrides an armed base class without "
                    f"calling super().setUp(); the leak guard stays unarmed",
                )
            )
    return violations


def find_cleanup_violations(source: str) -> list[tuple[int, str]]:
    """Return ``(line, reason)`` for every cleanup violation in *source*.

    Detection is AST based (never a text match), so ``deleteLater`` inside a
    docstring, a comment, or a string literal is correctly ignored.

    Rule: **any** ``deleteLater()`` call outside ``_qt_cleanup.py`` is a
    violation.  ``deleteLater`` always needs the deferred-delete flush, and the
    only place that flush is implemented is ``destroy_widget``, so every call
    site should go through it.

    Rejecting the call outright (rather than "requires destroy_widget somewhere
    in the file") closes the blind spots a file-level allowlist leaves open:
    ``deleteLater`` outside a function, a ``processEvents`` more than a few lines
    later (e.g. across a ``try/finally``), ``processEvents`` *before* the
    ``deleteLater``, and a lone unflushed ``deleteLater`` in a function that also
    happens to use ``destroy_widget`` elsewhere.  The repository currently has
    zero such calls, so this costs no false positives.
    """
    tree = ast.parse(source)

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if _called_name(node) != "deleteLater":
            continue
        violations.append(
            (
                node.lineno,
                "calls deleteLater() directly; use tests/_qt_cleanup.destroy_widget() "
                "so the deferred deletion is actually flushed",
            )
        )

    # De-duplicate while keeping deterministic order.
    return sorted(set(violations))


class QtCleanupStaticGuardTests(unittest.TestCase):
    """Enforce and self-test the ``destroy_widget`` cleanup rule."""

    def test_repository_tests_use_destroy_widget(self):
        """Every test file that posts ``deleteLater()`` must flush it properly."""
        offenders: list[str] = []
        for path in _iter_test_sources(_TESTS_DIR):
            source = path.read_text(encoding="utf-8")
            for line, reason in find_cleanup_violations(source):
                rel = path.relative_to(_PROJECT_ROOT)
                offenders.append(f"{rel}:{line}: {reason}")

        self.assertEqual(
            offenders,
            [],
            "GUI teardown must use tests/_qt_cleanup.destroy_widget "
            "(it flushes DeferredDelete so Qt objects are really destroyed); "
            "violations:\n  " + "\n  ".join(offenders),
        )

    def test_repository_classes_arm_the_leak_guard(self):
        """Every ``setUp`` overriding an armed base must call ``super().setUp()``."""
        offenders: list[str] = []
        for path in _iter_test_sources(_TESTS_DIR):
            source = path.read_text(encoding="utf-8")
            for line, reason in find_unarmed_guards(source):
                rel = path.relative_to(_PROJECT_ROOT)
                offenders.append(f"{rel}:{line}: {reason}")

        self.assertEqual(
            offenders,
            [],
            "A subclass that overrides setUp without calling super().setUp() "
            "silently disables the L3 widget-leak guard for its whole class; "
            "violations:\n  " + "\n  ".join(offenders),
        )

    # -- self-tests: the detector must be proven, not assumed -----------------

    def test_detects_the_legacy_teardown_idiom(self):
        source = (
            "def tearDown(self):\n"
            "    self.w.close()\n"
            "    self.w.deleteLater()\n"
            "    _APP.processEvents()\n"
        )
        violations = find_cleanup_violations(source)
        self.assertEqual(len(violations), 1, violations)
        self.assertEqual(violations[0][0], 3)
        self.assertIn("destroy_widget", violations[0][1])

    def test_detects_bare_delete_later(self):
        source = "def test_x():\n    widget.deleteLater()\n"
        violations = find_cleanup_violations(source)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], 2)

    def test_detects_delete_later_even_when_destroy_widget_is_used(self):
        """The allowlist hole: one good call must not hide a bad one."""
        source = (
            "from _qt_cleanup import destroy_widget\n"
            "def test_a():\n"
            "    destroy_widget(self.a)\n"
            "def test_b():\n"
            "    self.b.deleteLater()\n"
        )
        violations = find_cleanup_violations(source)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], 5, "must point at the offending call")

    def test_detects_delete_later_outside_any_function(self):
        source = "from _qt_cleanup import destroy_widget\nw.deleteLater()\n"
        violations = find_cleanup_violations(source)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], 2)

    def test_detects_process_events_before_delete_later(self):
        source = (
            "def tearDown(self):\n"
            "    _APP.processEvents()\n"
            "    self.w.deleteLater()\n"
        )
        violations = find_cleanup_violations(source)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], 3)

    def test_accepts_the_compliant_form(self):
        source = (
            "from _qt_cleanup import destroy_widget\n"
            "\n"
            "def tearDown(self):\n"
            "    destroy_widget(self.w)\n"
        )
        self.assertEqual(find_cleanup_violations(source), [])

    def test_ignores_delete_later_inside_strings_and_comments(self):
        source = (
            "from _qt_cleanup import destroy_widget\n"
            "\n"
            "def test_docs():\n"
            "    # widget.deleteLater() in a comment is not a call\n"
            '    note = "w.deleteLater(); app.processEvents()"\n'
            "    destroy_widget(None)\n"
        )
        self.assertEqual(find_cleanup_violations(source), [])

    def test_end_to_end_scan_of_a_temporary_file(self):
        """The scanner must report the real path and line of an on-disk file."""
        with tempfile.TemporaryDirectory(prefix="rv-guard-") as tmp:
            bad = Path(tmp) / "test_bad_teardown.py"
            bad.write_text(
                "def tearDown(self):\n"
                "    self.w.close()\n"
                "    self.w.deleteLater()\n"
                "    _APP.processEvents()\n",
                encoding="utf-8",
            )
            good = Path(tmp) / "test_good_teardown.py"
            good.write_text(
                "from _qt_cleanup import destroy_widget\n"
                "def tearDown(self):\n"
                "    destroy_widget(self.w)\n",
                encoding="utf-8",
            )

            bad_violations = find_cleanup_violations(bad.read_text(encoding="utf-8"))
            self.assertTrue(bad_violations, "the violation must be detected")
            self.assertEqual(bad_violations[0][0], 3, "must point at the deleteLater line")
            self.assertEqual(
                find_cleanup_violations(good.read_text(encoding="utf-8")), []
            )


class _ArmedBase(unittest.TestCase):
    """Stand-in for a shared base that arms the guard (mirrors the real one)."""

    def setUp(self):
        from _qt_cleanup import begin_widget_leak_check

        begin_widget_leak_check()


class _ForgetsSuper(_ArmedBase):
    """Deliberately broken: overrides setUp without chaining (must be flagged)."""

    def setUp(self):
        self.value = 1


class _ChainsSuper(_ArmedBase):
    """Correct form: chains to the base so the guard is armed."""

    def setUp(self):
        super().setUp()
        self.value = 1


class UnarmedGuardFailLoudTests(unittest.TestCase):
    """A guard that was never armed must fail, never pass silently.

    This is the root-cause defence for the P1 review finding: two classes
    overrode ``setUp`` without ``super().setUp()``, so their ``end`` call saw no
    baseline.  ``assert_no_widget_leak`` used to return quietly in that case,
    which meant CI's unittest path ran with no L3 protection at all.
    """

    def setUp(self):
        from _qt_cleanup import begin_widget_leak_check, end_widget_leak_check

        # Clear any baseline left over from an earlier test, then leave the
        # guard disarmed so each test starts from a known "never armed" state.
        begin_widget_leak_check()
        end_widget_leak_check("reset")

    def test_end_without_arm_raises(self):
        from _qt_cleanup import end_widget_leak_check

        with self.assertRaises(AssertionError) as caught:
            end_widget_leak_check("SomeTests.test_forgot_to_arm")
        message = str(caught.exception)
        self.assertIn("never armed", message)
        self.assertIn("super().setUp()", message)
        self.assertIn("SomeTests.test_forgot_to_arm", message)

    def test_assert_without_baseline_raises(self):
        from _qt_cleanup import assert_no_widget_leak

        with self.assertRaises(AssertionError) as caught:
            assert_no_widget_leak("SomeTests.test_no_baseline")
        self.assertIn("never armed", str(caught.exception))

    def test_static_guard_flags_missing_super_call(self):
        from test_qt_cleanup_guard import find_unarmed_guards

        offending = find_unarmed_guards(
            "class Base(unittest.TestCase):\n"
            "    def setUp(self):\n"
            "        begin_widget_leak_check()\n"
            "class Child(Base):\n"
            "    def setUp(self):\n"
            "        self.value = 1\n"
        )
        self.assertEqual(len(offending), 1, offending)
        self.assertEqual(offending[0][0], 5)
        self.assertIn("_ForgetsSuper_inline".replace("_ForgetsSuper_inline", "Child"), offending[0][1])
        self.assertIn("super().setUp()", offending[0][1])

    def test_static_guard_accepts_chained_super_call(self):
        from test_qt_cleanup_guard import find_unarmed_guards

        clean = find_unarmed_guards(
            "class Base(unittest.TestCase):\n"
            "    def setUp(self):\n"
            "        begin_widget_leak_check()\n"
            "class Child(Base):\n"
            "    def setUp(self):\n"
            "        super().setUp()\n"
            "        self.value = 1\n"
        )
        self.assertEqual(clean, [])

    def test_arms_in_real_mixin_subclasses(self):
        """The two classes the review flagged must really arm the guard."""
        import test_ui_interactions as ui

        for cls in (ui.PresetKeyboardNavigationTests, ui.ApplyShortcutTests):
            method = cls.setUp
            source = __import__("inspect").getsource(method)
            self.assertIn(
                "super().setUp()",
                source,
                f"{cls.__name__}.setUp must chain to super() so L3 is armed",
            )


if __name__ == "__main__":
    unittest.main()
