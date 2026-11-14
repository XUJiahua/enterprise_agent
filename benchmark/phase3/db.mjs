// Administrator-side fixture/RLS/failure RPC. Never provided to generation.
import pg from '../../apps/zenstack/node_modules/pg/lib/index.js';
import {readFileSync} from 'node:fs';
import readline from 'node:readline';
const url=process.env.ADMIN_DATABASE_URL;
if(process.env.POC_DB_RESET!=='1'||!url||!new URL(url).pathname.endsWith('_poc'))throw new Error('disposable _poc database required');
const db=new pg.Client({connectionString:url,query_timeout:10000,statement_timeout:10000});await db.connect();
const sql=(p)=>readFileSync(new URL('../../'+p,import.meta.url),'utf8');
async function clearFault(){
 for(const table of ['ApprovalAudit','ActionRequest'])await db.query(`DROP TRIGGER IF EXISTS phase3_fail ON "${table}"`);
 await db.query('DROP FUNCTION IF EXISTS phase3_failure()');
}
try{
 for await(const line of readline.createInterface({input:process.stdin})){
  try{
   const input=JSON.parse(line);
   if(input.op==='init'){
    await db.query('DROP TABLE IF EXISTS "ApprovalAudit","ActionRequest" CASCADE');
    await db.query('DROP TABLE IF EXISTS "Purchase" CASCADE');
    await db.query('DROP FUNCTION IF EXISTS phase3_failure()');
    if(!(await db.query("SELECT 1 FROM pg_roles WHERE rolname='app_runtime'")).rowCount)await db.query(sql('db/bootstrap.sql'));
    for(const p of ['db/migrations/001_purchase.sql','db/seed.sql','generated/guard.sql','poc/transactional-action/schema.sql'])await db.query(sql(p));
    console.log(JSON.stringify({ok:true}));
   }else if(input.op==='reset'){
    await clearFault();await db.query('BEGIN');
    await db.query('TRUNCATE "ApprovalAudit","ActionRequest","Purchase"');
    await db.query(sql('db/seed.sql'));
    for(const r of input.rows)await db.query(`INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES ($1,$2,$3,'Action fixture',9,$4)`,[r.id,r.tenantId,r.createdBy,r.status]);
    await db.query('COMMIT');console.log(JSON.stringify({ok:true}));
   }else if(input.op==='snapshot'){
    const result={ok:true};
    for(const [key,table,order] of [['purchases','Purchase','id'],['audits','ApprovalAudit','"tenantId","purchaseId"'],['requests','ActionRequest','"tenantId","actorId",operation,key']]){
     result[key]=(await db.query(`SELECT * FROM "${table}" ORDER BY ${order}`)).rows;
    }
    console.log(JSON.stringify(result));
   }else if(input.op==='fault'){
    await clearFault();
    if(input.target!==null){
     const table={audit:'ApprovalAudit',request:'ActionRequest'}[input.target];if(!table)throw new Error('unknown fault');
     await db.query(`CREATE FUNCTION phase3_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled phase3 write failure' USING ERRCODE='P0001'; END $$`);
     await db.query(`CREATE TRIGGER phase3_fail BEFORE INSERT ON "${table}" FOR EACH ROW EXECUTE FUNCTION phase3_failure()`);
    }
    console.log(JSON.stringify({ok:true}));
   }else if(input.op==='misconfigure'){
    const statements={purchase:'ALTER TABLE "Purchase" DISABLE ROW LEVEL SECURITY',audit:'ALTER TABLE "ApprovalAudit" DISABLE ROW LEVEL SECURITY',writable:'GRANT UPDATE ON "ActionRequest" TO app_runtime'};
    if(!statements[input.target])throw new Error('unknown misconfiguration');await db.query(statements[input.target]);console.log(JSON.stringify({ok:true}));
   }else if(input.op==='probe'){
    // Direct runtime SQL checks cover the two new table boundaries, without HTTP.
    const app=new pg.Client({connectionString:process.env.DATABASE_URL});await app.connect();let checks=0;
    try{
     for(const table of ['ActionRequest','ApprovalAudit']){
      const result=await app.query(`SELECT * FROM "${table}"`);if(result.rowCount)throw new Error('metadata visible without context');checks++;
      for(const operation of ['UPDATE','DELETE']){
       let denied=false;
       try{await app.query(operation==='UPDATE'?`UPDATE "${table}" SET operation=operation`:`DELETE FROM "${table}"`);}catch(e){if(e.code!=='42501')throw e;denied=true;}
       if(!denied)throw new Error('metadata not append-only');checks++;
      }
     }
     await app.query('BEGIN');await app.query("SELECT set_config('app.tenant','tenant-b',true),set_config('app.user','erin',true),set_config('app.role','approver',true)");
     for(const table of ['ActionRequest','ApprovalAudit']){if((await app.query(`SELECT * FROM "${table}"`)).rowCount)throw new Error('metadata escaped tenant');checks++;}
     await app.query('ROLLBACK');
     for(const statement of [
      `INSERT INTO "ActionRequest" ("tenantId","actorId",operation,key,"requestHash",response) VALUES ('tenant-b','erin','bulk-approve.v3','forged',repeat('0',64),'{"approvedIds":[]}')`,
      `INSERT INTO "ApprovalAudit" ("tenantId","purchaseId","approvedBy",operation,"idempotencyKey","fromStatus","toStatus") VALUES ('tenant-b','b-other','erin','bulk-approve.v3','forged','submitted','approved')`]){
      await app.query('BEGIN');await app.query("SELECT set_config('app.tenant','tenant-a',true),set_config('app.user','carol',true),set_config('app.role','approver',true)");let denied=false;
      try{await app.query(statement);}catch(e){if(e.code!=='42501')throw e;denied=true;}finally{await app.query('ROLLBACK');}
      if(!denied)throw new Error('forged metadata insert allowed');checks++;
     }
     console.log(JSON.stringify({ok:true,checks}));
    }finally{await app.end();}
   }else throw new Error('unknown DB operation');
  }catch(e){await db.query('ROLLBACK');console.log(JSON.stringify({ok:false,error:String(e)}));}
 }
}finally{await db.end();}
