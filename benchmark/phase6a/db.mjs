// Administrator-side v6a fixture/failure RPC. Never provided to generation.
import pg from '../../apps/zenstack/node_modules/pg/lib/index.js';
import {readFileSync} from 'node:fs';
import readline from 'node:readline';
const url=process.env.ADMIN_DATABASE_URL;
if(process.env.POC_DB_RESET!=='1'||!url||!new URL(url).pathname.endsWith('_poc'))throw new Error('disposable _poc database required');
const db=new pg.Client({connectionString:url,query_timeout:10000,statement_timeout:10000});await db.connect();
const sql=(p)=>readFileSync(new URL('../../'+p,import.meta.url),'utf8');
const SCHEMA=['db/migrations/001_purchase.sql','db/seed.sql','generated/guard.sql','poc/transactional-action/schema.sql','benchmark/phase4/schema.sql','benchmark/phase5/schema.sql','benchmark/phase6a/schema.sql'];
async function clearFault(){
 for(const table of ['ApprovalAudit','ActionRequest','PurchaseEvent'])await db.query(`DROP TRIGGER IF EXISTS phase6a_fail ON "${table}"`);
 await db.query('DROP FUNCTION IF EXISTS phase6a_failure()');
}
try{
 for await(const line of readline.createInterface({input:process.stdin})){
  try{
   const input=JSON.parse(line);
   if(input.op==='init'){
    await db.query('DROP TABLE IF EXISTS "PurchaseEvent","ApprovalAudit","ActionRequest" CASCADE');
    await db.query('DROP TABLE IF EXISTS "Purchase" CASCADE');
    await db.query('DROP FUNCTION IF EXISTS phase6a_failure()');
    await db.query('DROP FUNCTION IF EXISTS purchase_tier_guard()');
    if(!(await db.query("SELECT 1 FROM pg_roles WHERE rolname='app_runtime'")).rowCount)await db.query(sql('db/bootstrap.sql'));
    for(const p of SCHEMA)await db.query(sql(p));
    console.log(JSON.stringify({ok:true}));
   }else if(input.op==='reset'){
    // Rows may carry an amount (default 9, below the two-level threshold); requests/audits seed prior first approvals.
    await clearFault();await db.query('BEGIN');
    await db.query('TRUNCATE "PurchaseEvent","ApprovalAudit","ActionRequest","Purchase"');
    await db.query(sql('db/seed.sql'));
    for(const r of input.rows)await db.query(`INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES ($1,$2,$3,'Action fixture',$4,$5)`,[r.id,r.tenantId,r.createdBy,r.amount??9,r.status]);
    for(const q of input.requests??[])await db.query(`INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response) VALUES ($1,$2,$3,$4,$5,$6::jsonb)`,[q.tenantId,q.actorId,q.operation,q.key,q.requestHash,JSON.stringify(q.response)]);
    for(const a of input.audits??[])await db.query(`INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus") VALUES ($1,$2,$3,$4,$5,$6,$7)`,[a.tenantId,a.purchaseId,a.approvedBy,a.operation,a.idempotencyKey,a.fromStatus,a.toStatus]);
    await db.query('COMMIT');console.log(JSON.stringify({ok:true}));
   }else if(input.op==='snapshot'){
    const result={ok:true};
    for(const [key,table,order] of [['purchases','Purchase','id'],['audits','ApprovalAudit','"tenantId","purchaseId","createdAt","approvedBy",operation,"idempotencyKey"'],['requests','ActionRequest','"tenantId","actorId",operation,key'],['events','PurchaseEvent','"tenantId","purchaseId",type,"actorId","idempotencyKey"']]){
     result[key]=(await db.query(`SELECT * FROM "${table}" ORDER BY ${order}`)).rows;
    }
    console.log(JSON.stringify(result));
   }else if(input.op==='fault'){
    await clearFault();
    if(input.target!==null){
     const table={audit:'ApprovalAudit',request:'ActionRequest',event:'PurchaseEvent'}[input.target];if(!table)throw new Error('unknown fault');
     await db.query(`CREATE FUNCTION phase6a_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled phase6a write failure' USING ERRCODE='P0001'; END $$`);
     await db.query(`CREATE TRIGGER phase6a_fail BEFORE INSERT ON "${table}" FOR EACH ROW EXECUTE FUNCTION phase6a_failure()`);
    }
    console.log(JSON.stringify({ok:true}));
   }else throw new Error('unknown DB operation');
  }catch(e){await db.query('ROLLBACK');console.log(JSON.stringify({ok:false,error:String(e)}));}
 }
}finally{await db.end();}
