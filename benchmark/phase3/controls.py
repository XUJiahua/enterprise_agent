"""Human controls, excluded from candidate exports and all model sample counts."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def apply(candidate,cell,mode):
    if mode=='unchanged': return
    if mode not in ('correct','reject-all'): raise ValueError('unknown synthetic control')
    if mode=='reject-all':
        body='return protocol.Result{},false,&protocol.Rejection{Status:403}'
        imports=''
        setup=''
    else:
        if cell=='go-manual':
            # A full human transaction reference, not an exported starting answer.
            source=(ROOT/'internal/action/approval.go').read_text()
            source=source.replace('package action','package candidate',1).replace('func New(','func newExecutor(',1)
            (candidate/'internal/candidate/reference.go').write_text(source)
            namespace=''
            imports=''
        else:
            namespace='action.'
            imports='"github.com/XUJiahua/enterprise_agent/internal/action"'
        setup='executor,err:='+namespace+'New(ctx,pool,"");if err!=nil{return nil,err}' if namespace else 'executor,err:=newExecutor(ctx,pool,"");if err!=nil{return nil,err}'
        body='''value,replayed,err:=executor.BulkApprove(ctx,u,key,ids)
 var denied *'''+namespace+'''Rejection
 if errors.As(err,&denied){err=&protocol.Rejection{Status:denied.Status}}
 return protocol.Result{ApprovedIDs:value.ApprovedIDs},replayed,err'''
    (candidate/'internal/candidate/approval.go').write_text('''package candidate
import (
 "context"
 "net/http"
 '''+('"errors"' if mode=='correct' else '')+'''
 '''+imports+'''
 "github.com/XUJiahua/enterprise_agent/internal/app"
 "github.com/XUJiahua/enterprise_agent/internal/protocol"
 "github.com/jackc/pgx/v5/pgxpool"
)
func New(ctx context.Context,pool *pgxpool.Pool,fallback http.Handler)(http.Handler,error){
 '''+setup+'''
 approve:=func(ctx context.Context,u app.Identity,key string,ids []string)(protocol.Result,bool,error){
 '''+body+'''
 }
 return protocol.HTTP(approve,fallback),nil
}
''')
