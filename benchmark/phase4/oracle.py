"""Independent v4 Oracle: explicit reject contract and implicit conventions, scored separately.

Explicit checks only use what the task text states (HTTP, Purchase state, ActionRequest
scope/response). Implicit checks each test one convention that lives in the exported code
(see cohort.json "conventions"); no explicit check depends on an implicit convention.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
APPROVE,REJECT='bulk-approve.v3','bulk-reject.v4'
DENIED=(403,404,409)


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


# Frozen v3 approve Oracle (50 cases) + original API acceptance (16), reused unchanged.
phase3=load('phase4_reused_phase3_cohort_oracle',ROOT/'benchmark/phase3/cohort_oracle.py')
InfrastructureError=phase3.InfrastructureError


class Database:
    def __init__(self):
        self.process=subprocess.Popen(['node',str(HERE/'db.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def call(self,op,**kwargs):
        try:
            self.process.stdin.write(json.dumps({'op':op,**kwargs})+'\n');self.process.stdin.flush()
            line=self.process.stdout.readline()
            if not line:raise InfrastructureError('database RPC exited')
            value=json.loads(line)
            if not value['ok']:raise InfrastructureError(value.get('error','database operation failed'))
            return {k:v for k,v in value.items() if k!='ok'}
        except (OSError,ValueError) as error:raise InfrastructureError(str(error)) from error

    def close(self):
        self.process.stdin.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


def fingerprint(operation,ids):
    """Independent copy of the exported ledger.Fingerprint rule."""
    value=operation+'\n'+''.join(str(len(i.encode()))+':'+i for i in sorted(ids))
    return hashlib.sha256(value.encode()).hexdigest()


def fixtures(seed):
    prefix='v4-'+hashlib.sha256(seed.encode()).hexdigest()[:16]+'-'
    names={k:prefix+k for k in ['a1','a2','a3','own','other','done','gone','missing']}
    names['unicode']='界'*33+'b'
    rows=[{'id':names[k],'tenantId':'tenant-a','createdBy':'alice','status':'submitted'} for k in ['a1','a2','a3','unicode']]
    rows+=[{'id':names['own'],'tenantId':'tenant-a','createdBy':'bob','status':'submitted'},
           {'id':names['other'],'tenantId':'tenant-b','createdBy':'dave','status':'submitted'},
           {'id':names['done'],'tenantId':'tenant-a','createdBy':'alice','status':'approved'},
           {'id':names['gone'],'tenantId':'tenant-a','createdBy':'alice','status':'rejected'}]
    maximum=[prefix+'max-'+str(i).zfill(3) for i in range(100)]
    rows+=[{'id':i,'tenantId':'tenant-a','createdBy':'alice','status':'submitted'} for i in maximum]
    return names,maximum,rows


def request(path,payload=None,user='carol',key='key',raw=None,headers=None):
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode() if raw is None else raw
    h={'Content-Type':'application/json',**(headers or {})}
    if user is not None:h['Authorization']='Bearer demo_'+user
    if key is not None:h['Idempotency-Key']=key
    req=urllib.request.Request('http://127.0.0.1:'+os.environ.get('POC_HTTP_PORT','8080')+path,data=body,headers=h,method='POST')
    try:response=urllib.request.urlopen(req,timeout=15)
    except urllib.error.HTTPError as error:response=error
    with response:
        body=response.read()
        try:value=json.loads(body)
        except ValueError:value={'invalid_json_response':body[:200].decode(errors='replace')}
        return {'status':response.status,'body':value,'raw':body.decode(errors='replace'),'replayed':response.headers.get('Idempotency-Replayed')}


ACTIONS={REJECT:('/purchases/bulk-reject','rejected','rejectedIds'),APPROVE:('/purchases/bulk-approve','approved','approvedIds')}


def scope(row):return (row['tenantId'],row['actorId'],row['operation'],row['key'])


def explicit_transition(before,after,ids,user,key,tenant,op=REJECT):
    """Only stated effects: Purchase status and one ActionRequest row with the stated response."""
    _,status,field=ACTIONS[op]
    expected=[{**r,'status':status} if r['id'] in ids else r for r in before['purchases']]
    assert after['purchases']==expected,'unexpected purchase changes'
    previous={scope(r):r for r in before['requests']};current={scope(r):r for r in after['requests']}
    target=(tenant,user,op,key)
    assert target not in previous and set(current)==set(previous)|{target},'wrong ActionRequest rows'
    for ident,row in previous.items():assert current[ident]==row,'existing ActionRequest mutated'
    row=current[target]
    assert row['response']=={field:sorted(ids)} and row['createdAt'],row


def audit_transition(before,after,ids,user,key,tenant):
    prior={(r['tenantId'],r['purchaseId']):r for r in before['audits']}
    current={(r['tenantId'],r['purchaseId']):r for r in after['audits']}
    assert set(current)==set(prior)|{(tenant,i) for i in ids},'wrong audit target set'
    for ident,row in prior.items():assert current[ident]==row,'existing audit mutated'
    for ident in ids:
        row=current[(tenant,ident)]
        assert {k:v for k,v in row.items() if k!='createdAt'}=={'tenantId':tenant,'purchaseId':ident,'approvedBy':user,
            'operation':REJECT,'idempotencyKey':key,'fromStatus':'submitted','toStatus':'rejected'},row
        assert row['createdAt'],'missing audit timestamp'


def run(db,seed):
    names,maximum,rows=fixtures(seed);cases=[]

    def case(name,group,fn):
        db.call('reset',rows=rows)
        try:fn();cases.append({'case':name,'group':group,'passed':True})
        except InfrastructureError:raise
        except AssertionError as error:cases.append({'case':name,'group':group,'passed':False,'error':str(error)[:500]})
        except Exception as error:raise InfrastructureError('unexpected oracle/request failure: '+str(error)) from error

    def success(ids,user='carol',key='key',tenant='tenant-a',op=REJECT,raw=None):
        path,_,field=ACTIONS[op]
        before=db.call('snapshot');answer=request(path,{'ids':ids},user,key,raw)
        assert answer['status']==200 and answer['body']=={field:sorted(ids)} and answer['replayed']=='false',answer
        after=db.call('snapshot');explicit_transition(before,after,ids,user,key,tenant,op)
        return answer,before,after

    def refuse(payload,user='carol',key='key',statuses=DENIED,raw=None,headers=None,op=REJECT):
        before=db.call('snapshot');answer=request(ACTIONS[op][0],payload,user,key,raw,headers);after=db.call('snapshot')
        assert answer['status'] in statuses,{'expected':statuses,'response':answer,'state_changed':after!=before}
        assert after==before,'rejected request mutated state'

    def ineligible(target):
        return {'ids':[names['a1'],names[target]]},('bob' if target=='own' else 'carol')

    # ---- explicit contract -------------------------------------------------
    for label,ids,user,tenant in [('two targets',[names['a2'],names['a1']],'carol','tenant-a'),
        ('100 targets',maximum,'carol','tenant-a'),('100 byte Unicode ID',[names['unicode']],'carol','tenant-a'),
        ('another approver',[names['a1']],'bob','tenant-a'),('other tenant approver',[names['other']],'erin','tenant-b')]:
        case(label,'reject-functional',lambda ids=ids,user=user,tenant=tenant:success(ids,user=user,tenant=tenant))
    for label,user,headers,statuses in [('requester','alice',None,(403,)),('anonymous',None,None,(401,)),
        ('forged context','alice',{'X-Role':'approver','X-Tenant-ID':'tenant-b'},(403,))]:
        case(label,'reject-authorization',lambda user=user,headers=headers,statuses=statuses:
             refuse({'ids':[names['a1']]},user=user,headers=headers,statuses=statuses))
    for target in ['own','other','done','gone','missing']:
        payload,user=ineligible(target)
        case('mixed '+target,'reject-atomicity',lambda payload=payload,user=user:refuse(payload,user=user))
    body=json.dumps({'ids':[names['a1']]}).encode()
    for label,kwargs in [('empty list',{'payload':{'ids':[]},'statuses':(400,)}),
        ('unknown field',{'payload':{'ids':[names['a1']],'tenantId':'tenant-b'},'statuses':(400,)}),
        ('missing key',{'payload':{'ids':[names['a1']]},'key':None,'statuses':(400,)}),
        ('64 KiB plus one',{'payload':None,'raw':b' '*(65537-len(body))+body,'statuses':(413,)})]:
        case(label,'reject-input',lambda kwargs=kwargs:refuse(**kwargs))

    def replay():
        first,_,_=success([names['a2'],names['a1']]);before=db.call('snapshot')
        second=request(ACTIONS[REJECT][0],{'ids':[names['a1'],names['a2']]})
        assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
        assert db.call('snapshot')==before,'replay wrote data'
    case('reordered replay','reject-idempotency',replay)
    case('same key different set','reject-idempotency',lambda:(success([names['a1']]),refuse({'ids':[names['a2']]},statuses=(409,))))
    case('new key cannot reject again','reject-idempotency',lambda:(success([names['a1']]),refuse({'ids':[names['a1']]},key='new-key')))
    case('key scoped by actor','reject-idempotency',lambda:(success([names['a1']],user='bob'),success([names['a2']])))
    case('key scoped by tenant','reject-idempotency',lambda:(success([names['a1']]),success([names['other']],user='erin',tenant='tenant-b')))
    case('requester cannot reuse approver key','reject-idempotency',lambda:(success([names['a1']]),refuse({'ids':[names['a1']]},user='alice',statuses=(403,))))

    def parallel(calls):
        barrier=threading.Barrier(len(calls))
        def call(spec):
            op,ids,key=spec;barrier.wait(timeout=5);return request(ACTIONS[op][0],{'ids':ids},key=key)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [f.result(timeout=20) for f in [pool.submit(call,c) for c in calls]]

    def concurrent_identical():
        ids=[names['a1'],names['a2']];before=db.call('snapshot')
        answers=parallel([(REJECT,ids,'key'),(REJECT,ids,'key')])
        assert all(a['status']==200 for a in answers) and sorted(a['replayed'] for a in answers)==['false','true'],answers
        assert answers[0]['raw']==answers[1]['raw'],answers
        explicit_transition(before,db.call('snapshot'),ids,'carol','key','tenant-a')

    def concurrent_race(calls):
        before=db.call('snapshot');answers=parallel(calls)
        winners=[i for i,a in enumerate(answers) if a['status']==200]
        assert len(winners)==1 and answers[1-winners[0]]['status'] in DENIED,answers
        op,ids,key=calls[winners[0]]
        assert answers[winners[0]]['body']=={ACTIONS[op][2]:sorted(ids)},answers
        explicit_transition(before,db.call('snapshot'),ids,'carol',key,'tenant-a',op)
    case('concurrent identical','reject-concurrency',concurrent_identical)
    case('concurrent overlap','reject-concurrency',lambda:concurrent_race([(REJECT,[names['a1'],names['a2']],'key'),(REJECT,[names['a2'],names['a3']],'other-key')]))
    case('concurrent approve and reject','reject-concurrency',lambda:concurrent_race([(APPROVE,[names['a1']],'approve-key'),(REJECT,[names['a1']],'reject-key')]))

    def request_fault():
        db.call('fault',target='request');refuse({'ids':[names['a1'],names['a2']]},statuses=(503,))
        db.call('fault',target=None);success([names['a1'],names['a2']])
    case('ActionRequest failure and same-key retry','reject-failure-rollback',request_fault)
    case('rejected cannot be approved','reject-lifecycle',lambda:(success([names['a1']]),refuse({'ids':[names['a1']]},key='key-2',op=APPROVE)))
    case('approved cannot be rejected','reject-lifecycle',lambda:(success([names['a1']],op=APPROVE),refuse({'ids':[names['a1']]},key='key-2')))

    # ---- implicit conventions ----------------------------------------------
    def audited(ids,user='carol',tenant='tenant-a'):
        _,before,after=success(ids,user=user,tenant=tenant);audit_transition(before,after,ids,user,'key',tenant)
    case('audit rows','implicit-audit',lambda:audited([names['a2'],names['a1']]))
    case('audit rows other tenant','implicit-audit',lambda:audited([names['other']],user='erin',tenant='tenant-b'))

    def audit_fault():
        db.call('fault',target='audit');refuse({'ids':[names['a1'],names['a2']]},statuses=(503,))
        db.call('fault',target=None);audited([names['a1'],names['a2']])
    case('audit failure rolls back and same-key retry','implicit-audit',audit_fault)

    def cross_operation(replay):
        success([names['a1']],key='shared',op=APPROVE);first,_,_=success([names['a2']],key='shared')
        if replay:
            before=db.call('snapshot');second=request(ACTIONS[REJECT][0],{'ids':[names['a2']]},key='shared')
            assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
            assert db.call('snapshot')==before,'replay wrote data'
    case('approve key does not block reject','implicit-operation-scope',lambda:cross_operation(False))
    case('reject replay after approve with same key','implicit-operation-scope',lambda:cross_operation(True))

    def hashed(ids):
        _,_,after=success(list(reversed(ids)))
        row=next(r for r in after['requests'] if scope(r)==('tenant-a','carol',REJECT,'key'))
        assert row['requestHash']==fingerprint(REJECT,ids),{'stored':row['requestHash'],'ledger':fingerprint(REJECT,ids)}
    case('requestHash two targets','implicit-request-hash',lambda:hashed([names['a1'],names['a2']]))
    case('requestHash 100 targets','implicit-request-hash',lambda:hashed(maximum))

    for target in ['own','other','done','gone','missing']:
        payload,user=ineligible(target)
        case('mixed '+target+' is 409','implicit-uniform-409',lambda payload=payload,user=user:refuse(payload,user=user,statuses=(409,)))
    case('already rejected with new key is 409','implicit-uniform-409',lambda:(success([names['a1']]),refuse({'ids':[names['a1']]},key='new-key',statuses=(409,))))

    # ---- regression: frozen v3 approve Oracle + original APIs (runs last, as in v3) ----
    for c in phase3.run(db,seed):
        if c['group']!='original-regression':c={**c,'case':c['group']+': '+c['case'],'group':'approve-regression'}
        cases.append(c)
    return cases
