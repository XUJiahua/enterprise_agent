"""Human-authored evaluator controls. Never exported to model candidates."""

BODY = r'''
	if r.Method == "POST" && r.URL.Path == "/purchases/bulk-approve" {
		__REJECT_ALL__
		var input struct { IDs []string `json:"ids"` }
		decoder := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 65536))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&input); err != nil { return nil, 400, nil }
		if decoder.Decode(new(any)) != io.EOF { return nil, 400, nil }
		if len(input.IDs) < 1 || len(input.IDs) > 100 { return nil, 400, nil }
		seen := map[string]bool{}
		for _, id := range input.IDs {
			if len(id)==0 || len(id)>100 || seen[id] { return nil, 400, nil }
			seen[id]=true
		}
		if u.Role!="approver" { return nil, 403, nil }
		rows, err := tx.Query(ctx, `SELECT id FROM "Purchase" WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted' ORDER BY id FOR UPDATE`, input.IDs,u.Tenant,u.User)
		if err!=nil {return nil,0,err}
		eligible:=[]string{}
		for rows.Next(){var id string;if err=rows.Scan(&id);err!=nil {rows.Close();return nil,0,err};eligible=append(eligible,id)}
		err=rows.Err();rows.Close();if err!=nil{return nil,0,err}
		if len(eligible)==0 {return nil,403,nil}
		__COMPLETE_BEFORE__
		updated,err:=tx.Query(ctx,`UPDATE "Purchase" SET status='approved' WHERE id=ANY($1::text[]) AND "tenantId"=$2 AND "createdBy"<>$3 AND status='submitted' RETURNING id`,eligible,u.Tenant,u.User)
		if err!=nil{return nil,0,err}
		approved:=[]string{}
		for updated.Next(){var id string;if err=updated.Scan(&id);err!=nil{updated.Close();return nil,0,err};approved=append(approved,id)}
		err=updated.Err();updated.Close();if err!=nil{return nil,0,err}
		__COMPLETE_AFTER__
		sort.Strings(approved)
		return map[string]any{"approvedIds":approved},200,nil
	}
'''


def apply(candidate, mode):
    if mode not in ('correct-reference', 'eligible-subset', 'reject-all'):
        raise ValueError('unknown manual control')
    path = candidate / 'internal/app/server.go'
    source = path.read_text()
    if mode == 'reject-all':
        body = '\n\tif r.Method == "POST" && r.URL.Path == "/purchases/bulk-approve" { return nil,403,nil }\n'
    else:
        body = BODY.replace('__REJECT_ALL__', '')
        for marker, variable in [('__COMPLETE_BEFORE__', 'eligible'), ('__COMPLETE_AFTER__', 'approved')]:
            body = body.replace(marker, 'if len(' + variable + ')!=len(input.IDs) {return nil,403,nil}' if mode == 'correct-reference' else '')
        source = source.replace('"strings"', '"strings"\n"sort"')
    needle = '\tctx := r.Context()\n'
    if source.count(needle) != 1:
        raise ValueError('source injection point changed')
    path.write_text(source.replace(needle, needle + body, 1))
