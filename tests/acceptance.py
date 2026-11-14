"""Independent black-box business oracle; never imports an implementation/policy.
Fixed fixture expectations are intentionally authored separately from generated rules.
"""
import concurrent.futures
import os
import json
import urllib.error
import urllib.request
import uuid

BASE = ('http://127.0.0.1:'+str(int(os.environ.get('POC_HTTP_PORT','8080'))))
DENIED = (400, 401, 403, 404, 409)

def request(method, path, user='alice', data=None, headers=None):
    h = {'Content-Type': 'application/json', **(headers or {})}
    if user is not None:
        h['Authorization'] = 'Bearer demo_' + user
    req = urllib.request.Request(BASE + path, data=None if data is None else json.dumps(data).encode(), headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        value = json.load(error)
        if error.code >= 500:
            raise RuntimeError(f'service failure: {error.code} {value}')
        return error.code, value

def expect_denied(result):
    assert result[0] in DENIED, result

def new_purchase():
    ident='case-'+uuid.uuid4().hex
    status,row=request('POST','/purchases',data={'id':ident,'title':'Laptop','amount':100})
    assert status==201 and row['status']=='submitted', (status,row)
    return ident

def anonymous():
    assert request('GET','/purchases',None)[0]==401

def forged_headers():
    status,rows=request('GET','/purchases',headers={'X-Tenant-ID':'tenant-b','X-Role':'admin'})
    assert status==200 and all(row['tenantId']=='tenant-a' for row in rows), (status,rows)

def tenant_list():
    status,rows=request('GET','/purchases')
    assert status==200 and rows and all(row['tenantId']=='tenant-a' for row in rows), (status,rows)

def tenant_detail():
    expect_denied(request('GET','/purchases/b-other'))

def field_hidden():
    status,result=request('GET','/purchases/a-own')
    # A column grant can fail closed with 403. 5xx must never count as protection.
    assert status in (200,403), (status,result)
    if status==200: assert result.get('internalNote') is None, result

def requester_cannot_approve():
    expect_denied(request('POST','/purchases/a-other/approve'))
    status,row=request('GET','/purchases/a-other')
    assert status==200 and row['status']=='submitted', (status,row)

def approver_cannot_self_approve():
    expect_denied(request('POST','/purchases/a-self/approve','bob'))
    assert request('GET','/purchases/a-self','bob')[1]['status']=='submitted'

def cross_tenant_approval():
    expect_denied(request('POST','/purchases/b-other/approve','carol'))
    assert request('GET','/purchases/b-other','dave')[1]['status']=='submitted'

def valid_create():
    ident=new_purchase();status,row=request('GET','/purchases/'+ident)
    assert status==200 and row['createdBy']=='alice' and row['tenantId']=='tenant-a', (status,row)

def approver_cannot_create():
    expect_denied(request('POST','/purchases','bob',{'id':'bad-role','title':'Laptop','amount':100}))

def valid_approval():
    ident=new_purchase()
    status,row=request('POST',f'/purchases/{ident}/approve','bob')
    assert status==200 and row['status']=='approved', (status,row)
    expect_denied(request('POST',f'/purchases/{ident}/approve','bob'))
    assert request('GET','/purchases/'+ident)[1]['status']=='approved'

def concurrent_approval():
    ident=new_purchase()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:request('POST',f'/purchases/{ident}/approve','bob'),range(8)))
    assert sum(s==200 for s,_ in results)==1, results
    assert all(s==200 or s in DENIED for s,_ in results), results

def mass_assignment():
    result=request('POST','/purchases',data={'id':'forged','title':'Laptop','amount':1,'tenantId':'tenant-b','createdBy':'dave','status':'approved'})
    expect_denied(result)
    expect_denied(request('GET','/purchases/forged','dave'))

def bypass_action():
    expect_denied(request('PATCH','/purchases/a-other',data={'status':'approved'}))
    assert request('GET','/purchases/a-other')[1]['status']=='submitted'

def invalid_amount():
    assert request('POST','/purchases',data={'id':'invalid','title':'Laptop','amount':-1})[0]==400

def alternating_context():
    def check(i):
        user,tenant=('alice','tenant-a') if i%2==0 else ('dave','tenant-b')
        s,rows=request('GET','/purchases',user)
        assert s==200 and rows and all(r['tenantId']==tenant for r in rows),(s,rows)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(check,range(60)))

CASES={fn.__name__:fn for fn in [anonymous,forged_headers,tenant_list,tenant_detail,field_hidden,requester_cannot_approve,approver_cannot_self_approve,cross_tenant_approval,valid_create,approver_cannot_create,valid_approval,concurrent_approval,mass_assignment,bypass_action,invalid_amount,alternating_context]}
FAULTS={
 'omit-tenant':['tenant_list','tenant_detail'],
 'skip-approval-check':['requester_cannot_approve','approver_cannot_self_approve'],
 'leak-field':['field_hidden'],
 'mass-assignment':['mass_assignment'],
 'bypass-action':['bypass_action'],
 'disable-policy':['tenant_list','tenant_detail'],
}

def run(names=None):
    results=[]
    for name in names or CASES:
        try:
            CASES[name]();results.append({'case':name,'passed':True})
        except AssertionError as error:
            results.append({'case':name,'passed':False,'error':str(error),'kind':'invariant_failed'})
        except Exception as error:
            results.append({'case':name,'passed':False,'error':repr(error),'kind':'harness_error'})
    return results
