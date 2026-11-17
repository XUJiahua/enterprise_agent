package extcheck

// Catalog-bound operation Verifier (appctl operations). Every rule reads its
// expected semantics from the protected catalog internal/opdef: which
// operation names exist, their events or exemptions, request fingerprint
// fields and audit/history record. No rule infers semantics from verb forms
// or source shape. A construct the Verifier cannot resolve is reported as
// UNVERIFIABLE, which blocks the gate like a finding (exit 3).
//
// See docs/operation-semantics.md.

import (
	"fmt"
	"go/ast"
	"go/token"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/XUJiahua/enterprise_agent/internal/opdef"
)

// Role of an exported type of a kernel package when extension code uses it.
type Role string

const (
	RoleValue   Role = "value"   // request/result value: literals allowed
	RoleCatalog Role = "catalog" // catalog type: literals forbidden (DECLARATION_LITERAL)
	RoleLegacy  Role = "legacy"  // v6 declaration type: bound field by field to the catalog
)

// Kernels lists, per kernel package, every exported type and its role. A type
// a checkout's kernel package defines that is not listed, or a literal of an
// unlisted type, is UNVERIFIABLE: the kernel moved past this Verifier.
type Kernels map[string]map[string]Role

type OpConfig struct {
	Editable []string
	Kernels  Kernels
	Catalog  string // catalog package dir, compared byte-for-byte with the Verifier's own
	Ledger   string // package providing Fingerprint(domain, ids) for hand-written code
}

var DefaultOps = OpConfig{
	Editable: []string{"internal/candidate/"},
	Catalog:  "internal/opdef",
	Ledger:   "internal/ledger",
	Kernels: Kernels{
		"internal/opdef": {"Operation": RoleCatalog, "Tier": RoleCatalog, "Name": RoleCatalog, "Kind": RoleCatalog,
			"Subject": RoleCatalog, "Record": RoleCatalog, "Separation": RoleCatalog},
		"internal/opkernel": {"Request": RoleValue, "Rejection": RoleValue, "Executor": RoleValue},
		// The v6 kernel, for retrospective checks of archived candidates.
		"internal/action": {"Operation": RoleLegacy, "Update": RoleLegacy, "Request": RoleValue, "Rejection": RoleValue, "Executor": RoleValue},
	},
}

// legacyFields maps v6 declaration fields to catalog attributes.
var legacyFields = map[string]map[string]string{
	"Operation": {"Name": "name", "Role": "role", "Own": "subject", "From": "from", "To": "to", "Field": "response",
		"Event": "event", "Over": "tier.over", "OverTo": "tier.to", "Separate": "separation", "Confirm": "confirm"},
	"Update": {"Name": "name", "Role": "role", "Own": "subject", "Status": "status", "Column": "column", "Field": "response", "Event": "event"},
}

// legacyNoEvent is the v6 kernel's explicit "no event" sentinel (action.NoEvent).
const legacyNoEvent = "none"

type OpReport struct {
	Catalog  string    `json:"catalog"`
	Entries  []string  `json:"entries"`
	Verdict  string    `json:"verdict"` // pass | fail | unverifiable
	Findings []Finding `json:"findings"`
}

// Exit maps the verdict to the appctl exit code.
func (r OpReport) Exit() int {
	return map[string]int{"pass": 0, "fail": 1, "unverifiable": 3}[r.Verdict]
}

