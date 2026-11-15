"""Independent v6b Oracle: explicit bulk-reassign contract and scattered implicit conventions, scored separately.

Explicit checks only use what the task text states (HTTP, Purchase rows, ActionRequest
scope/response). Each implicit check tests one convention whose clues are spread over
several exported files (see cohort.json "conventions"). The frozen v5 Oracle (reopen with
its conventions, v4 reject, v3 approve and the original API; 145 cases) runs unchanged
as explicit regression, through a view that hides the v6b additions.
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
REASSIGN='bulk-reassign.v6'
PATH='/purchases/bulk-reassign'
FIELD='reassignedIds'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


phase5=load('phase6b_reused_phase5_oracle',ROOT/'benchmark/phase5/oracle.py')
InfrastructureError=phase5.InfrastructureError
fingerprint=phase5.fingerprint
post=phase5.post
scope=phase5.scope
new_rows=phase5.new_rows


class Database(phase5.Database):
    def __init__(self):
        self.process=subprocess.Popen(['node',str(HERE/'db.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


class V5View:
    """The frozen v5 Oracle compares whole Purchase rows and snapshots; v6b added assigneeId and AssignmentHistory."""
    def __init__(self,db):self.db=db

    def call(self,op,**kwargs):
        value=self.db.call(op,**kwargs)
        if op=='snapshot':
            value.pop('history',None)
            value['purchases']=[{k:v for k,v in r.items() if k!='assigneeId'} for r in value['purchases']]
        return value


def request_hash(assignee,ids):
    """Independent copy of the exported ledger rule for a parameterised batch response."""
    return fingerprint(REASSIGN+'\n'+assignee,ids)


def fixtures(seed):
    prefix='v6b-'+hashlib.sha256(seed.encode()).hexdigest()[:16]+'-'
    names={k:prefix+k for k in ['s1','s2','s3','other','done','gone','missing']}
    names['unicode']='界'*33+'c'
    rows=[{'id':names['s1'],'tenantId':'tenant-a','createdBy':'alice','status':'submitted','assigneeId':'bob'},
          {'id':names['s2'],'tenantId':'tenant-a','createdBy':'alice','status':'submitted'},
          {'id':names['s3'],'tenantId':'tenant-a','createdBy':'zoe','status':'submitted','assigneeId':'carol'},
          {'id':names['unicode'],'tenantId':'tenant-a','createdBy':'alice','status':'submitted'},
          {'id':names['other'],'tenantId':'tenant-b','createdBy':'dave','status':'submitted','assigneeId':'erin'},
          {'id':names['done'],'tenantId':'tenant-a','createdBy':'alice','status':'approved','assigneeId':'bob'},
          {'id':names['gone'],'tenantId':'tenant-a','createdBy':'alice','status':'rejected'}]
    history=[{'tenantId':r['tenantId'],'purchaseId':r['id'],'fromAssignee':None,'toAssignee':r['assigneeId'],
              'changedBy':{'tenant-a':'bob','tenant-b':'erin'}[r['tenantId']],'operation':REASSIGN,'idempotencyKey':'seed'}
             for r in rows if r.get('assigneeId')]
    return names,rows,history


def reassign(ids,assignee='hana',user='carol',key='key',raw=None,headers=None):
    body=json.dumps({'ids':ids,'assignee':assignee},ensure_ascii=False).encode() if raw is None else raw
    return post(PATH,body,user,key,headers)


def explicit_reassign(before,after,ids,assignee,user,key,tenant):
    """Only stated effects: assigneeId of the targets, and one ActionRequest row with the stated response."""
    expected=[{**r,'assigneeId':assignee} if r['id'] in ids else r for r in before['purchases']]
    assert after['purchases']==expected,'unexpected purchase changes'
    previous={scope(r):r for r in before['requests']};current={scope(r):r for r in after['requests']}
    target=(tenant,user,REASSIGN,key)
    assert target not in previous and set(current)==set(previous)|{target},'wrong ActionRequest rows'
    for k,row in previous.items():assert current[k]==row,'existing ActionRequest mutated'
    row=current[target]
    assert row['response']=={FIELD:sorted(ids),'assignee':assignee} and row['createdAt'],row


def run(db,seed):
    names,rows,history=fixtures(seed);cases=[]
    initial={r['id']:r.get('assigneeId') for r in rows}

    def case(name,group,fn):
        db.call('reset',rows=rows,history=history)
        try:fn();cases.append({'case':name,'group':group,'passed':True})
        except InfrastructureError:raise
        except AssertionError as error:cases.append({'case':name,'group':group,'passed':False,'error':str(error)[:500]})
        except Exception as error:raise InfrastructureError('unexpected oracle/request failure: '+str(error)) from error

    def success(ids,assignee='hana',user='carol',key='key',tenant='tenant-a'):
        before=db.call('snapshot');answer=reassign(ids,assignee,user,key)
        assert answer['status']==200 and answer['body']=={FIELD:sorted(set(ids)),'assignee':assignee} and answer['replayed']=='false',answer
        after=db.call('snapshot');explicit_reassign(before,after,set(ids),assignee,user,key,tenant)
        return answer,before,after

    def refuse(ids,statuses=None,**kwargs):
        before=db.call('snapshot');answer=reassign(ids,**kwargs);after=db.call('snapshot')
        ok=answer['status'] in statuses if statuses else 400<=answer['status']<500
        assert ok,{'expected':statuses or '4xx','response':answer,'state_changed':after!=before}
        assert after==before,'refused request mutated state'
        return answer

    # ---- explicit contract -------------------------------------------------
    case('single target','reassign-functional',lambda:success([names['s1']]))
    case('batch is sorted','reassign-functional',lambda:success([names['s3'],names['unicode'],names['s2']],assignee='bob'))
    case('other tenant approver','reassign-functional',lambda:success([names['other']],assignee='ivan',user='erin',tenant='tenant-b'))
    case('assign to caller','reassign-functional',lambda:success([names['s2']],assignee='carol'))
    case('requester','reassign-authorization',lambda:refuse([names['s1']],(403,),user='alice'))
    case('anonymous','reassign-authorization',lambda:refuse([names['s1']],(401,),user=None))
    case('forged context','reassign-authorization',lambda:refuse([names['s1']],(403,),user='alice',
         headers={'X-Role':'approver','X-User':'carol','X-Tenant-ID':'tenant-a'}))
    for label in ['missing','other','done','gone']:
        case('ineligible '+label,'reassign-eligibility',lambda label=label:refuse([names['s1'],names[label]]))
    case('ineligible alone','reassign-eligibility',lambda:refuse([names['done']]))
    for label,assignee in [('unknown','nobody'),('other tenant','erin'),('requester','alice'),('inactive','frank')]:
        case('assignee '+label,'reassign-assignee',lambda assignee=assignee:refuse([names['s1']],assignee=assignee))
    body=json.dumps({'ids':[names['s1']],'assignee':'hana'}).encode()
    for label,kwargs in [('missing key',{'key':None}),('empty ids',{'raw':b'{"ids":[],"assignee":"hana"}'}),
        ('empty assignee',{'assignee':''}),('missing assignee',{'raw':json.dumps({'ids':[names['s1']]}).encode()}),
        ('unknown field',{'raw':json.dumps({'ids':[names['s1']],'assignee':'hana','status':'approved'}).encode()}),
        ('assignee over 100 bytes',{'assignee':'h'*101}),
        ('64 KiB plus one',{'raw':b' '*(65537-len(body))+body,'statuses':(413,)})]:
        statuses=kwargs.pop('statuses',(400,))
        case(label,'reassign-input',lambda kwargs=kwargs,statuses=statuses:refuse([names['s1']],statuses,**kwargs))

    def replay():
        first,_,_=success([names['s2'],names['s1']]);before=db.call('snapshot');second=reassign([names['s1'],names['s2']])
        assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
        assert db.call('snapshot')==before,'replay wrote data'
    case('replay','reassign-idempotency',replay)
    case('same key different purchases','reassign-idempotency',lambda:(success([names['s1']]),refuse([names['s2']],(409,))))
    case('same key different assignee','reassign-idempotency',lambda:(success([names['s1']]),refuse([names['s1']],(409,),assignee='bob')))
    case('key scoped by actor','reassign-idempotency',lambda:(success([names['s1']]),success([names['s2']],user='bob')))

    def parallel(calls):
        barrier=threading.Barrier(len(calls))
        def call(spec):
            ids,assignee,key=spec;barrier.wait(timeout=5);return reassign(ids,assignee,key=key)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [f.result(timeout=20) for f in [pool.submit(call,c) for c in calls]]

    def concurrent_identical():
        before=db.call('snapshot');answers=parallel([([names['s1']],'hana','key'),([names['s1']],'hana','key')])
        assert all(a['status']==200 for a in answers) and sorted(a['replayed'] for a in answers)==['false','true'],answers
        assert answers[0]['raw']==answers[1]['raw'],answers
        explicit_reassign(before,db.call('snapshot'),{names['s1']},'hana','carol','key','tenant-a')

    def concurrent_keys(check=None):
        before=db.call('snapshot')
        answers=parallel([([names['s1'],names['s2']],'hana','key-h'),([names['s2'],names['s1']],'bob','key-b')])
        assert all(a['status']==200 and a['replayed']=='false' for a in answers),answers
        after=db.call('snapshot')
        final={r['id']:r['assigneeId'] for r in after['purchases'] if r['id'] in (names['s1'],names['s2'])}
        assert len(set(final.values()))==1 and set(final.values())<={'hana','bob'},final
        assert [r for r in after['purchases'] if r['id'] not in final]==[r for r in before['purchases'] if r['id'] not in final]
        keys={k for k in (scope(r) for r in after['requests'])}-{scope(r) for r in before['requests']}
        assert keys=={('tenant-a','carol',REASSIGN,'key-h'),('tenant-a','carol',REASSIGN,'key-b')},keys
        if check:check(before,after,final)
    case('concurrent identical','reassign-concurrency',concurrent_identical)
    case('concurrent different keys','reassign-concurrency',concurrent_keys)

    def request_fault():
        db.call('fault',target='request');refuse([names['s1']],(503,))
        db.call('fault',target=None);success([names['s1']])
    case('ActionRequest failure and same-key retry','reassign-failure-rollback',request_fault)

    def decided(op,field,status):
        success([names['s1']]);answer=phase5.batch(op,[names['s1']],user='bob')
        assert answer['status']==200 and answer['body']=={field:[names['s1']]},answer
        row=next(r for r in db.call('snapshot')['purchases'] if r['id']==names['s1'])
        assert row['status']==status and row['assigneeId']=='hana',row
    case('reassigned can be approved','reassign-lifecycle',lambda:decided(phase5.APPROVE,'approvedIds','approved'))
    case('reassigned can be rejected','reassign-lifecycle',lambda:decided(phase5.REJECT,'rejectedIds','rejected'))

    def again():
        decided(phase5.REJECT,'rejectedIds','rejected')
        answer=phase5.reopen(names['s1'],key='reopen-1');assert answer['status']==200,answer
        success([names['s1']],assignee='bob',key='key-2')
    case('reopened purchase can be reassigned again','reassign-lifecycle',again)

    # ---- implicit conventions ----------------------------------------------
    def recorded(ids,assignee='hana',user='carol',tenant='tenant-a',key='key'):
        _,before,after=success(ids,assignee,user,key,tenant)
        added=sorted(({k:v for k,v in r.items() if k!='createdAt'} for r in new_rows(before,after,'history')),key=lambda r:r['purchaseId'])
        expected=[{'tenantId':tenant,'purchaseId':i,'fromAssignee':initial[i],'toAssignee':assignee,'changedBy':user,
                   'operation':REASSIGN,'idempotencyKey':key} for i in sorted(ids)]
        assert added==expected,{'new history rows':added,'expected':expected}
    case('history row','implicit-history',lambda:recorded([names['s1']]))
    case('history rows per target with previous assignee','implicit-history',lambda:recorded([names['s2'],names['s3'],names['s1']],assignee='bob'))

    def chained(before,after,final):
        added=sorted(new_rows(before,after,'history'),key=lambda r:(r['purchaseId'],r['createdAt']))
        for ident in final:
            mine=[r for r in added if r['purchaseId']==ident]
            assert len(mine)==2,{'history rows':mine}
            assert mine[0]['fromAssignee']==initial[ident] and mine[1]['fromAssignee']==mine[0]['toAssignee'],mine
            assert mine[1]['toAssignee']==final[ident],mine
    case('concurrent changes form one chain','implicit-history',lambda:concurrent_keys(chained))

    def fault_then_retry(target,check):
        db.call('fault',target=target);refuse([names['s1']],(503,))
        db.call('fault',target=None);check([names['s1']])
    case('history failure rolls back and same-key retry','implicit-history',lambda:fault_then_retry('history',recorded))

    def hashed(ids,assignee):
        _,_,after=success(ids,assignee)
        row=next(r for r in after['requests'] if scope(r)==('tenant-a','carol',REASSIGN,'key'))
        assert row['requestHash']==request_hash(assignee,ids),{'stored':row['requestHash'],'ledger':request_hash(assignee,ids)}
    case('requestHash','implicit-request-hash',lambda:hashed([names['s1']],'hana'))
    case('requestHash batch Unicode ID','implicit-request-hash',lambda:hashed([names['unicode'],names['s2']],'bob'))

    def evented(ids,user='carol',tenant='tenant-a',assignee='hana'):
        _,before,after=success(ids,assignee,user,'key',tenant)
        added=sorted(({k:v for k,v in r.items() if k not in ('createdAt','deliveredAt')} for r in new_rows(before,after,'events')),
                     key=lambda r:r['purchaseId'])
        assert added==[{'tenantId':tenant,'purchaseId':i,'type':'purchase.reassigned','actorId':user,'idempotencyKey':'key'}
                       for i in sorted(ids)],added
    case('outbox row','implicit-outbox',lambda:evented([names['s1']]))
    case('outbox row per target','implicit-outbox',lambda:evented([names['s2'],names['s1']]))
    case('outbox failure rolls back and same-key retry','implicit-outbox',lambda:fault_then_retry('event',evented))

    def same_denial(assignee):
        unknown=reassign([names['s1']],'nobody',key='probe-unknown')['status'];other=reassign([names['s1']],assignee,key='probe-'+assignee)['status']
        assert unknown==other and 400<=unknown<500,{'unknown':unknown,assignee:other}
    for label,assignee in [('other tenant','erin'),('requester','alice'),('inactive','frank')]:
        case(label+' assignee denied like unknown','implicit-uniform-denial',lambda assignee=assignee:same_denial(assignee))

    # ---- regression: frozen v5 Oracle (reopen + v4 reject + v3 approve + original API), run last ----
    for c in phase5.run(V5View(db),seed):
        if c['group'] not in ('reject-regression','approve-regression','original-regression'):
            c={**c,'case':c['group']+': '+c['case'],'group':'reopen-regression'}
        cases.append(c)
    return cases
