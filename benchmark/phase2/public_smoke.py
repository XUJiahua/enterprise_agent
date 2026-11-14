#!/usr/bin/env python3
"""Visible non-destructive examples. Requires an already running test service."""
import argparse
import json
import urllib.error
import urllib.request

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    args = parser.parse_args()
    for user, payload, expected in [(None, {'ids': ['a-own']}, 401), ('carol', {'ids': []}, 400),
                                    ('carol', {'ids': ['a-own'], 'tenantId': 'tenant-b'}, 400)]:
        headers = {'Content-Type': 'application/json'}
        if user:
            headers['Authorization'] = 'Bearer demo_' + user
        request = urllib.request.Request(args.base_url + '/purchases/bulk-approve',
                                         data=json.dumps(payload).encode(), headers=headers, method='POST')
        try:
            result = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            assert result.status == expected, (result.status, expected)
    print('3 visible smoke examples passed')