// CheckOperations checks root's working tree against the trusted commit base
// and the catalog compiled into this binary.
func CheckOperations(root, base string, cfg OpConfig) (OpReport, error) {
	rep := OpReport{Catalog: opdef.Digest(), Findings: []Finding{}}
	for _, o := range opdef.All() {
		rep.Entries = append(rep.Entries, string(o.Name))
	}
	if err := opdef.Validate(); err != nil {
		return rep, err
	}
	if _, err := git(root, "rev-parse", "--verify", base+"^{commit}"); err != nil {
		return rep, err
	}
	changed, err := git(root, "diff", "--name-only", "--no-renames", base, "--")
	if err != nil {
		return rep, err
	}
	untracked, err := git(root, "ls-files", "--others", "--exclude-standard")
	if err != nil {
		return rep, err
	}
	var parse []Finding
	cur := working(root, &parse)
	for _, f := range parse {
		f.Rule, f.Code = "unverifiable", "UNVERIFIABLE"
		rep.Findings = append(rep.Findings, f)
	}
	paths := map[string]bool{}
	for _, p := range strings.Fields(string(changed) + "\n" + string(untracked)) {
		paths[p] = true
	}
	for _, p := range sortedKeys(paths) {
		if !anyPrefix(cfg.Editable, p) {
			rep.Findings = append(rep.Findings, Finding{"extension-scope", "PROTECTED_PATH_CHANGED", p, 0, "only " + strings.Join(cfg.Editable, ", ") + " may change"})
		}
	}
	c := &opchecker{cfg: cfg, cur: cur, r: newResolver(cur)}
	c.catalogFiles(root)
	c.kernelTypes()
	for _, p := range sortedKeys(cur.files) {
		if anyPrefix(cfg.Editable, p) {
			c.extension(cur.files[p])
		}
	}
	c.manual()
	rep.Findings = append(rep.Findings, c.findings...)
	rep.Verdict = "pass"
	for _, f := range rep.Findings {
		if f.Code == "UNVERIFIABLE" {
			rep.Verdict = "unverifiable"
			break
		}
		rep.Verdict = "fail"
	}
	return rep, nil
}

func anyPrefix(prefixes []string, p string) bool {
	for _, e := range prefixes {
		if strings.HasPrefix(p, e) {
			return true
		}
	}
	return false
}

type opchecker struct {
	cfg      OpConfig
	cur      *tree
	r        *resolver
	findings []Finding
}

func (c *opchecker) add(rule, code string, f *file, n ast.Node, format string, a ...any) {
	p, line := "", 0
	if f != nil {
		p = f.path
		if n != nil {
			line = f.fset.Position(n.Pos()).Line
		}
	}
	c.findings = append(c.findings, Finding{rule, code, p, line, fmt.Sprintf(format, a...)})
}

func (c *opchecker) unverifiable(f *file, n ast.Node, format string, a ...any) {
	c.add("unverifiable", "UNVERIFIABLE", f, n, format, a...)
}

// catalogFiles: a checkout carrying the catalog package must carry exactly
// the catalog this Verifier is bound to.
func (c *opchecker) catalogFiles(root string) {
	want := opdef.SourceFiles()
	have := map[string]bool{}
	for p := range c.cur.files {
		if path.Dir(p) == c.cfg.Catalog {
			have[path.Base(p)] = true
		}
	}
	if len(have) == 0 {
		return // pre-catalog checkout (v6): the Verifier's catalog is the reference
	}
	for _, name := range sortedKeys(want) {
		src, err := os.ReadFile(filepath.Join(root, c.cfg.Catalog, name))
		if err != nil || string(src) != string(want[name]) {
			c.add("catalog", "CATALOG_DRIFT", nil, nil, "%s/%s differs from the reviewed catalog %s", c.cfg.Catalog, name, opdef.Digest())
		}
		delete(have, name)
	}
	for _, name := range sortedKeys(have) {
		c.add("catalog", "CATALOG_DRIFT", nil, nil, "%s/%s is not part of the reviewed catalog", c.cfg.Catalog, name)
	}
}

// kernelTypes: every exported type of a known kernel package must be classified.
func (c *opchecker) kernelTypes() {
	for dir, roles := range c.cfg.Kernels {
		for _, f := range c.cur.pkg(dir) {
			for _, d := range f.ast.Decls {
				g, ok := d.(*ast.GenDecl)
				if !ok || g.Tok != token.TYPE {
					continue
				}
				for _, s := range g.Specs {
					ts := s.(*ast.TypeSpec)
					if ts.Name.IsExported() && roles[ts.Name.Name] == "" {
						c.unverifiable(f, ts, "kernel type %s.%s is unknown to this Verifier", path.Base(dir), ts.Name.Name)
					}
				}
			}
		}
	}
}

// kernelRef resolves pkg.Type in f to (kernel dir, type) when pkg is a kernel import.
func (c *opchecker) kernelRef(f *file, e ast.Expr) (string, string, bool) {
	sel, ok := e.(*ast.SelectorExpr)
	if !ok {
		return "", "", false
	}
	id, ok := sel.X.(*ast.Ident)
	if !ok {
		return "", "", false
	}
	dir := c.r.importDir(f, id.Name)
	if _, known := c.cfg.Kernels[dir]; !known {
		return "", "", false
	}
	return dir, sel.Sel.Name, true
}

