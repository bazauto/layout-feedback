#!/usr/bin/env bash
# Run the host test suite. Needs no hardware — tests/conftest.py stubs `machine`
# and `utime`.
#
# Paths come from pytest.ini now, so the PYTHONPATH=. prefix this script used to
# carry is gone. Arguments are passed straight through:
#
#   bash scripts/run_tests.sh                    # everything
#   bash scripts/run_tests.sh -k mqtt            # one area
#   bash scripts/run_tests.sh tests/test_fakes.py -v
python -m pytest "$@"
