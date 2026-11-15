"""Independent v6a Oracle: explicit countersign contract and scattered implicit conventions, scored separately.

Explicit checks only use what the task text states (HTTP, Purchase state, ActionRequest
scope/response). Each implicit check tests one convention whose clues are spread over
several exported files (see cohort.json "conventions"). The frozen v5 Oracle (reopen, reject,
v3 approve and the original API, 145 cases) runs unchanged as explicit regression.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.parse

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
COUNTERSIGN,APPROVE,REJECT='countersign.v6','bulk-approve.v3','bulk-reject.v4'
OVER=50000
REGRESSION=('reject-regression','approve-regression','original-regression')


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


phase5=load('phase6a_reused_phase5_oracle',ROOT/'benchmark/phase5/oracle.py')
InfrastructureError=phase5.InfrastructureError
fingerprint=phase5.fingerprint
post=phase5.post


class Database(phase5.Database):
    def __init__(self):
        self.process=subprocess.Popen(['node',str(HERE/'db.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


def fixtures(seed):
    """Pending-second rows carry a seeded first approval (ActionRequest + ApprovalAudit) by `first`."""
    prefix='v6a-'+hashlib.sha256(seed.encode()).hexdigest()[:16]+'-'
    names={k:prefix+k for k in ['c1','c2','shared','other','own','sub','done','missing','big','small','edge']}
    names['unicode']='界'*33+'c'
    spec=[('c1','tenant-a','alice','pending-second',60000,'bob'),('c2','tenant-a','alice','pending-second',70000,'bob'),
          ('unicode','tenant-a','alice','pending-second',65000,'bob'),('shared','tenant-a','alice','pending-second',90000,'zed'),
          ('other','tenant-b','dave','pending-second',80000,'frank'),('own','tenant-a','carol','pending-second',75000,'bob'),
          ('sub','tenant-a','alice','submitted',60000,None),('done','tenant-a','alice','approved',60000,None),
          ('big','tenant-a','alice','submitted',60000,None),('small','tenant-a','alice','submitted',500,None),
          ('edge','tenant-a','alice','submitted',OVER,None)]
    rows,requests,audits=[],[],[]
    for label,tenant,creator,status,amount,first in spec:
        ident=names[label];rows.append({'id':ident,'tenantId':tenant,'createdBy':creator,'status':status,'amount':amount})
        if first:
            key='seed-'+label
            requests.append({'tenantId':tenant,'actorId':first,'operation':APPROVE,'key':key,
                             'requestHash':fingerprint(APPROVE,[ident]),'response':{'approvedIds':[ident]}})
            audits.append({'tenantId':tenant,'purchaseId':ident,'approvedBy':first,'operation':APPROVE,'idempotencyKey':key,
                           'fromStatus':'submitted','toStatus':'pending-second'})
    amounts={label:amount for label,_,_,_,amount,_ in spec};amounts['missing']=60000
    return names,amounts,{'rows':rows,'requests':requests,'audits':audits}


def countersign(ident,amount,user='carol',key='key',raw=None,headers=None):
    body=json.dumps({'amount':amount}).encode() if raw is None else raw
    return post('/purchases/'+urllib.parse.quote(ident,safe='')+'/countersign',body,user,key,headers)


def batch(op,ids,user='bob',key='batch-key'):
    path={APPROVE:'/purchases/bulk-approve',REJECT:'/purchases/bulk-reject'}[op]
    return post(path,json.dumps({'ids':ids}).encode(),user,key)


scope=phase5.scope
new_rows=phase5.new_rows


def explicit_countersign(before,after,ident,user,key,tenant):
    """Only stated effects: Purchase status and one ActionRequest row with the stated response."""
    expected=[{**r,'status':'approved'} if r['id']==ident else r for r in before['purchases']]
    assert after['purchases']==expected,'unexpected purchase changes'
    previous={scope(r):r for r in before['requests']};current={scope(r):r for r in after['requests']}
    target=(tenant,user,COUNTERSIGN,key)
    assert target not in previous and set(current)==set(previous)|{target},'wrong ActionRequest rows'
    for k,row in previous.items():assert current[k]==row,'existing ActionRequest mutated'
    row=current[target]
    assert row['response']=={'id':ident,'status':'approved'} and row['createdAt'],row


def status_of(db,ident):return next(r for r in db.call('snapshot')['purchases'] if r['id']==ident)['status']


def run(db,seed):
    names,amounts,state=fixtures(seed);cases=[]

    def case(name,group,fn):
        db.call('reset',**state)
        try:fn();cases.append({'case':name,'group':group,'passed':True})
        except InfrastructureError:raise
        except AssertionError as error:cases.append({'case':name,'group':group,'passed':False,'error':str(error)[:500]})
        except Exception as error:raise InfrastructureError('unexpected oracle/request failure: '+str(error)) from error

    def success(label,user='carol',key='key',tenant='tenant-a'):
        ident=names[label];before=db.call('snapshot');answer=countersign(ident,amounts[label],user,key)
        assert answer['status']==200 and answer['body']=={'id':ident,'status':'approved'} and answer['replayed']=='false',answer
        after=db.call('snapshot');explicit_countersign(before,after,ident,user,key,tenant)
        return answer,before,after

    def refuse(label,statuses=None,amount=None,**kwargs):
        before=db.call('snapshot');answer=countersign(names[label],amounts[label] if amount is None else amount,**kwargs)
        after=db.call('snapshot')
        ok=answer['status'] in statuses if statuses else 400<=answer['status']<500
        assert ok,{'expected':statuses or '4xx','response':answer,'state_changed':after!=before}
        assert after==before,'refused request mutated state'
        return answer

    # ---- explicit contract -------------------------------------------------
    case('countersign pending-second','countersign-functional',lambda:success('c1'))
    case('other tenant approver','countersign-functional',lambda:success('other',user='erin',tenant='tenant-b'))
    case('100 byte Unicode ID','countersign-functional',lambda:success('unicode'))
    case('requester','countersign-authorization',lambda:refuse('c1',(403,),user='alice'))
    case('anonymous','countersign-authorization',lambda:refuse('c1',(401,),user=None))
    case('forged context','countersign-authorization',lambda:refuse('c1',(403,),user='alice',
         headers={'X-Role':'approver','X-User':'carol','X-Tenant-ID':'tenant-a'}))
    for label in ['missing','other','sub','done','own']:
        case('ineligible '+label,'countersign-eligibility',lambda label=label:refuse(label))
    case('first approver cannot countersign','countersign-eligibility',lambda:refuse('c1',user='bob'))
    case('amount mismatch','countersign-amount',lambda:refuse('c1',(409,),amount=amounts['c1']-1))
    case('mismatch does not consume key','countersign-amount',lambda:(refuse('c1',(409,),amount=amounts['c1']+1),success('c1')))
    body=json.dumps({'amount':amounts['c1']}).encode()
    for label,kwargs in [('missing key',{'key':None}),('empty body',{'raw':b''}),
        ('unknown field',{'raw':json.dumps({'amount':amounts['c1'],'status':'approved'}).encode()}),
        ('string amount',{'raw':json.dumps({'amount':str(amounts['c1'])}).encode()}),
        ('fractional amount',{'raw':b'{"amount":60000.5}'}),('zero amount',{'amount':0}),
        ('64 KiB plus one',{'raw':b' '*(65537-len(body))+body,'statuses':(413,)})]:
        statuses=kwargs.pop('statuses',(400,))
        case(label,'countersign-input',lambda kwargs=kwargs,statuses=statuses:refuse('c1',statuses,**kwargs))

    def replay():
        first,_,_=success('c1');before=db.call('snapshot');second=countersign(names['c1'],amounts['c1'])
        assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
        assert db.call('snapshot')==before,'replay wrote data'
    case('replay','countersign-idempotency',replay)
    case('same key different purchase','countersign-idempotency',lambda:(success('c1'),refuse('c2',(409,))))
    case('key scoped by actor','countersign-idempotency',lambda:(success('c1'),success('other',user='erin',tenant='tenant-b')))

    def parallel(calls):
        barrier=threading.Barrier(len(calls))
        def call(spec):
            label,user,key=spec;barrier.wait(timeout=5);return countersign(names[label],amounts[label],user,key)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [f.result(timeout=20) for f in [pool.submit(call,c) for c in calls]]

    def concurrent_identical():
        before=db.call('snapshot');answers=parallel([('c1','carol','key'),('c1','carol','key')])
        assert all(a['status']==200 for a in answers) and sorted(a['replayed'] for a in answers)==['false','true'],answers
        assert answers[0]['raw']==answers[1]['raw'],answers
        explicit_countersign(before,db.call('snapshot'),names['c1'],'carol','key','tenant-a')

    def one_winner(calls):
        before=db.call('snapshot');answers=parallel(calls)
        winners=[i for i,a in enumerate(answers) if a['status']==200]
        assert len(winners)==1 and 400<=answers[1-winners[0]]['status']<500,answers
        _,user,key=calls[winners[0]]
        explicit_countersign(before,db.call('snapshot'),names[calls[0][0]],user,key,'tenant-a')
    case('concurrent identical','countersign-concurrency',concurrent_identical)
    case('concurrent different keys','countersign-concurrency',lambda:one_winner([('c1','carol','key'),('c1','carol','other-key')]))
    case('concurrent different approvers','countersign-concurrency',lambda:one_winner([('shared','carol','key'),('shared','bob','key')]))

    def request_fault():
        db.call('fault',target='request');refuse('c1',(503,))
        db.call('fault',target=None);success('c1')
    case('ActionRequest failure and same-key retry','countersign-failure-rollback',request_fault)

    def final():
        success('c1');before=db.call('snapshot')
        for answer in [batch(APPROVE,[names['c1']],user='carol',key='k2'),batch(REJECT,[names['c1']],user='carol',key='k3'),
                       countersign(names['c1'],amounts['c1'],key='k4')]:
            assert 400<=answer['status']<500,answer
        assert db.call('snapshot')==before,'approved purchase changed'

    def end_to_end():
        ident=names['big']+'-new'
        created=post('/purchases',json.dumps({'id':ident,'title':'Server rack','amount':OVER+1}).encode(),'alice',None)
        assert created['status']==201,created
        first=batch(APPROVE,[ident],user='bob',key='e2e')
        assert first['status']==200 and first['body']=={'approvedIds':[ident]} and status_of(db,ident)=='pending-second',first
        assert 400<=countersign(ident,OVER+1,user='bob',key='e2e')['status']<500
        second=countersign(ident,OVER+1,user='carol',key='e2e')
        assert second['status']==200 and second['body']=={'id':ident,'status':'approved'},second
        assert status_of(db,ident)=='approved'

    def after_reopen():
        rejected=batch(REJECT,[names['big']],user='bob',key='r1');assert rejected['status']==200,rejected
        reopened=post('/purchases/'+names['big']+'/reopen',json.dumps({'reason':'Quote attached'}).encode(),'alice','r2')
        assert reopened['status']==200,reopened
        first=batch(APPROVE,[names['big']],user='carol',key='r3');assert first['status']==200,first
        assert status_of(db,names['big'])=='pending-second'
        second=countersign(names['big'],amounts['big'],user='bob',key='r4')
        assert second['status']==200 and status_of(db,names['big'])=='approved',second
    case('countersigned purchase is final','countersign-lifecycle',final)
    case('create, first approval, countersign','countersign-lifecycle',end_to_end)
    case('reject, reopen, first approval, countersign','countersign-lifecycle',after_reopen)

    # Start-state tier behaviour of bulk-approve (explicit regression; the task must preserve it).
    def tier(labels,expected):
        answer=batch(APPROVE,[names[l] for l in labels])
        assert answer['status']==200 and answer['body']=={'approvedIds':sorted(names[l] for l in labels)},answer
        for label,status in expected.items():assert status_of(db,names[label])==status,(label,status)
    case('above threshold goes to pending-second','tier-regression',lambda:tier(['big'],{'big':'pending-second'}))
    case('mixed batch','tier-regression',lambda:tier(['big','small'],{'big':'pending-second','small':'approved'}))
    case('threshold amount approves directly','tier-regression',lambda:tier(['edge'],{'edge':'approved'}))

    def pending_not_batch():
        before=db.call('snapshot')
        for op in (APPROVE,REJECT):
            answer=batch(op,[names['c1']],user='carol');assert 400<=answer['status']<500,(op,answer)
        assert db.call('snapshot')==before
    case('pending-second not accepted by batch Actions','tier-regression',pending_not_batch)

    # ---- implicit conventions ----------------------------------------------
    def audited(label,user='carol',tenant='tenant-a'):
        _,before,after=success(label,user=user,tenant=tenant)
        added=new_rows(before,after,'audits')
        assert len(added)==1,{'new audit rows':added}
        row={k:v for k,v in added[0].items() if k!='createdAt'}
        assert row=={'tenantId':tenant,'purchaseId':names[label],'approvedBy':user,'operation':COUNTERSIGN,'idempotencyKey':'key',
                     'fromStatus':'pending-second','toStatus':'approved','reason':None},row
    case('audit row','implicit-audit',lambda:audited('c1'))
    case('audit row other tenant','implicit-audit',lambda:audited('other',user='erin',tenant='tenant-b'))

    def fault_then_retry(target,check):
        db.call('fault',target=target);refuse('c1',(503,))
        db.call('fault',target=None);check('c1')
    case('audit failure rolls back and same-key retry','implicit-audit',lambda:fault_then_retry('audit',audited))

    def hashed(label):
        _,_,after=success(label)
        row=next(r for r in after['requests'] if scope(r)==('tenant-a','carol',COUNTERSIGN,'key'))
        expected=fingerprint(COUNTERSIGN,[names[label]])
        assert row['requestHash']==expected,{'stored':row['requestHash'],'ledger':expected}
    case('requestHash','implicit-request-hash',lambda:hashed('c1'))
    case('requestHash Unicode ID','implicit-request-hash',lambda:hashed('unicode'))

    def evented(label,user='carol',tenant='tenant-a'):
        _,before,after=success(label,user=user,tenant=tenant)
        added=[{k:v for k,v in r.items() if k not in ('createdAt','deliveredAt')} for r in new_rows(before,after,'events')]
        assert added==[{'tenantId':tenant,'purchaseId':names[label],'type':'purchase.countersigned','actorId':user,'idempotencyKey':'key'}],added
    case('outbox row','implicit-outbox',lambda:evented('c1'))
    case('outbox row other tenant','implicit-outbox',lambda:evented('other',user='erin',tenant='tenant-b'))
    case('outbox failure rolls back and same-key retry','implicit-outbox',lambda:fault_then_retry('event',evented))

    def same_denial(label,user='carol'):
        missing=countersign(names['missing'],amounts['missing'],user,'probe-missing')['status']
        other=countersign(names[label],amounts[label],user,'probe-'+label)['status']
        assert missing==other and 400<=missing<500,{'missing':missing,label:other}
    for label in ['other','sub','done','own']:
        case(label+' denied like missing','implicit-uniform-denial',lambda label=label:same_denial(label))
    case('first approver denied like missing','implicit-uniform-denial',lambda:same_denial('c1',user='bob'))

    # ---- regression: frozen v5 Oracle (reopen + reject + v3 approve + original API), run last ----
    for c in phase5.run(db,seed):
        if c['group'] not in REGRESSION:c={**c,'case':c['group']+': '+c['case'],'group':'reopen-regression'}
        cases.append(c)
    return cases