func inCatalog(name string) bool {
	_, ok := opdef.Lookup(opdef.Name(name))
	return ok
}

// extension applies the reference rules to one editable file.
func (c *opchecker) extension(f *file) {
	ast.Inspect(f.ast, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.BasicLit:
			if v, err := strconv.Unquote(x.Value); err == nil && x.Kind == token.STRING && versioned.MatchString(v) && !inCatalog(v) {
				c.add("catalog", "OPERATION_NOT_IN_CATALOG", f, x, "operation %q is not in the catalog", v)
			}
		case *ast.CompositeLit:
			dir, typ, ok := c.kernelRef(f, x.Type)
			if !ok {
				return true
			}
			switch c.cfg.Kernels[dir][typ] {
			case RoleValue:
			case RoleCatalog:
				c.add("catalog", "DECLARATION_LITERAL", f, x, "extension code may not construct %s.%s; reference a catalog entry by name", path.Base(dir), typ)
				for _, el := range x.Elts {
					if kv, ok := el.(*ast.KeyValueExpr); ok {
						if k, ok := kv.Key.(*ast.Ident); ok && k.Name == "EventExemption" {
							c.add("catalog-event", "EVENT_EXEMPTION_NOT_IN_CATALOG", f, kv, "event exemptions exist only in the reviewed catalog")
						}
					}
				}
				return false
			case RoleLegacy:
				c.legacy(f, x, dir, typ)
				return false
			default:
				c.unverifiable(f, x, "literal of unknown kernel type %s.%s", path.Base(dir), typ)
				return false
			}
		case *ast.CallExpr:
			// opdef.Name(x) is the only way to make a Name from a non-constant
			// string, so it must convert a constant. Other Name values come from
			// catalog constants or checked literals; Lookup of one needs no check.
			dir, fn, ok := c.kernelRef(f, x.Fun)
			if ok && dir == c.cfg.Catalog && fn == "Name" && len(x.Args) == 1 {
				if _, constant := c.r.value(f, x.Args[0]); !constant {
					c.unverifiable(f, x, "%s.%s argument is not a constant", path.Base(dir), fn)
				} // a constant comes from a string literal, checked above
			}
		}
		return true
	})
}

// fieldOrder reads a kernel struct's field names from the checkout.
func (c *opchecker) fieldOrder(dir, typ string) []string {
	var out []string
	for _, f := range c.cur.pkg(dir) {
		ast.Inspect(f.ast, func(n ast.Node) bool {
			if ts, ok := n.(*ast.TypeSpec); ok && ts.Name.Name == typ {
				if st, ok := ts.Type.(*ast.StructType); ok {
					for _, fl := range st.Fields.List {
						for _, nm := range fl.Names {
							out = append(out, nm.Name)
						}
					}
				}
			}
			return true
		})
	}
	return out
}

// constant resolves string, bool and int constants.
func (c *opchecker) constant(f *file, e ast.Expr) (any, bool) {
	if v, ok := c.r.value(f, e); ok {
		return v, true
	}
	switch x := e.(type) {
	case *ast.Ident:
		if x.Name == "true" || x.Name == "false" {
			return x.Name == "true", true
		}
	case *ast.BasicLit:
		if x.Kind == token.INT {
			v, err := strconv.Atoi(strings.ReplaceAll(x.Value, "_", ""))
			return v, err == nil
		}
	}
	return nil, false
}

