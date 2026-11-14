"""Independent HTTP + three-table Oracle for the transactional Action contract."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.request

HERE=Path(__file__).resolve().parent
OPERATION='bulk-approve.v3'
DENIED=(403,404,409)


class InfrastructureError(RuntimeError): pass


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


def fixtures():
    names={k:'v3-'+k for k in ['a1','a2','a3','own','other','done','missing']}
    names['unicode']='界'*33+'a';names['quote']="v3-'quote\\id"
    rows=[{'id':names[k],'tenantId':'tenant-a','createdBy':'alice','status':'submitted'} for k in ['a1','a2','a3','unicode','quote']]
    rows += [{'id':names['own'],'tenantId':'tenant-a','createdBy':'bob','status':'submitted'},
             {'id':names['other'],'tenantId':'tenant-b','createdBy':'dave','status':'submitted'},
             {'id':names['done'],'tenantId':'tenant-a','createdBy':'alice','status':'approved'}]
    maximum=['v3-max-'+str(i).zfill(3) for i in range(100)]
    rows += [{'id':ident,'tenantId':'tenant-a','createdBy':'alice','status':'submitted'} for ident in maximum]
    return names,maximum,rows


def request(payload=None,user='carol',key='key',raw=None,headers=None):
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode() if raw is None else raw
    h={'Content-Type':'application/json',**(headers or {})}
    if user is not None:h['Authorization']='Bearer demo_'+user
    if key is not None:h['Idempotency-Key']=key
    req=urllib.request.Request('http://127.0.0.1:'+os.environ.get('POC_HTTP_PORT','8080')+'/purchases/bulk-approve',data=body,headers=h,method='POST')
    try:response=urllib.request.urlopen(req,timeout=15)
    except urllib.error.HTTPError as error:response=error
    with response:
        body=response.read()
        try:value=json.loads(body)
        except ValueError:value={'invalid_json_response':body[:200].decode(errors='replace')}
        return {'status':response.status,'body':value,'raw':body.decode(errors='replace'),'replayed':response.headers.get('Idempotency-Replayed')}


def fingerprint(ids):
    value=OPERATION+'\n'+''.join(str(len(i.encode()))+':'+i for i in sorted(ids))
    return hashlib.sha256(value.encode()).hexdigest()


def assert_transition(before,after,ids,user='carol',key='key',tenant='tenant-a'):
    expected=[{**r,'status':'approved'} if r['id'] in ids else r for r in before['purchases']]
    assert after['purchases']==expected,'unexpected purchase changes'
    prior_audits={(r['tenantId'],r['purchaseId']):r for r in before['audits']}
    current={(r['tenantId'],r['purchaseId']):r for r in after['audits']}
    assert set(current)==set(prior_audits)|{(tenant,i) for i in ids},'wrong audit target set'
    for ident,row in prior_audits.items():assert current[ident]==row,'existing audit mutated'
    for ident in ids:
        row=current[(tenant,ident)]
        assert {k:v for k,v in row.items() if k!='createdAt'}=={'tenantId':tenant,'purchaseId':ident,'approvedBy':user,'operation':OPERATION,'idempotencyKey':key,'fromStatus':'submitted','toStatus':'approved'},row
        assert row['createdAt'],'missing audit timestamp'
    scope=lambda r:(r['tenantId'],r['actorId'],r['operation'],r['key'])
    previous={scope(r):r for r in before['requests']};current={scope(r):r for r in after['requests']}
    target=(tenant,user,OPERATION,key)
    assert set(current)==set(previous)|{target},'wrong completed-request scope'
    for ident,row in previous.items():assert current[ident]==row,'completed request mutated'
    row=current[target]
    assert row['requestHash']==fingerprint(ids) and row['response']=={'approvedIds':sorted(ids)} and row['createdAt'],row


def run(db):
    names,maximum,rows=fixtures();cases=[]

    def case(name,group,fn):
        db.call('reset',rows=rows)
        try:fn();cases.append({'case':name,'group':group,'passed':True})
        except InfrastructureError:raise
        except AssertionError as error:cases.append({'case':name,'group':group,'passed':False,'error':str(error)})
        except Exception as error:raise InfrastructureError('unexpected oracle/request failure: '+str(error)) from error

    def success(ids,user='carol',key='key',tenant='tenant-a',raw=None):
        before=db.call('snapshot');answer=request({'ids':ids},user,key,raw)
        assert answer['status']==200 and answer['body']=={'approvedIds':sorted(ids)} and answer['replayed']=='false',answer
        assert_transition(before,db.call('snapshot'),ids,user,key,tenant)
        return answer

    def reject(payload,user='carol',key='key',statuses=DENIED,raw=None,headers=None):
        before=db.call('snapshot');answer=request(payload,user,key,raw,headers);after=db.call('snapshot')
        assert answer['status'] in statuses,{'response':answer,'state_changed':after!=before}
        assert after==before,'rejected request mutated state/audit/idempotency'

    for label,ids,user,tenant in [('two targets',[names['a2'],names['a1']],'carol','tenant-a'),
        ('100 targets',maximum,'carol','tenant-a'),('100 byte Unicode ID',[names['unicode']],'carol','tenant-a'),
        ('quoted ID',[names['quote']],'carol','tenant-a'),('another approver',[names['a1']],'bob','tenant-a'),
        ('other tenant approver',[names['other']],'erin','tenant-b')]:
        case(label,'functional',lambda ids=ids,user=user,tenant=tenant:success(ids,user=user,tenant=tenant))
    for label,payload,user,headers,statuses in [('requester',{'ids':[names['own']]},'alice',None,(403,)),
        ('anonymous',{'ids':[names['a1']]},None,None,(401,)),('cross tenant',{'ids':[names['other']]},'carol',None,DENIED),
        ('self approval',{'ids':[names['own']]},'bob',None,DENIED),
        ('forged context',{'ids':[names['own']]},'alice',{'X-Role':'approver','X-Tenant-ID':'tenant-b'},(403,))]:
        case(label,'authorization',lambda payload=payload,user=user,headers=headers,statuses=statuses:reject(payload,user=user,headers=headers,statuses=statuses))
    for target in ['own','other','done','missing']:
        case('mixed '+target,'atomicity',lambda target=target:reject({'ids':[names['a1'],names[target]]},user='bob' if target=='own' else 'carol'))
    invalid=[('empty list',{'ids':[]}),('duplicate IDs',{'ids':[names['a1'],names['a1']]}),
        ('101 IDs',{'ids':maximum+[names['a1']]}),('null IDs',{'ids':None}),('null member',{'ids':[None]}),
        ('number member',{'ids':[1]}),('empty ID',{'ids':['']}),('102 byte ID',{'ids':['界'*34]}),
        ('unknown field',{'ids':[names['a1']],'tenantId':'tenant-b'}),('case sensitive field',{'IDS':[names['a1']]}),
        ('array root',[]),('null root',None)]
    for name,payload in invalid:case(name,'input',lambda payload=payload:reject(payload,statuses=(400,)))
    for name,raw in [('malformed JSON',b'{'),('duplicate field',b'{"ids":["v3-a1"],"ids":["v3-a2"]}'),
        ('trailing JSON',b'{"ids":["v3-a1"]} {}'),('invalid UTF8',b'{"ids":["\xff"]}')]:
        case(name,'input',lambda raw=raw:reject(None,raw=raw,statuses=(400,)))
    for name,key in [('missing key',None),('empty key',''),('bad key','bad/key'),('129 byte key','x'*129)]:
        case(name,'input',lambda key=key:reject({'ids':[names['a1']]},key=key,statuses=(400,)))
    case('128 byte key','input',lambda:success([names['a1']],key='x'*128))
    body=json.dumps({'ids':[names['a1']]}).encode()
    case('exact 64 KiB body','input',lambda:success([names['a1']],raw=b' '*(65536-len(body))+body))
    case('64 KiB plus one','input',lambda:reject(None,raw=b' '*(65537-len(body))+body,statuses=(413,)))
    case('NUL absent target','input',lambda:reject({'ids':['\x00']}))

    def replay():
        first=success([names['a2'],names['a1']]);before=db.call('snapshot')
        second=request({'ids':[names['a1'],names['a2']]})
        assert second['status']==200 and second['raw']==first['raw'] and second['replayed']=='true',second
        assert db.call('snapshot')==before,'replay wrote data'
    case('reordered replay','idempotency',replay)

    def conflict():
        success([names['a1']]);reject({'ids':[names['a2']]},statuses=(409,))
    case('same key different payload','idempotency',conflict)

    def different_key():
        success([names['a1']]);reject({'ids':[names['a1']]},key='new-key',statuses=(409,))
    case('new key cannot approve again','idempotency',different_key)

    def actor_scope():
        success([names['a1']],user='bob');success([names['a2']],user='carol')
    case('key scoped by actor','idempotency',actor_scope)

    def tenant_scope():
        success([names['a1']]);success([names['other']],user='erin',tenant='tenant-b')
    case('key scoped by tenant','idempotency',tenant_scope)

    def unauthorized_replay():
        success([names['a1']]);reject({'ids':[names['a1']]},user='alice',statuses=(403,))
    case('requester cannot reuse approver key','idempotency',unauthorized_replay)

    def concurrent_case(kind):
        before=db.call('snapshot');barrier=threading.Barrier(2)
        sets=[[names['a1'],names['a2']],[names['a1'],names['a2']] if kind=='identical' else [names['a2'],names['a3']]]
        keys=['key','other-key' if kind=='overlap' else 'key']
        def call(i):barrier.wait(timeout=5);return request({'ids':sets[i]},key=keys[i])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(call,i) for i in [0,1]];answers=[f.result(timeout=20) for f in futures]
        winners=[i for i,a in enumerate(answers) if a['status']==200]
        if kind=='identical':
            assert len(winners)==2 and sorted(a['replayed'] for a in answers)==['false','true'],answers
            assert answers[0]['raw']==answers[1]['raw'],answers
            winner=0
        else:
            assert len(winners)==1 and answers[1-winners[0]]['status']==409,answers
            winner=winners[0]
        assert answers[winner]['body']=={'approvedIds':sorted(sets[winner])},answers
        assert_transition(before,db.call('snapshot'),sets[winner],key=keys[winner])
    for kind in ['identical','mismatch','overlap']:
        case('concurrent '+kind,'concurrency',lambda kind=kind:concurrent_case(kind))

    def fault(target):
        db.call('fault',target=target)
        reject({'ids':[names['a1'],names['a2']]},statuses=(503,))
        db.call('fault',target=None)
        success([names['a1'],names['a2']])
    for target in ['audit','request']:case(target+' failure and same-key retry','failure-rollback',lambda target=target:fault(target))
    # Leave populated tenant-a metadata for direct runtime RLS/append-only probes.
    db.call('reset',rows=rows)
    if not any(not c['passed'] for c in cases):success([names['a1'],names['a2']])
    return cases
