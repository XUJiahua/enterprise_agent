import pg from '../apps/zenstack/node_modules/pg/lib/index.js';
import { readFileSync, readdirSync } from 'node:fs';
const [command, route] = process.argv.slice(2);
if(process.env.POC_DB_RESET!=='1') throw new Error('POC_DB_RESET=1 required; disposable test database only');
const url=process.env.ADMIN_DATABASE_URL;
if(!url || !new URL(url).pathname.endsWith('_poc')) throw new Error('database name must end in _poc');
const db=new pg.Client({connectionString:url});await db.connect();
try {
 if(command==='reset'){
  await db.query('DROP TABLE IF EXISTS "Purchase" CASCADE');
  const exists=await db.query("SELECT 1 FROM pg_roles WHERE rolname='app_runtime'");
  if(!exists.rowCount)await db.query(readFileSync('db/bootstrap.sql','utf8'));
  for(const file of readdirSync('db/migrations').filter(f=>f.endsWith('.sql')).sort())await db.query(readFileSync('db/migrations/'+file,'utf8'));
  await db.query(readFileSync('db/seed.sql','utf8'));
  await db.query(readFileSync(route==='guarded'?'generated/guard.sql':'db/baseline-grants.sql','utf8'));
 }else if(command==='probe'){
  await db.end();
  const app=new pg.Client({connectionString:process.env.DATABASE_URL});await app.connect();
  try {
   // These are actual direct driver queries with no API/Repository in the path.
   const fields='id,"tenantId","createdBy",title,amount,status';
   const noContext=await app.query(`SELECT ${fields} FROM "Purchase"`);
   if(noContext.rowCount!==0)throw new Error('RLS leaked rows without context');
   await app.query('BEGIN');
   await app.query("SELECT set_config('app.tenant','tenant-a',true),set_config('app.user','alice',true),set_config('app.role','requester',true)");
   const rows=await app.query(`SELECT ${fields} FROM "Purchase"`);
   if(!rows.rowCount || rows.rows.some(r=>r.tenantId!=='tenant-a'))throw new Error('direct query escaped tenant');
   const changed=await app.query(`UPDATE "Purchase" SET status='approved' WHERE id='a-other'`);
   if(changed.rowCount!==0)throw new Error('direct update bypassed approval');
   await app.query('ROLLBACK');
   for(const sql of ['SELECT "internalNote" FROM "Purchase"',`UPDATE "Purchase" SET "tenantId"='tenant-b' WHERE id='a-own'`]){
    let blocked=false;
    try{await app.query(sql);}catch(e){if(e.code==='42501')blocked=true;else throw e;}
    if(!blocked)throw new Error('column privilege bypass: '+sql);
   }
   const after=await app.query(`SELECT ${fields} FROM "Purchase"`);
   if(after.rowCount)throw new Error('transaction context leaked after rollback');
   console.log(JSON.stringify({ok:true,checks:6}));
  }finally{await app.end();}
  process.exit(0);
 }else throw new Error('unknown command');
}finally{await db.end();}