// legacy binds a v6 kernel declaration to its catalog entry.
func (c *opchecker) legacy(f *file, lit *ast.CompositeLit, dir, typ string) {
	order := c.fieldOrder(dir, typ)
	mapping := legacyFields[typ]
	values := map[string]any{}
	for i, el := range lit.Elts {
		key, val := "", el
		if kv, ok := el.(*ast.KeyValueExpr); ok {
			if id, ok := kv.Key.(*ast.Ident); ok {
				key, val = id.Name, kv.Value
			}
		} else if i < len(order) {
			key = order[i]
		}
		if mapping[key] == "" {
			c.unverifiable(f, el, "%s.%s field %q has no catalog mapping", path.Base(dir), typ, key)
			return
		}
		v, ok := c.constant(f, val)
		if !ok {
			c.unverifiable(f, el, "%s.%s field %s is not a constant", path.Base(dir), typ, key)
			return
		}
		values[key] = v
	}
	name, _ := values["Name"].(string)
	if name == "" {
		c.unverifiable(f, lit, "%s.%s without a constant Name", path.Base(dir), typ)
		return
	}
	op, ok := opdef.Lookup(opdef.Name(name))
	if !ok {
		c.add("catalog", "OPERATION_NOT_IN_CATALOG", f, lit, "operation %q is not in the catalog", name)
		return
	}
	if (typ == "Update") != (op.Kind == opdef.FieldUpdate) {
		c.add("catalog", "DECLARATION_MISMATCH", f, lit, "%s is a %s in the catalog, declared as %s.%s", name, op.Kind, path.Base(dir), typ)
		return
	}
	// Compare only the attributes this kernel type can express.
	str := func(k string) string { s, _ := values[k].(string); return s }
	boolean := func(k string) bool { b, _ := values[k].(bool); return b }
	integer := func(k string) int { n, _ := values[k].(int); return n }
	tierOver, tierTo := 0, ""
	if op.Tier != nil {
		tierOver, tierTo = op.Tier.Over, op.Tier.To
	}
	want := map[string]any{"Role": op.Role, "Own": op.Subject == opdef.Own, "From": op.From, "To": op.To, "Field": op.Response,
		"Over": tierOver, "OverTo": tierTo, "Separate": op.Separation == opdef.DistinctFromPreviousApprover,
		"Confirm": op.ConfirmAmount, "Status": op.Status, "Column": op.Column}
	got := map[string]any{"Role": str("Role"), "Own": boolean("Own"), "From": str("From"), "To": str("To"), "Field": str("Field"),
		"Over": integer("Over"), "OverTo": str("OverTo"), "Separate": boolean("Separate"), "Confirm": boolean("Confirm"),
		"Status": str("Status"), "Column": str("Column")}
	for _, k := range order {
		if k == "Name" || k == "Event" || mapping[k] == "" {
			continue
		}
		if got[k] != want[k] {
			c.add("catalog", "DECLARATION_MISMATCH", f, lit, "%s.%s = %v, catalog %s has %v", name, k, got[k], mapping[k], want[k])
		}
	}
	event := str("Event")
	switch {
	case op.EventExemption != "" && event == legacyNoEvent:
	case op.EventExemption == "" && event == legacyNoEvent:
		c.add("catalog-event", "EVENT_EXEMPTION_NOT_IN_CATALOG", f, lit, "%s declares no event; the catalog requires %q and grants no exemption", name, op.Event)
	case event != op.Event || (op.EventExemption != "" && event != legacyNoEvent):
		expected := op.Event
		if op.EventExemption != "" {
			expected = "exempt (" + op.EventExemption + ")"
		}
		c.add("catalog-event", "EVENT_MISMATCH", f, lit, "%s declares event %q, catalog: %s", name, event, expected)
	}
}

var insertColumns = regexp.MustCompile(`(?s)\(([^()]*)\)\s*VALUES\s*\(([^()]*)\)`)

// requestHash returns the expression bound to the "requestHash" column of the
// ActionRequest insert, following one single-assignment local, or nil.
func requestHash(w writer, locals map[string]ast.Expr) ast.Expr {
	for i, a := range w.call.Args {
		lit, ok := a.(*ast.BasicLit)
		if !ok || !strings.Contains(lit.Value, `"ActionRequest"`) {
			continue
		}
		m := insertColumns.FindStringSubmatch(lit.Value)
		if m == nil {
			return nil
		}
		cols, vals := strings.Split(m[1], ","), strings.Split(m[2], ",")
		for j, col := range cols {
			if strings.Contains(col, "requestHash") && j < len(vals) {
				n, err := strconv.Atoi(strings.TrimPrefix(strings.TrimSpace(vals[j]), "$"))
				if err != nil || i+n >= len(w.call.Args) {
					return nil
				}
				e := w.call.Args[i+n]
				if id, ok := e.(*ast.Ident); ok && locals[id.Name] != nil {
					return locals[id.Name]
				}
				return e
			}
		}
	}
	return nil
}

// writer is one hand-written ActionRequest insert.
type writer struct {
	f    *file
	fn   fnode
	call *ast.CallExpr
	op   opdef.Operation
}

