// Package extcheck statically checks a candidate checkout that extends the
// platform Action kernel. It compares the working tree with a trusted git
// baseline and reports deterministic findings; it does not build or run code.
//
// Rules (see docs/verifier-extension-rules.md):
//
//	extension-scope        only Config.Editable paths change; no kernel-typed
//	                       declaration is added outside them
//	operation-declaration  new action.Operation/Transition literals are complete,
//	                       constant, versioned and globally unique
//	event-consumer         a new declaration's event has a consumer, and a
//	                       consumer event named after the operation is not
//	                       declined with NoEvent / an empty Event
//	manual-event-consumer  (hand-written code) the same pairing for new
//	                       versioned operation constants and PurchaseEvent writes
//	manual-fingerprint     (hand-written code) each new ActionRequest write hashes
//	                       the same operation and only its targets
package extcheck

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type Finding struct {
	Rule   string `json:"rule"`
	Code   string `json:"code"`
	Path   string `json:"path"`
	Line   int    `json:"line,omitempty"`
	Detail string `json:"detail"`
}

// Kind describes one declarative kernel type: its required fields and the
// field holding the operation name.
type Kind struct {
	Name     string
	Required []string
	Event    string // "" when the type has no event field
}

type Config struct {
	Editable  []string // path prefixes candidates may change
	Kernel    string   // kernel package dir, e.g. internal/action
	Consumers string   // event consumer package dir, e.g. internal/notify
	Kinds     map[string]Kind
	NoEvent   string // kernel sentinel value for "deliberately no event"
}

var Default = Config{
	Editable:  []string{"internal/candidate/"},
	Kernel:    "internal/action",
	Consumers: "internal/notify",
	Kinds: map[string]Kind{
		"Operation":  {Name: "Name", Required: []string{"Name", "Role", "From", "To", "Event"}, Event: "Event"},
		"Transition": {Name: "Operation", Required: []string{"Operation", "From", "To", "Field"}},
	},
	NoEvent: "none",
}

var (
	versioned  = regexp.MustCompile(`^[a-z][a-z0-9-]*\.v[0-9]+$`)
	eventName  = regexp.MustCompile(`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`)
	sqlVerb    = regexp.MustCompile(`(?i)\b(SELECT|INSERT|UPDATE|DELETE)\b`)
	sqlLiteral = regexp.MustCompile(`'([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)'`)
)

// Stem maps an operation name to its verb: "bulk-reject.v4" -> "reject".
func Stem(name string) string {
	name = regexp.MustCompile(`\.v[0-9]+$`).ReplaceAllString(name, "")
	return name[strings.LastIndex(name, "-")+1:]
}

// Names reports whether event is the regular English past participle of verb
// in its last dotted segment: purchase.reopened~reopen, purchase.approved~approve,
// purchase.submitted~submit. Irregular verbs never match (a false negative).
func Names(event, verb string) bool {
	w := event[strings.LastIndex(event, ".")+1:]
	if verb == "" || !strings.HasSuffix(w, "ed") {
		return false
	}
	b := w[:len(w)-2]
	doubled := len(b) >= 2 && b[len(b)-1] == b[len(b)-2] && b[:len(b)-1] == verb
	return b == verb || w[:len(w)-1] == verb || doubled
}

type file struct {
	path string
	ast  *ast.File
	fset *token.FileSet
}

type tree struct {
	module string
	files  map[string]*file // by slash path
}

func (t *tree) pkg(dir string) []*file {
	var out []*file
	for p, f := range t.files {
		if path.Dir(p) == dir {
			out = append(out, f)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].path < out[j].path })
	return out
}

func parse(name string, src []byte) (*file, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, name, src, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	return &file{name, f, fset}, nil
}

func git(root string, args ...string) ([]byte, error) {
	out, err := exec.Command("git", append([]string{"-C", root}, args...)...).Output()
	if err != nil {
		return nil, fmt.Errorf("git %s: %w", strings.Join(args, " "), err)
	}
	return out, nil
}

func module(src []byte) string {
	for _, line := range strings.Split(string(src), "\n") {
		if m, ok := strings.CutPrefix(strings.TrimSpace(line), "module "); ok {
			return strings.TrimSpace(m)
		}
	}
	return ""
}

func production(p string) bool {
	return strings.HasSuffix(p, ".go") && !strings.HasSuffix(p, "_test.go")
}

