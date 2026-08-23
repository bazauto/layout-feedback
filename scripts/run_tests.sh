#!/usr/bin/env bash
# Run tests with project root on PYTHONPATH
PYTHONPATH=. pytest "$@"