// manual applies the catalog to hand-written operation code in editable packages.
func (c *opchecker) manual() {
	for _, dir := range editableDirs(c.cur, Config{Editable: c.cfg.Editable}) {
		files := c.cur.pkg(dir)
		var writers []writer
		for _, f := range files {
			for _, fn := range innermostFuncs(f.ast) {
				for _, call := range directSQLCalls(fn, `INSERT INTO "ActionRequest"`) {
					names := map[string]bool{}
					for _, a := range call.Args {
						if v, ok := c.r.value(f, a); ok && versioned.MatchString(v) {
							names[v] = true
						}
					}
					if len(names) != 1 {
						c.unverifiable(f, call, "ActionRequest write without exactly one constant operation name")
						continue
					}
					name := sortedKeys(names)[0]
					op, ok := opdef.Lookup(opdef.Name(name))
					if !ok {
						continue // OPERATION_NOT_IN_CATALOG already reported
					}
					writers = append(writers, writer{f, fn, call, op})
				}
			}
		}
		if len(writers) == 0 {
			continue
		}
		c.events(files, writers)
		c.records(files, writers)
		for _, w := range writers {
			c.fingerprint(files, w)
		}
	}
}

// emitted collects event types written to PurchaseEvent in files.
func (c *opchecker) emitted(files []*file) map[string]bool {
	out := map[string]bool{}
	for _, f := range files {
		for _, call := range sqlCalls(f.ast, `"PurchaseEvent"`) {
			found := false
			for _, a := range call.Args {
				if lit, ok := a.(*ast.BasicLit); ok && strings.Contains(lit.Value, `"PurchaseEvent"`) {
					for _, m := range sqlLiteral.FindAllStringSubmatch(lit.Value, -1) {
						out[m[1]], found = true, true
					}
				} else if v, ok := c.r.value(f, a); ok && eventName.MatchString(v) && !versioned.MatchString(v) {
					out[v], found = true, true
				}
			}
			if !found && !strings.Contains(render(f.fset, call), "SELECT") {
				c.unverifiable(f, call, "PurchaseEvent write whose event type is not a constant")
			}
		}
	}
	return out
}

func (c *opchecker) events(files []*file, writers []writer) {
	emitted := c.emitted(files)
	catalogEvents := map[string]bool{}
	for _, o := range opdef.All() {
		if o.Event != "" {
			catalogEvents[o.Event] = true
		}
	}
	for _, e := range sortedKeys(emitted) {
		if !catalogEvents[e] {
			c.add("catalog-event", "EVENT_NOT_IN_CATALOG", files[0], nil, "hand-written event %q is not a catalog event", e)
		}
	}
	done := map[opdef.Name]bool{}
	for _, w := range writers {
		if w.op.Event != "" && !emitted[w.op.Event] && !done[w.op.Name] {
			done[w.op.Name] = true
			c.add("catalog-event", "EVENT_NOT_WRITTEN", w.f, w.call, "%s writes no %q event (catalog: event required, no exemption)", w.op.Name, w.op.Event)
		}
	}
}

func (c *opchecker) records(files []*file, writers []writer) {
	done := map[opdef.Name]bool{}
	for _, w := range writers {
		fragment := `INSERT INTO "` + string(w.op.Record) + `"`
		ok := len(directSQLCalls(w.fn, fragment)) > 0
		for _, f := range files {
			for _, call := range sqlCalls(f.ast, fragment) {
				for _, a := range call.Args {
					if v, constant := c.r.value(f, a); constant && v == string(w.op.Name) {
						ok = true
					}
				}
			}
		}
		if !ok && !done[w.op.Name] {
			done[w.op.Name] = true
			c.add("catalog-record", "RECORD_NOT_WRITTEN", w.f, w.call, "%s writes no %s row (catalog record)", w.op.Name, w.op.Record)
		}
	}
}

// params returns a handler's parameter names after ctx and identity:
// [key, inputs...] when fn has the protocol handler shape for op.
func params(fn fnode, op opdef.Operation) ([]string, bool) {
	var names []string
	for _, p := range fn.params.List {
		if len(p.Names) == 0 {
			names = append(names, "_")
		}
		for _, n := range p.Names {
			names = append(names, n.Name)
		}
	}
	var results *ast.FieldList
	switch x := fn.node.(type) {
	case *ast.FuncLit:
		results = x.Type.Results
	case *ast.FuncDecl:
		results = x.Type.Results
	}
	if results == nil || results.NumFields() != 3 || len(names) != 3+len(op.Inputs) {
		return nil, false
	}
	return names[2:], true
}

