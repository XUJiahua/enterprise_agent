import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { ZenStackClient, ORMErrorReason } from '@zenstackhq/orm';
import { PostgresDialect } from '@zenstackhq/orm/dialects/postgres';
import { PolicyPlugin } from '@zenstackhq/plugin-policy';
import { Pool } from 'pg';
import { schema } from '../zenstack/schema.ts';

const port = Number(process.env.POC_HTTP_PORT ?? '8080');
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('invalid POC_HTTP_PORT');
const mutation = process.env.POC_MUTATION ?? '';
if (mutation && process.env.POC_ALLOW_MUTATIONS !== '1') throw new Error('fault injection is opt-in');
if (!['', 'omit-tenant', 'skip-approval-check', 'leak-field', 'mass-assignment', 'bypass-action', 'disable-policy'].includes(mutation)) throw new Error('unknown mutation');
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const privilege = await pool.query(`SELECT rolsuper OR rolbypassrls OR pg_has_role(current_user, c.relowner, 'MEMBER') AS unsafe FROM pg_roles r CROSS JOIN pg_class c WHERE r.rolname=current_user AND c.oid='"Purchase"'::regclass`);
if (privilege.rows[0]?.unsafe !== false) throw new Error('runtime requires non-owner non-privileged DB role');
const raw = new ZenStackClient(schema, { dialect: new PostgresDialect({ pool }) });
const secured = raw.$use(new PolicyPlugin());
const users: Record<string, {id:string;tenantId:string;role:string}> = {
  demo_alice: {id:'alice',tenantId:'tenant-a',role:'requester'},
  demo_bob: {id:'bob',tenantId:'tenant-a',role:'approver'},
  demo_carol: {id:'carol',tenantId:'tenant-a',role:'approver'},
  demo_dave: {id:'dave',tenantId:'tenant-b',role:'requester'},
  demo_erin: {id:'erin',tenantId:'tenant-b',role:'approver'},
};
const select = {id:true,tenantId:true,createdBy:true,title:true,amount:true,status:true} as const;
function send(res:ServerResponse, status:number, value:unknown) { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(value)); }
function deny(res:ServerResponse, status:number) { send(res,status,{error:status}); }
async function body(req:IncomingMessage): Promise<Record<string,unknown>> {
  let data=''; for await (const b of req) { data+=b; if(data.length>65536) throw Object.assign(new Error('body too big'),{http:400}); }
  const value:unknown = JSON.parse(data);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Object.assign(new Error('invalid body'),{http:400});
  return value as Record<string,unknown>;
}
createServer(async(req,res)=>{
 try {
  const path = (req.url??'').split('?')[0];
  if(path==='/health'){await pool.query('SELECT 1');send(res,200,{ok:true});return;}
  const auth=req.headers.authorization??'';const user=users[auth.startsWith('Bearer ')?auth.slice(7):''];
  if(!user){deny(res,401);return;}
  const db=(mutation==='disable-policy'?raw:secured).$setAuth(user);
  const parts=path.split('/').filter(Boolean);
  if(parts[0]!=='purchases'){deny(res,404);return;}
  if(req.method==='GET' && parts.length<=2){
   // omit-tenant deliberately removes the ordinary query filter. Policy still applies.
   const where={...(mutation==='omit-tenant'||mutation==='disable-policy'?{}:{tenantId:user.tenantId}),...(parts[1]?{id:parts[1]}:{})};
   const rows=await db.purchase.findMany({where,select:mutation==='leak-field'?{...select,internalNote:true}:select,orderBy:{id:'asc'}});
   if(parts.length===2){if(!rows.length){deny(res,404);return;}send(res,200,rows[0]);}else send(res,200,rows);
   return;
  }
  if(req.method==='POST' && parts.length===1){
   const input=await body(req);
   if(mutation!=='mass-assignment'&&Object.keys(input).some(k=>!['id','title','amount'].includes(k))){deny(res,400);return;}
   if(typeof input.id!=='string'||!input.id||input.id.length>100||typeof input.title!=='string'||!input.title||input.title.length>200||!Number.isInteger(input.amount)||Number(input.amount)<=0||Number(input.amount)>2147483647){deny(res,400);return;}
   const data={id:input.id,title:input.title,amount:Number(input.amount),tenantId:user.tenantId,createdBy:user.id,status:'submitted'};
   if(mutation==='mass-assignment'){
    if(typeof input.tenantId==='string')data.tenantId=input.tenantId;
    if(typeof input.createdBy==='string')data.createdBy=input.createdBy;
    if(typeof input.status==='string')data.status=input.status;
   }
   const result=await db.purchase.create({data,select});send(res,201,result);return;
  }
  const approve=req.method==='POST'&&parts.length===3&&parts[2]==='approve';
  const bypass=req.method==='PATCH'&&parts.length===2&&mutation==='bypass-action';
  if(approve||bypass){
   if(!bypass&&mutation!=='skip-approval-check'&&user.role!=='approver'){deny(res,403);return;}
   // Atomic conditional update avoids read-check-write races even without serializable isolation.
   const result=await db.purchase.updateMany({where:{id:parts[1],status:'submitted'},data:{status:'approved'}});
   if(result.count!==1){deny(res,404);return;}
   const resultRow=await db.purchase.findUnique({where:{id:parts[1]},select});send(res,200,resultRow);return;
  }
  deny(res,req.method==='PATCH'?403:404);
 }catch(error){
  const e=error as {reason?:string;code?:string;http?:number};
  if(e.http){deny(res,e.http);return;}
  if(error instanceof SyntaxError){deny(res,400);return;}
  if(e.reason===ORMErrorReason.REJECTED_BY_POLICY||e.code==='42501'){deny(res,403);return;}
  if(e.reason===ORMErrorReason.NOT_FOUND){deny(res,404);return;}
  if(e.code==='23505'){deny(res,409);return;}
  console.error(error);deny(res,500);
 }
}).listen(port,'127.0.0.1',()=>console.log(`ZenStack POC listening on 127.0.0.1:${port}`));
