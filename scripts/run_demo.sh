#!/bin/bash
# Starts all 3 Pro tier products. Run from repo root.
set -e
cd "$(dirname "$0")/.."
echo "Starting PPE Detection (8103)..."
uvicorn applications.ppe_detection.main:app --host 0.0.0.0 --port 8103 &
echo "Starting Driver Monitoring (8104)..."
uvicorn applications.driver_monitoring.main:app --host 0.0.0.0 --port 8104 &
echo "Starting Healthcare Monitoring (8105)..."
uvicorn applications.healthcare.main:app --host 0.0.0.0 --port 8105 &
wait