// handlerNames maps request field -> parameter name of the writing function
// for w: directly when it has the protocol handler shape, else through the
// calls handler-shaped functions make to it (all must agree).
func (c *opchecker) handlerNames(files []*file, w writer) (map[string]string, bool) {
	build := func(names []string) map[string]string {
		m := map[string]string{"key": names[0]}
		for i, in := range w.op.Inputs {
			m[in] = names[1+i]
		}
		return m
	}
	if names, ok := params(w.fn, w.op); ok {
		return build(names), true
	}
	decl, ok := w.fn.node.(*ast.FuncDecl)
	if !ok || decl.Recv != nil {
		return nil, false
	}
	var own []string
	for _, p := range decl.Type.Params.List {
		for _, n := range p.Names {
			own = append(own, n.Name)
		}
	}
	var found map[string]string
	conflict := false
	for _, f := range files {
		for _, fn := range innermostFuncs(f.ast) {
			names, ok := params(fn, w.op)
			if !ok {
				continue
			}
			outer := build(names)
			field := map[string]string{} // handler param name -> request field
			for k, v := range outer {
				field[v] = k
			}
			ast.Inspect(fn.body, func(n ast.Node) bool {
				call, isCall := n.(*ast.CallExpr)
				if !isCall {
					return true
				}
				if id, isIdent := call.Fun.(*ast.Ident); !isIdent || id.Name != decl.Name.Name || len(call.Args) != len(own) {
					return true
				}
				m := map[string]string{}
				for i, a := range call.Args {
					if id, ok := a.(*ast.Ident); ok && field[id.Name] != "" {
						m[field[id.Name]] = own[i]
					}
				}
				if len(m) != len(outer) || (found != nil && fmt.Sprint(found) != fmt.Sprint(m)) {
					conflict = true
				}
				found = m
				return true
			})
		}
	}
	return found, found != nil && !conflict
}

