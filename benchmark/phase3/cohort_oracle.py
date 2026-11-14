"""Seeded fixtures for a new cohort; the PR 12 mechanism Oracle is unchanged."""
import hashlib
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

mechanism=load('phase3_frozen_mechanism_oracle',HERE/'oracle.py')
Database=mechanism.Database
InfrastructureError=mechanism.InfrastructureError

def run(db,seed):
    oracle=load('phase3_seeded_oracle',HERE/'oracle.py')
    # One exception identity across the RPC helper and independent test runner.
    oracle.InfrastructureError=InfrastructureError
    names,maximum,rows=oracle.fixtures()
    # Keep deliberate length/escaping probes; blind only ordinary identifiers.
    prefix='trial-'+hashlib.sha256(seed.encode()).hexdigest()[:16]+'-'
    mapping={v:prefix+v for k,v in names.items() if k not in ('unicode','quote')}
    mapping.update({v:prefix+v for v in maximum})
    names={k:mapping.get(v,v) for k,v in names.items()}
    maximum=[mapping[v] for v in maximum]
    rows=[{**row,'id':mapping.get(row['id'],row['id'])} for row in rows]
    oracle.fixtures=lambda:(names,maximum,rows)
    cases=oracle.run(db)
    original=load('phase3_original_regression',HERE.parents[1]/'tests/acceptance.py')
    return cases+[{'group':'original-regression',**case} for case in original.run()]
