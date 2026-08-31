#!/bin/bash
# Runs the full test suite. Run from repo root.
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -v