// fingerprint: the requestHash of an operation's write must be
// ledger.Fingerprint(name + "\n" + value..., targets) with exactly the
// catalog Fingerprint fields, in catalog order.
func (c *opchecker) fingerprint(files []*file, w writer) {
	names, ok := c.handlerNames(files, w)
	if !ok {
		c.unverifiable(w.f, w.call, "%s: cannot map handler parameters to catalog inputs %v", w.op.Name, w.op.Inputs)
		return
	}
	helpers := map[string]*ast.FuncDecl{}
	for _, f := range files {
		for _, d := range f.ast.Decls {
			if fd, ok := d.(*ast.FuncDecl); ok && fd.Recv == nil {
				helpers[fd.Name.Name] = fd
			}
		}
	}
	locals := assignments(w.fn.body)
	var hashes []*ast.CallExpr
	ast.Inspect(w.fn.body, func(n ast.Node) bool {
		if _, nested := n.(*ast.FuncLit); nested {
			return false
		}
		if call, ok := n.(*ast.CallExpr); ok && !hasSQL(call) {
			if c.isLedger(w.f, call) {
				hashes = append(hashes, call)
			} else if id, ok := call.Fun.(*ast.Ident); ok && helpers[id.Name] != nil {
				if inner := c.inline(helpers[id.Name], call); inner != nil {
					hashes = append(hashes, inner)
				}
			}
		}
		return true
	})
	opArg := map[string]bool{}
	for _, a := range w.call.Args {
		if v, ok := c.r.value(w.f, a); ok && v == string(w.op.Name) {
			opArg[render(w.f.fset, a)] = true
		}
	}
	var bound []*ast.CallExpr
	for _, h := range hashes {
		if len(h.Args) == 2 {
			terms := flatten(h.Args[0], locals)
			if len(terms) > 0 && (opArg[render(w.f.fset, terms[0])] || c.isName(w.f, terms[0], w.op.Name)) {
				bound = append(bound, h)
			}
		}
	}
	if len(bound) == 0 {
		if len(hashes) == 0 {
			// A hand-rolled digest cannot be proven equal to the catalog
			// fingerprint, but a catalog-excluded input reaching it is a violation.
			if h := requestHash(w, locals); h != nil {
				for _, in := range append([]string{"key"}, w.op.Inputs...) {
					if !slices.Contains(w.op.Fingerprint, in) && hasIdent(h, names[in]) {
						c.add("catalog-fingerprint", "FINGERPRINT_EXTRA_INPUT", w.f, h, "%s: requestHash %s includes %s, which the catalog excludes", w.op.Name, render(w.f.fset, h), in)
						return
					}
				}
			}
			c.unverifiable(w.f, w.call, "%s: requestHash is not computed through %s.Fingerprint in the writing function", w.op.Name, path.Base(c.cfg.Ledger))
			return
		}
		c.add("catalog-fingerprint", "FINGERPRINT_OPERATION_MISSING", w.f, w.call, "%s: no %s.Fingerprint call is bound to this operation name", w.op.Name, path.Base(c.cfg.Ledger))
		return
	}
	forbidden := map[string]string{names["key"]: "Idempotency-Key"}
	for _, in := range w.op.Inputs {
		if !slices.Contains(w.op.Fingerprint, in) {
			forbidden[names[in]] = in
		}
	}
	for _, h := range bound {
		terms := flatten(h.Args[0], locals)
		// Expected domain: name, then "\n", <field> for each non-target fingerprint field.
		var got []string
		for _, t := range terms[1:] {
			if v, ok := c.r.value(w.f, t); ok {
				got = append(got, strconv.Quote(v))
			} else if id, ok := t.(*ast.Ident); ok {
				got = append(got, id.Name)
			} else {
				c.unverifiable(w.f, h, "%s: fingerprint domain term %s is not resolvable", w.op.Name, render(w.f.fset, t))
				return
			}
		}
		var want []string
		for _, fld := range w.op.Fingerprint[1:] {
			want = append(want, strconv.Quote("\n"), names[fld])
		}
		bad := false
		for _, a := range h.Args {
			ast.Inspect(a, func(n ast.Node) bool {
				if id, ok := n.(*ast.Ident); ok && forbidden[id.Name] != "" {
					c.add("catalog-fingerprint", "FINGERPRINT_EXTRA_INPUT", w.f, h, "%s: requestHash includes %s (%s), which the catalog excludes", w.op.Name, id.Name, forbidden[id.Name])
					forbidden[id.Name], bad = "", true
				}
				return true
			})
		}
		if bad {
			continue
		}
		if !slices.Equal(got, want) {
			missing := []string{}
			for _, fld := range w.op.Fingerprint[1:] {
				if !hasIdent(h, names[fld]) {
					missing = append(missing, fld)
				}
			}
			if len(missing) > 0 {
				c.add("catalog-fingerprint", "FINGERPRINT_INPUT_MISSING", w.f, h, "%s: requestHash omits catalog fingerprint field(s) %v", w.op.Name, missing)
			} else {
				c.add("catalog-fingerprint", "FINGERPRINT_SHAPE", w.f, h, "%s: domain is %v, catalog requires name followed by %v", w.op.Name, got, want)
			}
			continue
		}
		target := names[opdef.IDs]
		if !hasIdent(h.Args[1], target) {
			c.add("catalog-fingerprint", "FINGERPRINT_SHAPE", w.f, h, "%s: digested IDs do not use the request targets %s", w.op.Name, target)
		}
		for _, in := range w.op.Inputs[1:] {
			if hasIdent(h.Args[1], names[in]) {
				c.add("catalog-fingerprint", "FINGERPRINT_SHAPE", w.f, h, "%s: %s is mixed into the digested IDs", w.op.Name, in)
			}
		}
	}
}

func (c *opchecker) isLedger(f *file, call *ast.CallExpr) bool {
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok || sel.Sel.Name != "Fingerprint" {
		return false
	}
	id, ok := sel.X.(*ast.Ident)
	return ok && c.r.importDir(f, id.Name) == c.cfg.Ledger
}

func (c *opchecker) isName(f *file, e ast.Expr, name opdef.Name) bool {
	v, ok := c.r.value(f, e)
	return ok && v == string(name)
}

// inline returns helper's ledger Fingerprint call with the helper's parameters
// replaced by call's arguments (one level), or nil.
func (c *opchecker) inline(helper *ast.FuncDecl, call *ast.CallExpr) *ast.CallExpr {
	var params []string
	for _, p := range helper.Type.Params.List {
		for _, n := range p.Names {
			params = append(params, n.Name)
		}
	}
	if len(params) != len(call.Args) {
		return nil
	}
	sub := map[string]ast.Expr{}
	for i, p := range params {
		sub[p] = call.Args[i]
	}
	var found *ast.CallExpr
	ast.Inspect(helper.Body, func(n ast.Node) bool {
		if x, ok := n.(*ast.CallExpr); ok && found == nil {
			if sel, ok := x.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Fingerprint" && len(x.Args) == 2 {
				found = &ast.CallExpr{Fun: x.Fun, Args: []ast.Expr{substitute(x.Args[0], sub), substitute(x.Args[1], sub)}}
			}
		}
		return found == nil
	})
	return found
}

