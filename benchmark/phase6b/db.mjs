// Administrator-side v6b fixture/failure RPC. Never provided to generation.
import pg from '../../apps/zenstack/node_modules/pg/lib/index.js';
import {readFileSync} from 'node:fs';
import readline from 'node:readline';
const url=process.env.ADMIN_DATABASE_URL;
if(process.env.POC_DB_RESET!=='1'||!url||!new URL(url).pathname.endsWith('_poc'))throw new Error('disposable _poc database required');
const db=new pg.Client({connectionString:url,query_timeout:10000,statement_timeout:10000});await db.connect();
const sql=(p)=>readFileSync(new URL('../../'+p,import.meta.url),'utf8');
const SCHEMA=['db/migrations/001_purchase.sql','db/seed.sql','generated/guard.sql','poc/transactional-action/schema.sql',
 'benchmark/phase4/schema.sql','benchmark/phase5/schema.sql','benchmark/phase6b/schema.sql'];
const TABLES={audit:'ApprovalAudit',request:'ActionRequest',event:'PurchaseEvent',history:'AssignmentHistory'};
async function clearFault(){
 for(const table of Object.values(TABLES))await db.query(`DROP TRIGGER IF EXISTS phase6b_fail ON "${table}"`);
 await db.query('DROP FUNCTION IF EXISTS phase6b_failure()');
}
try{
 for await(const line of readline.createInterface({input:process.stdin})){
  try{
   const input=JSON.parse(line);
   if(input.op==='init'){
    await db.query('DROP TABLE IF EXISTS "AssignmentHistory","PurchaseEvent","ApprovalAudit","ActionRequest" CASCADE');
    await db.query('DROP TABLE IF EXISTS "Purchase","TenantMember" CASCADE');
    await db.query('DROP FUNCTION IF EXISTS phase6b_failure()');
    if(!(await db.query("SELECT 1 FROM pg_roles WHERE rolname='app_runtime'")).rowCount)await db.query(sql('db/bootstrap.sql'));
    for(const p of SCHEMA)await db.query(sql(p));
    console.log(JSON.stringify({ok:true}));
   }else if(input.op==='reset'){
    // Accepts the v3-v5 Oracle row format; v6b rows may also carry an initial assignee and history.
    await clearFault();await db.query('BEGIN');
    await db.query('TRUNCATE "AssignmentHistory","PurchaseEvent","ApprovalAudit","ActionRequest","Purchase"');
    await db.query(sql('db/seed.sql'));
    for(const r of input.rows)await db.query(`INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status,"assigneeId") VALUES ($1,$2,$3,'Action fixture',9,$4,$5)`,[r.id,r.tenantId,r.createdBy,r.status,r.assigneeId??null]);
    for(const h of input.history??[])await db.query(`INSERT INTO "AssignmentHistory" ("tenantId","purchaseId","fromAssignee","toAssignee","changedBy",operation,"idempotencyKey","createdAt") VALUES ($1,$2,$3,$4,$5,$6,$7,now()-interval '1 day')`,[h.tenantId,h.purchaseId,h.fromAssignee,h.toAssignee,h.changedBy,h.operation,h.idempotencyKey]);
    await db.query('COMMIT');console.log(JSON.stringify({ok:true}));
   }else if(input.op==='snapshot'){
    const result={ok:true};
    for(const [key,table,order] of [['purchases','Purchase','id'],['audits','ApprovalAudit','"tenantId","purchaseId","createdAt","approvedBy",operation,"idempotencyKey"'],['requests','ActionRequest','"tenantId","actorId",operation,key'],['events','PurchaseEvent','"tenantId","purchaseId",type,"actorId","idempotencyKey"'],['history','AssignmentHistory','"tenantId","purchaseId","createdAt","changedBy",operation,"idempotencyKey"']]){
     result[key]=(await db.query(`SELECT * FROM "${table}" ORDER BY ${order}`)).rows;
    }
    console.log(JSON.stringify(result));
   }else if(input.op==='fault'){
    await clearFault();
    if(input.target!==null){
     const table=TABLES[input.target];if(!table)throw new Error('unknown fault');
     await db.query(`CREATE FUNCTION phase6b_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled phase6b write failure' USING ERRCODE='P0001'; END $$`);
     await db.query(`CREATE TRIGGER phase6b_fail BEFORE INSERT ON "${table}" FOR EACH ROW EXECUTE FUNCTION phase6b_failure()`);
    }
    console.log(JSON.stringify({ok:true}));
   }else throw new Error('unknown DB operation');
  }catch(e){await db.query('ROLLBACK');console.log(JSON.stringify({ok:false,error:String(e)}));}
 }
}finally{await db.end();}
