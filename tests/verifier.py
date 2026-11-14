#!/usr/bin/env python3
"""Adversarial filesystem/Git probes against the real CLI binary."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/'bin/appctl'
results=[]
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for path in ['policies','generated','db']:
        shutil.copytree(ROOT/path,root/path)
    def cmd(*args):return subprocess.run(args,cwd=root,check=True,capture_output=True)
    cmd('git','init','-b','main');cmd('git','config','user.email','poc@example.invalid');cmd('git','config','user.name','POC')
    cmd('git','add','.');cmd('git','commit','-m','trusted baseline')
    baseline=cmd('git','rev-parse','HEAD').stdout.decode().strip()
    def check(name,code=None):
        p=subprocess.run([str(BINARY),'verify','--base',baseline],cwd=root,capture_output=True,text=True)
        value=json.loads(p.stdout);codes=[f['code'] for f in value['findings']]
        assert (p.returncode==0 if code is None else p.returncode!=0 and code in codes),(name,p.stdout,p.stderr)
        results.append({'probe':name,'passed':True,'findings':codes})
    def restore():cmd('git','reset','--hard',baseline);cmd('git','clean','-fd')
    check('safe control')
    policy=root/'policies/purchase.json';p=json.loads(policy.read_text());p['approveRoles'].append('requester');policy.write_text(json.dumps(p))
    cmd(str(BINARY),'compile');check('role expansion survives regenerated SQL','POLICY_EXPANSION');restore()
    path=root/'db/migrations/001_purchase.sql';path.write_text(path.read_text()+'\n-- modified published history\n')
    (root/'db/migrations.lock.json').write_text(json.dumps({'db/migrations/001_purchase.sql':hashlib.sha256(path.read_bytes()).hexdigest()}))
    check('rewritten manifest cannot authorize changed history','PUBLISHED_MIGRATION_CHANGED');restore()
    path=root/'db/migrations/002_drop.sql';path.write_text('ALTER TABLE "Purchase" DROP COLUMN title;')
    lock=json.loads((root/'db/migrations.lock.json').read_text());lock['db/migrations/002_drop.sql']=hashlib.sha256(path.read_bytes()).hexdigest();(root/'db/migrations.lock.json').write_text(json.dumps(lock))
    check('new destructive migration with matching checksum','MIGRATION_REVIEW_REQUIRED');restore()
    path=root/'generated/guard.sql';path.write_text(path.read_text().replace('FORCE ROW LEVEL SECURITY','DISABLE ROW LEVEL SECURITY'))
    check('RLS tamper','GENERATED_DRIFT');restore()
    policy=root/'policies/purchase.json';p=json.loads(policy.read_text());p['approval']['disallowSelf']=False;policy.write_text(json.dumps(p))
    check('self-approval policy weakening','POLICY_INVALID');restore()
    process=subprocess.run([str(BINARY),'verify','--base','nonexistent-ref'],cwd=root,capture_output=True,text=True)
    assert process.returncode!=0 and 'BASE_INVALID' in process.stdout
    results.append({'probe':'invalid trusted baseline fails closed','passed':True})
print(json.dumps({'ok':True,'results':results},indent=2))
