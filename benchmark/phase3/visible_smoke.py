"""Public HTTP smoke; use a separate public fixture database, never Oracle DB."""
import argparse
import json
import urllib.request
import urllib.error

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--base-url',default='http://127.0.0.1:8080')
    p.add_argument('--ids',nargs='+',default=['a-own','a-other'])
    p.add_argument('--key',default='public-demo-001')
    a=p.parse_args()
    responses=[]
    for ids in (a.ids,list(reversed(a.ids))):
        req=urllib.request.Request(a.base_url+'/purchases/bulk-approve',
            data=json.dumps({'ids':ids}).encode(),headers={'Authorization':'Bearer demo_carol',
            'Content-Type':'application/json','Idempotency-Key':a.key},method='POST')
        try: response=urllib.request.urlopen(req,timeout=15)
        except urllib.error.HTTPError as error: response=error
        with response:
            responses.append((response.status,response.read(),response.headers.get('Idempotency-Replayed')))
    assert responses[0][0]==responses[1][0]==200,responses
    assert responses[0][1]==responses[1][1],responses
    assert [r[2] for r in responses]==['false','true'],responses
    assert json.loads(responses[0][1])=={'approvedIds':sorted(a.ids)},responses
    print('public success and replay smoke passed')
