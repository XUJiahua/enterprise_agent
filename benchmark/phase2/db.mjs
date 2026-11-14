// Private evaluator fixture/snapshot RPC. Never included in candidate exports.
import pg from '../../apps/zenstack/node_modules/pg/lib/index.js';
import { readFileSync } from 'node:fs';
import readline from 'node:readline';
const url=process.env.ADMIN_DATABASE_URL;
if(process.env.POC_DB_RESET!=='1'||!url||!new URL(url).pathname.endsWith('_poc'))throw new Error('disposable _poc database required');
const db=new pg.Client({connectionString:url,query_timeout:10000,statement_timeout:10000});await db.connect();
try{
 for await(const line of readline.createInterface({input:process.stdin})){
  try{
   const input=JSON.parse(line);
   if(input.op==='reset'){
    await db.query('BEGIN');
    await db.query('TRUNCATE "Purchase"');
    await db.query(readFileSync(new URL('../../db/seed.sql',import.meta.url),'utf8'));
    const values=[];const params=[];
    for(const row of input.rows){const start=params.length;params.push(row.id,row.tenantId,row.createdBy,row.status);values.push(`($${start+1},$${start+2},$${start+3},'Private fixture',9,$${start+4})`);}
    if(values.length)await db.query(`INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES ${values.join(',')}`,params);
    await db.query('COMMIT');
    console.log(JSON.stringify({ok:true}));
   }else if(input.op==='snapshot'){
    const rows=(await db.query('SELECT id,"tenantId","createdBy",title,amount,status,"internalNote" FROM "Purchase" ORDER BY id')).rows;
    console.log(JSON.stringify({ok:true,rows}));
   }else throw new Error('unknown private DB operation');
  }catch(e){await db.query('ROLLBACK');console.log(JSON.stringify({ok:false,error:String(e)}));}
 }
}finally{await db.end();}
