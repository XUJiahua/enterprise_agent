#!/usr/bin/env python3
"""Record CI credential presence without inspecting or serializing credentials."""
import argparse
import json
import os
from pathlib import Path


def record(ci_value=None):
    if ci_value not in (None, 'true', 'false'):
        raise ValueError('CI presence must be true or false')
    return {
        'source': 'local-unchecked' if ci_value is None else 'github-actions',
        'openai_api_key_configured_for_run': None if ci_value is None else ci_value == 'true',
        'model_execution_attempted': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ci', action='store_true')
    parser.add_argument('--output', default='reports/model-access.json')
    args = parser.parse_args()
    value = record(os.environ['MODEL_API_CONFIGURED'] if args.ci else None)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
