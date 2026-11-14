"""Task-specific HTTP assertions. This file runs from the trusted harness checkout."""
import json
import urllib.error
import urllib.parse
import urllib.request


def request(path,user='alice'):
    headers={} if user is None else {'Authorization':'Bearer demo_'+user}
    try:
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080'+path,headers=headers),timeout=10) as response:
            return response.status,json.load(response)
    except urllib.error.HTTPError as error:
        return error.code,json.load(error)


def check(task):
    results=[]
    def case(name,fn):
        try:fn();results.append({'case':name,'passed':True})
        except AssertionError as error:results.append({'case':name,'passed':False,'error':str(error)})
        except Exception as error:raise RuntimeError(f'oracle transport/parsing failure in {name}: {error}') from error
    def equal(path,user,expected):
        actual=request(path,user);assert actual==(200,expected),(actual,expected)
    def ids(path,user,expected):
        status,rows=request(path,user);assert status==200,(status,rows)
        actual=sorted(row['id'] for row in rows);assert actual==sorted(expected),(actual,expected)
        assert all(row['tenantId']==('tenant-b' if user=='dave' else 'tenant-a') for row in rows)
        assert all(row.get('internalNote') is None for row in rows)
    if task=='tenant-summary':
        case('tenant-a totals',lambda:equal('/purchases/summary','alice',{'count':4,'totalAmount':800}))
        case('tenant-b totals',lambda:equal('/purchases/summary','dave',{'count':1,'totalAmount':400}))
        case('approver totals',lambda:equal('/purchases/summary','carol',{'count':4,'totalAmount':800}))
    elif task=='title-search':
        case('case insensitive',lambda:ids('/purchases?title=LAPTOP','alice',['a-own','a-other','a-done']))
        case('tenant isolation',lambda:ids('/purchases?title=laptop','dave',['b-other']))
        for title in ['%', '_', "' OR 1=1 --"]:
            case('literal '+title,lambda title=title:ids('/purchases?title='+urllib.parse.quote(title,safe=''),'alice',[]))
        case('empty search',lambda:ids('/purchases?title=','alice',['a-own','a-other','a-self','a-done']))
    elif task=='status-filter':
        case('approved records',lambda:ids('/purchases?status=approved','alice',['a-done']))
        case('submitted records',lambda:ids('/purchases?status=submitted','alice',['a-own','a-other','a-self']))
        case('tenant-b approved',lambda:ids('/purchases?status=approved','dave',[]))
        for value in ['unknown','']:
            def reject(value=value):
                result=request('/purchases?status='+value);assert result[0]==400,result
            case('invalid status '+value,reject)
    else:raise ValueError('unknown task')
    def anonymous():
        result=request('/purchases/summary' if task=='tenant-summary' else '/purchases',None)
        assert result[0]==401,result
    case('anonymous rejected',anonymous)
    return results