func baseline(root, base string) (*tree, error) {
	names, err := git(root, "ls-tree", "-r", "--name-only", base)
	if err != nil {
		return nil, err
	}
	t := &tree{files: map[string]*file{}}
	if gomod, err := git(root, "show", base+":go.mod"); err == nil {
		t.module = module(gomod)
	}
	for _, p := range strings.Fields(string(names)) {
		if !production(p) {
			continue
		}
		src, err := git(root, "show", base+":"+p)
		if err != nil {
			return nil, err
		}
		if t.files[p], err = parse(p, src); err != nil {
			return nil, err
		}
	}
	return t, nil
}

func working(root string, findings *[]Finding) *tree {
	t := &tree{files: map[string]*file{}}
	if gomod, err := os.ReadFile(filepath.Join(root, "go.mod")); err == nil {
		t.module = module(gomod)
	}
	_ = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if rel != "." && (strings.HasPrefix(d.Name(), ".") || d.Name() == "vendor" || d.Name() == "testdata" || d.Name() == "node_modules") {
				return filepath.SkipDir
			}
			return nil
		}
		if !production(rel) {
			return nil
		}
		src, err := os.ReadFile(p)
		if err == nil {
			var f *file
			if f, err = parse(rel, src); err == nil {
				t.files[rel] = f
				return nil
			}
		}
		*findings = append(*findings, Finding{"operation-declaration", "PARSE_ERROR", rel, 0, err.Error()})
		return nil
	})
	return t
}

// Check runs every rule against root's working tree (tracked changes and
// untracked files) relative to the trusted commit base.
func Check(root, base string, cfg Config) ([]Finding, error) {
	if _, err := git(root, "rev-parse", "--verify", base+"^{commit}"); err != nil {
		return nil, err
	}
	findings := []Finding{}
	changed, err := git(root, "diff", "--name-only", "--no-renames", base, "--")
	if err != nil {
		return nil, err
	}
	untracked, err := git(root, "ls-files", "--others", "--exclude-standard")
	if err != nil {
		return nil, err
	}
	paths := map[string]bool{}
	for _, p := range strings.Fields(string(changed) + "\n" + string(untracked)) {
		paths[p] = true
	}
	for _, p := range sortedKeys(paths) {
		if !editable(cfg, p) {
			findings = append(findings, Finding{"extension-scope", "PROTECTED_PATH_CHANGED", p, 0,
				"only " + strings.Join(cfg.Editable, ", ") + " may change"})
		}
	}
	old, err := baseline(root, base)
	if err != nil {
		return nil, err
	}
	cur := working(root, &findings)
	return append(findings, analyse(old, cur, cfg)...), nil
}

