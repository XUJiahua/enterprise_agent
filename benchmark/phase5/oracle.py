"""Independent v5 Oracle: explicit reopen contract and scattered implicit conventions, scored separately.

Explicit checks only use what the task text states (HTTP, Purchase state, ActionRequest
scope/response). Each implicit check tests one convention whose clues are spread over
several exported files (see cohort.json "conventions"). The frozen v4 Oracle (reject,
v3 approve and the original API, 108 cases) runs unchanged as explicit regression.
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
import urllib.parse
import urllib.request

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
REOPEN,APPROVE,REJECT='reopen.v5','bulk-approve.v3','bulk-reject.v4'
REASON='Supplier quote corrected'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


phase4=load('phase5_reused_phase4_oracle',ROOT/'benchmark/phase4/oracle.py')
InfrastructureError=phase4.InfrastructureError


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


class LegacyView:
    """The frozen v3/v4 Oracles compare whole audit rows; v5 only added the reason column."""
    def __init__(self,db):self.db=db

    def call(self,op,**kwargs):
        value=self.db.call(op,**kwargs)
        if op=='snapshot':value['audits']=[{k:v for k,v in r.items() if k!='reason'} for r in value['audits']]
        return value


fingerprint=phase4.fingerprint


def fixtures(seed):
    prefix='v5-'+hashlib.sha256(seed.encode()).hexdigest()[:16]+'-'
    names={k:prefix+k for k in ['r1','r2','r3','zoe','other','sub','done','missing']}
    names['unicode']='界'*33+'c'
    rows=[{'id':names[k],'tenantId':'tenant-a','createdBy':'alice','status':'rejected'} for k in ['r1','r2','r3','unicode']]
    rows+=[{'id':names['zoe'],'tenantId':'tenant-a','createdBy':'zoe','status':'rejected'},
           {'id':names['other'],'tenantId':'tenant-b','createdBy':'dave','status':'rejected'},
           {'id':names['sub'],'tenantId':'tenant-a','createdBy':'alice','status':'submitted'},
           {'id':names['done'],'tenantId':'tenant-a','createdBy':'alice','status':'approved'}]
    return names,rows


def port():return os.environ.get('POC_HTTP_PORT','8080')


def post(path,body,user,key,headers=None):
    h={'Content-Type':'application/json',**(headers or {})}
    if user is not None:h['Authorization']='Bearer demo_'+user
    if key is not None:h['Idempotency-Key']=key
    req=urllib.request.Request('http://127.0.0.1:'+port()+path,data=body,headers=h,method='POST')
    try:response=urllib.request.urlopen(req,timeout=15)
    except urllib.error.HTTPError as error:response=error
    with response:
        data=response.read()
        try:value=json.loads(data)
        except ValueError:value={'invalid_json_response':data[:200].decode(errors='replace')}
        return {'status':response.status,'body':value,'raw':data.decode(errors='replace'),'replayed':response.headers.get('Idempotency-Replayed')}


def reopen(ident,reason=REASON,user='alice',key='key',raw=None,headers=None):
    body=json.dumps({'reason':reason},ensure_ascii=False).encode() if raw is None else raw
    return post('/purchases/'+urllib.parse.quote(ident,safe='')+'/reopen',body,user,key,headers)


def batch(op,ids,user='carol',key='batch-key'):
    path={APPROVE:'/purchases/bulk-approve',REJECT:'/purchases/bulk-reject'}[op]
    return post(path,json.dumps({'ids':ids}).encode(),user,key)


def scope(row):return (row['tenantId'],row['actorId'],row['operation'],row['key'])


def explicit_reopen(before,after,ident,user,key,tenant):
    """Only stated effects: Purchase status and one ActionRequest row with the stated response."""
    expected=[{**r,'status':'submitted'} if r['id']==ident else r for r in before['purchases']]
    assert after['purchases']==expected,'unexpected purchase changes'
    previous={scope(r):r for r in before['requests']};current={scope(r):r for r in after['requests']}
    target=(tenant,user,REOPEN,key)
    assert target not in previous and set(current)==set(previous)|{target},'wrong ActionRequest rows'
    for k,row in previous.items():assert current[k]==row,'existing ActionRequest mutated'
    row=current[target]
    assert row['response']=={'id':ident,'status':'submitted'} and row['createdAt'],row


def new_rows(before,after,table):
    rest=[r for r in after[table]]
    for r in before[table]:
        assert r in rest,table+' row mutated or removed'
        rest.remove(r)
    return rest


def run(db,seed):
    names,rows=fixtures(seed);cases=[]

    def case(name,group,fn):
        db.call('reset',rows=rows)
        try:fn();cases.append({'case':name,'group':group,'passed':True})
        except InfrastructureError:raise
        except AssertionError as error:cases.append({'case':name,'group':group,'passed':False,'error':str(error)[:500]})
        except Exception as error:raise InfrastructureError('unexpected oracle/request failure: '+str(error)) from error

    def success(ident,user='alice',key='key',tenant='tenant-a',reason=REASON):
        before=db.call('snapshot');answer=reopen(ident,reason,user,key)
        assert answer['status']==200 and answer['body']=={'id':ident,'status':'submitted'} and answer['replayed']=='false',answer
        after=db.call('snapshot');explicit_reopen(before,after,ident,user,key,tenant)
        return answer,before,after

    def refuse(ident,statuses=None,**kwargs):
        before=db.call('snapshot');answer=reopen(ident,**kwargs);after=db.call('snapshot')
        ok=answer['status'] in statuses if statuses else 400<=answer['status']<500
        assert ok,{'expected':statuses or '4xx','response':answer,'state_changed':after!=before}
        assert after==before,'refused request mutated state'
        return answer

    # ---- explicit contract -------------------------------------------------
    case('reopen own rejected','reopen-functional',lambda:success(names['r1']))
    case('other tenant requester','reopen-functional',lambda:success(names['other'],user='dave',tenant='tenant-b'))
    case('100 byte Unicode ID','reopen-functional',lambda:success(names['unicode']))
    case('approver','reopen-authorization',lambda:refuse(names['r1'],(403,),user='carol'))
    case('anonymous','reopen-authorization',lambda:refuse(names['r1'],(401,),user=None))
    case('forged context','reopen-authorization',lambda:refuse(names['r1'],(403,),user='carol',
         headers={'X-Role':'requester','X-User':'alice','X-Tenant-ID':'tenant-a'}))
    for label in ['missing','other','zoe','sub','done']:
        case('ineligible '+label,'reopen-eligibility',lambda label=label:refuse(names[label]))
    body=json.dumps({'reason':REASON}).encode()
    for label,kwargs in [('missing key',{'key':None}),('empty reason',{'reason':''}),
        ('unknown field',{'raw':json.dumps({'reason':REASON,'status':'approved'}).encode()}),
        ('reason over 200 bytes',{'reason':'r'*201}),
        ('64 KiB plus one',{'raw':b' '*(65537-len(body))+body,'statuses':(413,)})]:
        statuses=kwargs.pop('statuses',(400,))
        case(label,'reopen-input',lambda kwargs=kwargs,statuses=statuses:refuse(names['r1'],statuses,**kwargs))

    def replay():
        first,_,_=success(names['r1']);before=db.call('snapshot');second=reopen(names['r1'])
        assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
        assert db.call('snapshot')==before,'replay wrote data'
    case('replay','reopen-idempotency',replay)
    case('same key different purchase','reopen-idempotency',lambda:(success(names['r1']),refuse(names['r2'],(409,))))
    case('key scoped by actor','reopen-idempotency',lambda:(success(names['r1']),success(names['other'],user='dave',tenant='tenant-b')))

    def parallel(calls):
        barrier=threading.Barrier(len(calls))
        def call(spec):
            ident,key=spec;barrier.wait(timeout=5);return reopen(ident,key=key)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [f.result(timeout=20) for f in [pool.submit(call,c) for c in calls]]

    def concurrent_identical():
        before=db.call('snapshot');answers=parallel([(names['r1'],'key'),(names['r1'],'key')])
        assert all(a['status']==200 for a in answers) and sorted(a['replayed'] for a in answers)==['false','true'],answers
        assert answers[0]['raw']==answers[1]['raw'],answers
        explicit_reopen(before,db.call('snapshot'),names['r1'],'alice','key','tenant-a')

    def concurrent_keys():
        before=db.call('snapshot');answers=parallel([(names['r1'],'key'),(names['r1'],'other-key')])
        winners=[i for i,a in enumerate(answers) if a['status']==200]
        assert len(winners)==1 and 400<=answers[1-winners[0]]['status']<500,answers
        explicit_reopen(before,db.call('snapshot'),names['r1'],'alice',['key','other-key'][winners[0]],'tenant-a')
    case('concurrent identical','reopen-concurrency',concurrent_identical)
    case('concurrent different keys','reopen-concurrency',concurrent_keys)

    def request_fault():
        db.call('fault',target='request');refuse(names['r1'],(503,))
        db.call('fault',target=None);success(names['r1'])
    case('ActionRequest failure and same-key retry','reopen-failure-rollback',request_fault)

    def decided(op):
        success(names['r1']);answer=batch(op,[names['r1']])
        field={APPROVE:'approvedIds',REJECT:'rejectedIds'}[op]
        assert answer['status']==200 and answer['body']=={field:[names['r1']]},answer
        status={APPROVE:'approved',REJECT:'rejected'}[op]
        assert next(r for r in db.call('snapshot')['purchases'] if r['id']==names['r1'])['status']==status
    case('reopened can be approved','reopen-lifecycle',lambda:decided(APPROVE))
    case('reopened can be rejected again','reopen-lifecycle',lambda:decided(REJECT))
    case('reopen again after second rejection','reopen-lifecycle',lambda:(decided(REJECT),success(names['r1'],key='key-2')))

    # ---- implicit conventions ----------------------------------------------
    def audited(ident,user='alice',tenant='tenant-a',key='key'):
        _,before,after=success(ident,user=user,tenant=tenant,key=key)
        added=new_rows(before,after,'audits')
        assert len(added)==1,{'new audit rows':added}
        row={k:v for k,v in added[0].items() if k!='createdAt'}
        assert row=={'tenantId':tenant,'purchaseId':ident,'approvedBy':user,'operation':REOPEN,'idempotencyKey':key,
                     'fromStatus':'rejected','toStatus':'submitted','reason':REASON},row
    case('audit row','implicit-audit',lambda:audited(names['r1']))
    case('audit row other tenant','implicit-audit',lambda:audited(names['other'],user='dave',tenant='tenant-b'))

    def fault_then_retry(target,check):
        db.call('fault',target=target);refuse(names['r1'],(503,))
        db.call('fault',target=None);check(names['r1'])
    case('audit failure rolls back and same-key retry','implicit-audit',lambda:fault_then_retry('audit',audited))

    def hashed(ident):
        _,_,after=success(ident)
        row=next(r for r in after['requests'] if scope(r)==('tenant-a','alice',REOPEN,'key'))
        assert row['requestHash']==fingerprint(REOPEN,[ident]),{'stored':row['requestHash'],'ledger':fingerprint(REOPEN,[ident])}
    case('requestHash','implicit-request-hash',lambda:hashed(names['r1']))
    case('requestHash Unicode ID','implicit-request-hash',lambda:hashed(names['unicode']))

    def evented(ident,user='alice',tenant='tenant-a'):
        _,before,after=success(ident,user=user,tenant=tenant)
        added=[{k:v for k,v in r.items() if k not in ('createdAt','deliveredAt')} for r in new_rows(before,after,'events')]
        assert added==[{'tenantId':tenant,'purchaseId':ident,'type':'purchase.reopened','actorId':user,'idempotencyKey':'key'}],added
    case('outbox row','implicit-outbox',lambda:evented(names['r1']))
    case('outbox row other tenant','implicit-outbox',lambda:evented(names['other'],user='dave',tenant='tenant-b'))
    case('outbox failure rolls back and same-key retry','implicit-outbox',lambda:fault_then_retry('event',evented))

    def same_denial(label):
        missing=reopen(names['missing'],key='probe-missing')['status'];other=reopen(names[label],key='probe-'+label)['status']
        assert missing==other and 400<=missing<500,{'missing':missing,label:other}
    for label in ['other','zoe','sub','done']:
        case(label+' denied like missing','implicit-uniform-denial',lambda label=label:same_denial(label))

    # ---- regression: frozen v4 Oracle (reject + v3 approve + original API), run last ----
    for c in phase4.run(LegacyView(db),seed):
        if c['group'] not in ('approve-regression','original-regression'):
            c={**c,'case':c['group']+': '+c['case'],'group':'reject-regression'}
        cases.append(c)
    return cases
