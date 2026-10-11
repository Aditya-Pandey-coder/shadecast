#!/usr/bin/env python3
"""Run the GFS ingest from a laptop and write into the same DynamoDB table.

The escape hatch for the container build. Identical logic, identical rows.
    python3 scripts/ingest_local.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aws.lambda_ingest import handler
print(handler({}, None))