func editable(cfg Config, p string) bool {
	for _, e := range cfg.Editable {
		if strings.HasPrefix(p, e) {
			return true
		}
	}
	return false
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// resolver turns constant expressions into strings using package-level consts.
type resolver struct {
	t      *tree
	consts map[string]map[string]string // pkg dir -> name -> value
}

func newResolver(t *tree) *resolver {
	r := &resolver{t: t, consts: map[string]map[string]string{}}
	for p, f := range t.files {
		dir := path.Dir(p)
		if r.consts[dir] == nil {
			r.consts[dir] = map[string]string{}
		}
		for _, d := range f.ast.Decls {
			g, ok := d.(*ast.GenDecl)
			if !ok || g.Tok != token.CONST {
				continue
			}
			for _, s := range g.Specs {
				v := s.(*ast.ValueSpec)
				for i, n := range v.Names {
					if i < len(v.Values) {
						if lit, ok := v.Values[i].(*ast.BasicLit); ok && lit.Kind == token.STRING {
							r.consts[dir][n.Name], _ = strconv.Unquote(lit.Value)
						}
					}
				}
			}
		}
	}
	return r
}

// importDir maps an import alias used in f to a module-relative package dir.
func (r *resolver) importDir(f *file, alias string) string {
	for _, s := range f.ast.Imports {
		p, _ := strconv.Unquote(s.Path.Value)
		name := path.Base(p)
		if s.Name != nil {
			name = s.Name.Name
		}
		if name == alias && r.t.module != "" {
			if rel, ok := strings.CutPrefix(p, r.t.module+"/"); ok {
				return rel
			}
		}
	}
	return ""
}

func (r *resolver) value(f *file, e ast.Expr) (string, bool) {
	switch x := e.(type) {
	case *ast.BasicLit:
		if x.Kind == token.STRING {
			v, err := strconv.Unquote(x.Value)
			return v, err == nil
		}
	case *ast.Ident:
		v, ok := r.consts[path.Dir(f.path)][x.Name]
		return v, ok
	case *ast.SelectorExpr:
		if id, ok := x.X.(*ast.Ident); ok {
			v, ok := r.consts[r.importDir(f, id.Name)][x.Sel.Name]
			return v, ok
		}
	case *ast.ParenExpr:
		return r.value(f, x.X)
	}
	return "", false
}

type decl struct {
	kind   string
	file   string
	line   int
	fields map[string]ast.Expr
	name   string
	named  bool // name resolved to a constant
	values map[string]string
}

// declarations finds composite literals of kernel kinds: qualified by the
// kernel import anywhere, or unqualified inside the kernel package itself.
func declarations(t *tree, cfg Config) []decl {
	r := newResolver(t)
	order := map[string][]string{}
	for _, f := range t.pkg(cfg.Kernel) {
		ast.Inspect(f.ast, func(n ast.Node) bool {
			if ts, ok := n.(*ast.TypeSpec); ok {
				if st, ok := ts.Type.(*ast.StructType); ok && cfg.Kinds[ts.Name.Name].Name != "" {
					for _, fl := range st.Fields.List {
						for _, nm := range fl.Names {
							order[ts.Name.Name] = append(order[ts.Name.Name], nm.Name)
						}
					}
				}
			}
			return true
		})
	}
	var out []decl
	for _, p := range sortedKeys(t.files) {
		f := t.files[p]
		inKernel := path.Dir(p) == cfg.Kernel
		ast.Inspect(f.ast, func(n ast.Node) bool {
			lit, ok := n.(*ast.CompositeLit)
			if !ok {
				return true
			}
			kind := ""
			switch x := lit.Type.(type) {
			case *ast.SelectorExpr:
				if id, ok := x.X.(*ast.Ident); ok && r.importDir(f, id.Name) == cfg.Kernel {
					kind = x.Sel.Name
				}
			case *ast.Ident:
				if inKernel {
					kind = x.Name
				}
			}
			k, ok := cfg.Kinds[kind]
			if !ok {
				return true
			}
			d := decl{kind: kind, file: p, line: f.fset.Position(lit.Pos()).Line, fields: map[string]ast.Expr{}, values: map[string]string{}}
			for i, el := range lit.Elts {
				if kv, ok := el.(*ast.KeyValueExpr); ok {
					if id, ok := kv.Key.(*ast.Ident); ok {
						d.fields[id.Name] = kv.Value
					}
				} else if i < len(order[kind]) {
					d.fields[order[kind][i]] = el
				}
			}
			for name, e := range d.fields {
				if v, ok := r.value(f, e); ok {
					d.values[name] = v
				}
			}
			d.name, d.named = d.values[k.Name]
			out = append(out, d)
			return true
		})
	}
	return out
}

// consumed collects string case labels in the consumer package's switches.
func consumed(t *tree, cfg Config) map[string]bool {
	r := newResolver(t)
	out := map[string]bool{}
	for _, f := range t.pkg(cfg.Consumers) {
		ast.Inspect(f.ast, func(n ast.Node) bool {
			if c, ok := n.(*ast.CaseClause); ok {
				for _, e := range c.List {
					if v, ok := r.value(f, e); ok {
						out[v] = true
					}
				}
			}
			return true
		})
	}
	return out
}

func analyse(old, cur *tree, cfg Config) []Finding {
	var findings []Finding
	add := func(rule, code, p string, line int, format string, a ...any) {
		findings = append(findings, Finding{rule, code, p, line, fmt.Sprintf(format, a...)})
	}
	existing := map[string]bool{} // kind+name declared in the baseline
	for _, d := range declarations(old, cfg) {
		existing[d.kind+"\x00"+d.name] = true
	}
	all := declarations(cur, cfg)
	seen := map[string]decl{}
	consumers := consumed(cur, cfg)
	for _, d := range all {
		k := cfg.Kinds[d.kind]
		if d.named {
			if prev, dup := seen[d.name]; dup {
				add("operation-declaration", "OPERATION_NAME_DUPLICATE", d.file, d.line, "%s also declared at %s:%d", d.name, prev.file, prev.line)
			}
			seen[d.name] = d
		}
		if d.named && existing[d.kind+"\x00"+d.name] {
			continue // baseline declarations are grandfathered
		}
		if !editable(cfg, d.file) {
			add("extension-scope", "DECLARATION_OUTSIDE_EXTENSION", d.file, d.line, "new %s.%s must be declared in %s", path.Base(cfg.Kernel), d.kind, strings.Join(cfg.Editable, ", "))
		}
		for _, field := range k.Required {
			e, present := d.fields[field]
			v, constant := d.values[field]
			switch {
			case !present:
				add("operation-declaration", "FIELD_MISSING", d.file, d.line, "%s.%s is required", d.kind, field)
			case !constant:
				add("operation-declaration", "FIELD_NOT_CONSTANT", d.file, d.line, "%s.%s must be a string constant, got %T", d.kind, field, e)
			case v == "":
				add("operation-declaration", "FIELD_EMPTY", d.file, d.line, "%s.%s is empty", d.kind, field)
			}
		}
		if d.named && !versioned.MatchString(d.name) {
			add("operation-declaration", "NAME_UNVERSIONED", d.file, d.line, "%q needs a version suffix like .v1", d.name)
		}
		if k.Event == "" {
			continue
		}
		event, constant := d.values[k.Event]
		if _, present := d.fields[k.Event]; present && !constant {
			continue // already FIELD_NOT_CONSTANT; the event is unknown statically
		}
		if event != "" && event != cfg.NoEvent {
			if !consumers[event] {
				add("event-consumer", "EVENT_NO_CONSUMER", d.file, d.line, "event %q is not handled by %s", event, cfg.Consumers)
			}
			continue
		}
		for _, e := range sortedKeys(consumers) {
			if d.named && Names(e, Stem(d.name)) {
				add("event-consumer", "EVENT_EXPECTED_BY_CONSUMER", d.file, d.line, "%s declares no event but %s handles %q", d.name, cfg.Consumers, e)
			}
		}
	}
	return append(findings, manual(old, cur, cfg, consumers)...)
}

// sqlCalls yields calls in fn whose arguments include a string literal with
// the given SQL fragment.
func sqlCalls(fn ast.Node, fragment string) []*ast.CallExpr {
	var out []*ast.CallExpr
	ast.Inspect(fn, func(n ast.Node) bool {
		if c, ok := n.(*ast.CallExpr); ok {
			for _, a := range c.Args {
				if lit, ok := a.(*ast.BasicLit); ok && lit.Kind == token.STRING && strings.Contains(lit.Value, fragment) {
					out = append(out, c)
					break
				}
			}
		}
		return true
	})
	return out
}

func render(fset *token.FileSet, e ast.Node) string {
	var b bytes.Buffer
	_ = printer.Fprint(&b, fset, e)
	return b.String()
}

// manual applies the hand-written-code rules to the editable packages.
func manual(old, cur *tree, cfg Config, consumers map[string]bool) []Finding {
	var findings []Finding
	r := newResolver(cur)
	oldR := newResolver(old)
	for _, dir := range editableDirs(cur, cfg) {
		files := cur.pkg(dir)
		writes := false
		for _, f := range files {
			writes = writes || len(sqlCalls(f.ast, `INSERT INTO "ActionRequest"`)) > 0
		}
		if !writes {
			continue // operations here go through the kernel
		}
		// Events written to the outbox by hand, from SQL text or constant arguments.
		emitted := map[string]string{}
		for _, f := range files {
			for _, c := range sqlCalls(f.ast, `"PurchaseEvent"`) {
				for _, a := range c.Args {
					if lit, ok := a.(*ast.BasicLit); ok && strings.Contains(lit.Value, `"PurchaseEvent"`) {
						for _, m := range sqlLiteral.FindAllStringSubmatch(lit.Value, -1) {
							emitted[m[1]] = fmt.Sprintf("%s:%d", f.path, f.fset.Position(c.Pos()).Line)
						}
					} else if v, ok := r.value(f, a); ok && eventName.MatchString(v) && !versioned.MatchString(v) {
						emitted[v] = fmt.Sprintf("%s:%d", f.path, f.fset.Position(c.Pos()).Line)
					}
				}
			}
		}
		for _, e := range sortedKeys(emitted) {
			if !consumers[e] && len(consumers) > 0 {
				findings = append(findings, Finding{"manual-event-consumer", "EVENT_NO_CONSUMER", emitted[e], 0, fmt.Sprintf("hand-written event %q is not handled by %s", e, cfg.Consumers)})
			}
		}
		before := writtenOps(old.pkg(dir), oldR)
		for _, v := range sortedKeys(writtenOps(files, r)) {
			if before[v] {
				continue
			}
			for _, e := range sortedKeys(consumers) {
				if Names(e, Stem(v)) && emitted[e] == "" {
					findings = append(findings, Finding{"manual-event-consumer", "EVENT_EXPECTED_BY_CONSUMER", dir, 0, fmt.Sprintf("new operation %s writes no %q event but %s handles it", v, e, cfg.Consumers)})
				}
			}
		}
		findings = append(findings, fingerprints(files, old.pkg(dir), r)...)
	}
	return findings
}

// writtenOps: versioned operation names of a hand-written package, from its
// string constants and from constant arguments of ActionRequest writes.
func writtenOps(files []*file, r *resolver) map[string]bool {
	out := map[string]bool{}
	for _, f := range files {
		for _, v := range r.consts[path.Dir(f.path)] {
			if versioned.MatchString(v) {
				out[v] = true
			}
		}
		for _, c := range sqlCalls(f.ast, `INSERT INTO "ActionRequest"`) {
			for _, a := range c.Args {
				if v, ok := r.value(f, a); ok && versioned.MatchString(v) {
					out[v] = true
				}
			}
		}
	}
	return out
}

func editableDirs(t *tree, cfg Config) []string {
	dirs := map[string]bool{}
	for p := range t.files {
		if editable(cfg, p) {
			dirs[path.Dir(p)] = true
		}
	}
	return sortedKeys(dirs)
}

// handlerExtras returns the names of handler parameters that are request
// inputs other than the targets: the Idempotency-Key (3rd) and, for
// single-target handlers (ctx, identity, key, id, reason), the reason (5th).
func handlerExtras(files []*file) map[string]bool {
	out := map[string]bool{}
	for _, f := range files {
		ast.Inspect(f.ast, func(n ast.Node) bool {
			var ft *ast.FuncType
			switch x := n.(type) {
			case *ast.FuncLit:
				ft = x.Type
			case *ast.FuncDecl:
				ft = x.Type
			default:
				return true
			}
			var names []string
			for _, p := range ft.Params.List {
				for _, nm := range p.Names {
					names = append(names, nm.Name)
				}
			}
			if ft.Results != nil && ft.Results.NumFields() == 3 && (len(names) == 4 || len(names) == 5) {
				out[names[2]] = true
				if len(names) == 5 {
					out[names[4]] = true
				}
			}
			return true
		})
	}
	return out
}

// fingerprints: every new or changed function that writes ActionRequest must
// compute the requestHash through a call that receives the same operation
// identifier as the write: ledger.Fingerprint(op, ids), helper(op, ...), or a
// package-local helper whose body names op. Arguments other than op must not
// include the Idempotency-Key or the reason. Functions identical to the
// baseline are grandfathered.
func fingerprints(files []*file, old []*file, r *resolver) []Finding {
	var findings []Finding
	extras := handlerExtras(files)
	unchanged := map[string]bool{}
	for _, f := range old {
		for _, fn := range innermostFuncs(f.ast) {
			unchanged[render(f.fset, fn.node)] = true
		}
	}
	helpers := map[string]*ast.FuncDecl{}
	for _, f := range files {
		for _, d := range f.ast.Decls {
			if fd, ok := d.(*ast.FuncDecl); ok && fd.Recv == nil {
				helpers[fd.Name.Name] = fd
			}
		}
	}
	for _, f := range files {
		consts := r.consts[path.Dir(f.path)]
		for _, fn := range innermostFuncs(f.ast) {
			inserts := directSQLCalls(fn, `INSERT INTO "ActionRequest"`)
			if len(inserts) == 0 || unchanged[render(f.fset, fn.node)] {
				continue
			}
			// The operation argument: a versioned string constant, or a parameter of
			// this function (a shared helper taking op) that is not key/reason.
			// Operation keys of the write: rendered identifier/selector arguments other
			// than key/reason, and "=value" for versioned string constants.
			keys := func(c *ast.CallExpr) map[string]bool {
				out := map[string]bool{}
				for _, a := range c.Args {
					switch x := a.(type) {
					case *ast.Ident:
						if !extras[x.Name] {
							out[x.Name] = true
						}
					case *ast.SelectorExpr:
						out[render(f.fset, x)] = true
					}
					if v, ok := r.value(f, a); ok && versioned.MatchString(v) {
						out["="+v] = true
					}
				}
				return out
			}
			ops := map[string]bool{}
			for _, c := range inserts {
				for k := range keys(c) {
					ops[k] = true
				}
			}
			line := f.fset.Position(inserts[0].Pos()).Line
			matched := false
			ast.Inspect(fn.body, func(n ast.Node) bool {
				if _, nested := n.(*ast.FuncLit); nested {
					return false
				}
				c, ok := n.(*ast.CallExpr)
				if !ok || hasSQL(c) {
					return true
				}
				// Only ledger.Fingerprint or a package-local helper can produce the hash.
				helper := (*ast.FuncDecl)(nil)
				switch fun := c.Fun.(type) {
				case *ast.SelectorExpr:
					if fun.Sel.Name != "Fingerprint" {
						return true
					}
				case *ast.Ident:
					if helper = helpers[fun.Name]; helper == nil {
						return true
					}
				default:
					return true
				}
				op := false
				for k := range keys(c) {
					op = op || ops[k]
				}
				if helper != nil && !op {
					ast.Inspect(helper.Body, func(n ast.Node) bool {
						if id, ok := n.(*ast.Ident); ok {
							v := consts[id.Name]
							op = op || versioned.MatchString(v) && ops["="+v]
						}
						return !op
					})
				}
				if !op {
					return true
				}
				matched = true
				for _, a := range c.Args {
					ast.Inspect(a, func(n ast.Node) bool {
						if id, ok := n.(*ast.Ident); ok && extras[id.Name] {
							findings = append(findings, Finding{"manual-fingerprint", "FINGERPRINT_EXTRA_INPUT", f.path, f.fset.Position(c.Pos()).Line,
								fmt.Sprintf("requestHash call %s includes non-target request field %q", render(f.fset, c), id.Name)})
						}
						return true
					})
				}
				return true
			})
			if !matched {
				findings = append(findings, Finding{"manual-fingerprint", "FINGERPRINT_OPERATION_MISSING", f.path, line,
					"ActionRequest write whose requestHash is not computed from the same operation (ledger.Fingerprint(op, ids))"})
			}
		}
	}
	return findings
}

// hasSQL reports whether a call passes a SQL statement literal.
func hasSQL(c *ast.CallExpr) bool {
	for _, a := range c.Args {
		if lit, ok := a.(*ast.BasicLit); ok && lit.Kind == token.STRING && sqlVerb.MatchString(lit.Value) {
			return true
		}
	}
	return false
}

type fnode struct {
	node   ast.Node
	params *ast.FieldList
	body   *ast.BlockStmt
}

func innermostFuncs(f *ast.File) []fnode {
	var out []fnode
	ast.Inspect(f, func(n ast.Node) bool {
		switch x := n.(type) {
		case *ast.FuncDecl:
			if x.Body != nil {
				out = append(out, fnode{x, x.Type.Params, x.Body})
			}
		case *ast.FuncLit:
			out = append(out, fnode{x, x.Type.Params, x.Body})
		}
		return true
	})
	return out
}

// directSQLCalls is sqlCalls restricted to fn's own body (not nested literals).
func directSQLCalls(fn fnode, fragment string) []*ast.CallExpr {
	var out []*ast.CallExpr
	ast.Inspect(fn.body, func(n ast.Node) bool {
		if _, nested := n.(*ast.FuncLit); nested {
			return false
		}
		if c, ok := n.(*ast.CallExpr); ok {
			for _, a := range c.Args {
				if lit, ok := a.(*ast.BasicLit); ok && lit.Kind == token.STRING && strings.Contains(lit.Value, fragment) {
					out = append(out, c)
					break
				}
			}
		}
		return true
	})
	return out
}