// substitute replaces bare identifiers (shallow, for '+' chains and idents).
func substitute(e ast.Expr, sub map[string]ast.Expr) ast.Expr {
	switch x := e.(type) {
	case *ast.Ident:
		if r, ok := sub[x.Name]; ok {
			return r
		}
	case *ast.BinaryExpr:
		return &ast.BinaryExpr{X: substitute(x.X, sub), Op: x.Op, Y: substitute(x.Y, sub)}
	case *ast.ParenExpr:
		return substitute(x.X, sub)
	case *ast.CompositeLit:
		elts := make([]ast.Expr, len(x.Elts))
		for i, el := range x.Elts {
			elts[i] = substitute(el, sub)
		}
		return &ast.CompositeLit{Type: x.Type, Elts: elts}
	case *ast.CallExpr:
		args := make([]ast.Expr, len(x.Args))
		for i, a := range x.Args {
			args[i] = substitute(a, sub)
		}
		return &ast.CallExpr{Fun: x.Fun, Args: args, Ellipsis: x.Ellipsis}
	}
	return e
}

// assignments: local identifiers assigned exactly once in body (x := expr).
func assignments(body *ast.BlockStmt) map[string]ast.Expr {
	out, count := map[string]ast.Expr{}, map[string]int{}
	ast.Inspect(body, func(n ast.Node) bool {
		if _, nested := n.(*ast.FuncLit); nested {
			return false
		}
		if a, ok := n.(*ast.AssignStmt); ok && len(a.Lhs) == len(a.Rhs) {
			for i, l := range a.Lhs {
				if id, ok := l.(*ast.Ident); ok {
					count[id.Name]++
					out[id.Name] = a.Rhs[i]
				}
			}
		}
		return true
	})
	for k, n := range count {
		if n != 1 {
			delete(out, k)
		}
	}
	return out
}

// flatten expands a '+' concatenation into terms, following single-assignment locals.
func flatten(e ast.Expr, locals map[string]ast.Expr) []ast.Expr {
	switch x := e.(type) {
	case *ast.BinaryExpr:
		if x.Op == token.ADD {
			return append(flatten(x.X, locals), flatten(x.Y, locals)...)
		}
	case *ast.ParenExpr:
		return flatten(x.X, locals)
	case *ast.Ident:
		if r, ok := locals[x.Name]; ok {
			delete(locals, x.Name) // no cycles
			defer func() { locals[x.Name] = r }()
			return flatten(r, locals)
		}
	}
	return []ast.Expr{e}
}

func hasIdent(n ast.Node, name string) bool {
	found := false
	ast.Inspect(n, func(n ast.Node) bool {
		if id, ok := n.(*ast.Ident); ok && id.Name == name {
			found = true
		}
		return !found
	})
	return found
}

// RuleCodes lists the rule of every code, for reports.
var RuleCodes = func() map[string]string {
	m := map[string]string{}
	for rule, codes := range map[string][]string{
		"extension-scope":     {"PROTECTED_PATH_CHANGED"},
		"catalog":             {"CATALOG_DRIFT", "OPERATION_NOT_IN_CATALOG", "DECLARATION_LITERAL", "DECLARATION_MISMATCH"},
		"catalog-event":       {"EVENT_EXEMPTION_NOT_IN_CATALOG", "EVENT_MISMATCH", "EVENT_NOT_WRITTEN", "EVENT_NOT_IN_CATALOG"},
		"catalog-fingerprint": {"FINGERPRINT_OPERATION_MISSING", "FINGERPRINT_EXTRA_INPUT", "FINGERPRINT_INPUT_MISSING", "FINGERPRINT_SHAPE"},
		"catalog-record":      {"RECORD_NOT_WRITTEN"},
		"unverifiable":        {"UNVERIFIABLE"},
	} {
		for _, code := range codes {
			m[code] = rule
		}
	}
	return m
}()
