// Trusted fixture/probe helper. Operates only on an explicitly disposable POC DB.
import pg from '../apps/zenstack/node_modules/pg/lib/index.js';
import { readFileSync } from 'node:fs';
const mode=process.argv[2];
if(process.env.POC_DB_RESET!=='1')throw new Error('POC_DB_RESET required');
const admin=process.env.ADMIN_DATABASE_URL;
if(!admin||!new URL(admin).pathname.endsWith('_poc'))throw new Error('disposable _poc database required');
if(mode==='exec'){
 const db=new pg.Client({connectionString:admin});await db.connect();
 try{const value=JSON.parse(readFileSync(0,'utf8'));const result=await db.query(value.sql,value.params??[]);console.log(JSON.stringify({rows:result.rows,rowCount:result.rowCount}));}finally{await db.end();}
}else if(mode==='batch-probe'){
 const app=new pg.Client({connectionString:process.env.DATABASE_URL});await app.connect();
 try{
  const outcomes=[];
  for(const checked of [false,true]){
   // The previous probe's update is reversed through the trusted administrator.
   const db=new pg.Client({connectionString:admin});await db.connect();
   try{await db.query(`UPDATE "Purchase" SET status='submitted' WHERE id IN ('a-other','b-other')`);}finally{await db.end();}
   await app.query('BEGIN');
   await app.query("SELECT set_config('app.tenant','tenant-a',true),set_config('app.user','carol',true),set_config('app.role','approver',true)");
   const result=await app.query(`UPDATE "Purchase" SET status='approved' WHERE id=ANY($1::text[]) RETURNING id`,[['a-other','b-other']]);
   const rollback=checked&&result.rowCount!==2;
   await app.query(rollback?'ROLLBACK':'COMMIT');
   const inspect=new pg.Client({connectionString:admin});await inspect.connect();
   let rows;try{rows=(await inspect.query(`SELECT id,status FROM "Purchase" WHERE id IN ('a-other','b-other') ORDER BY id`)).rows;}finally{await inspect.end();}
   outcomes.push({mode:checked?'affected-count-checked':'row-policy-only',affected:result.rowCount,rolled_back:rollback,rows});
  }
  if(outcomes[0].affected!==1||outcomes[0].rows[0].status!=='approved'||outcomes[0].rows[1].status!=='submitted')throw new Error('unexpected row-policy-only outcome');
  if(!outcomes[1].rolled_back||outcomes[1].rows.some(r=>r.status!=='submitted'))throw new Error('checked action failed to preserve all-or-nothing business contract');
  console.log(JSON.stringify({kind:'manual-business-contract-probe',new_model_generations:0,outcomes}));
 }finally{await app.end();}
}else throw new Error('unknown probe');
