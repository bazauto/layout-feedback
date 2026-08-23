#!/usr/bin/env bash
# Stop hook: before Claude reports finished, prove the host test suite still passes.
# Exit 2 (with output on stderr) blocks the stop and feeds the failure back, so a broken
# suite can never be reported as done.
#
# A Stop hook rather than PostToolUse: one run per turn instead of one per file edit, and
# no spurious wake-ups partway through a multi-file change that is not meant to pass yet.
#
# Everything explanatory goes to STDERR. A Stop hook's stdout is not surfaced, so a failure
# reported on stdout blocks the turn with no visible reason — an unbreakable loop showing
# only "No stderr output".
#
# This hook must never exit 0 for a reason other than "the tests really passed" or "there
# is nothing to test, and I said so". A gate whose silence is indistinguishable from
# success is worse than no gate at all.

set -u
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# --- Nothing to verify ------------------------------------------------------
if [ ! -d tests ]; then
  echo "verify-tests: no tests/ directory, so nothing was verified." >&2
  exit 0
fi

# --- pytest must actually be installed --------------------------------------
#
# Loud, not quiet. A missing pytest is an environment problem, and letting it pass silently
# would mean every future turn reports success without running a single test.
PY="${LAYOUT_FEEDBACK_PYTHON:-python}"
if ! "$PY" -m pytest --version >/dev/null 2>&1; then
  {
    echo "verify-tests: pytest is not installed for '$PY', so NOTHING was verified."
    echo "              Install it:  $PY -m pip install --user pytest"
    echo "              Do not report this work as tested."
  } >&2
  exit 2
fi

# The host suite imports device modules with machine/utime stubbed by tests/conftest.py,
# so the project root and tests/ both need to be importable.
if ! out=$(PYTHONPATH=. "$PY" -m pytest -q 2>&1); then
  {
    echo "Host tests are failing — do not report this work as complete."
    echo "--- $PY -m pytest -q ---"
    echo "$out" | tail -60
  } >&2
  exit 2
fi

exit 0
