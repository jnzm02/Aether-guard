#!/bin/bash
cd "/Users/nizamijussupov/Desktop/AI/Aether Guard/services/agent"
export PYTHONPATH="/Users/nizamijussupov/Desktop/AI/Aether Guard/services/agent:$PYTHONPATH"
python3 -m pytest -x \
  --ignore=tests/test_benchmark_real_tempo.py \
  --ignore=mutants \
  tests/test_incident_report.py
