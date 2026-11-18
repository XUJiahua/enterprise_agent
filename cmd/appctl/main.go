package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"

	"github.com/XUJiahua/enterprise_agent/internal/extcheck"
	"github.com/XUJiahua/enterprise_agent/internal/policy"
	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

type Finding struct {
	Code   string `json:"code"`
	Path   string `json:"path"`
	Detail string `json:"detail"`
}

func main() { os.Exit(run()) }
func run() int {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: appctl compile|check|plan|verify|extensions|operations|migrate|describe|validate [--base REF] [--root DIR]")
		return 1
	}
	command := os.Args[1]
	if command == "extensions" {
		return extensions(os.Args[2:])
	}
	if command == "operations" {
		return operations(os.Args[2:])
	}
	if command == "migrate" {
		return migrateProduct(os.Args[2:])
	}
	if command == "describe" {
		return describe()
	}
	if command == "validate" {
		// Separate from verify, whose POC probes (tests/verifier.py) run on
		// partial checkouts without the product registry.
		f := flag.NewFlagSet("validate", flag.ContinueOnError)
		root := f.String("root", ".", "checkout")
		base := f.String("base", "", "trusted git baseline ref")
		if f.Parse(os.Args[2:]) != nil {
			return 1
		}
		all := append(append([]Finding{}, contractFindings(*root, *base)...), writeFindings(*root)...)
		return report(append(all, behaviorFindings(*root, *base)...))
	}
	f := flag.NewFlagSet(command, flag.ContinueOnError)
	root := f.String("root", ".", "application root")
	base := f.String("base", "", "trusted git baseline ref")
	if f.Parse(os.Args[2:]) != nil {
		return 1
	}
	if !slices.Contains([]string{"compile", "check", "plan", "verify"}, command) {
		fmt.Fprintln(os.Stderr, "unknown command")
		return 1
	}
	p, e := policy.Load(filepath.Join(*root, "policies/purchase.json"))
	if e != nil {
		return report([]Finding{{"POLICY_INVALID", "policies/purchase.json", e.Error()}})
	}
	expected := policy.Compile(p)
	if command == "compile" {
		if e = os.MkdirAll(filepath.Join(*root, "generated"), 0755); e == nil {
			e = os.WriteFile(filepath.Join(*root, "generated/guard.sql"), []byte(expected), 0644)
		}
		if e != nil {
			fmt.Fprintln(os.Stderr, e)
			return 1
		}
		return 0
	}
	findings := []Finding{}
	b, e := os.ReadFile(filepath.Join(*root, "generated/guard.sql"))
	if e != nil || string(b) != expected {
		findings = append(findings, Finding{"GENERATED_DRIFT", "generated/guard.sql", "compiled RLS differs from policy; run appctl compile"})
	}
	var lock map[string]string
	b, e = os.ReadFile(filepath.Join(*root, "db/migrations.lock.json"))
	if e != nil || json.Unmarshal(b, &lock) != nil || len(lock) == 0 {
		findings = append(findings, Finding{"MIGRATION_LOCK", "db/migrations.lock.json", "missing or invalid nonempty manifest"})
	}
	files, _ := filepath.Glob(filepath.Join(*root, "db/migrations/*.sql"))
	current := map[string]string{}
	for _, file := range files {
		b, e := os.ReadFile(file)
		if e != nil {
			findings = append(findings, Finding{"MIGRATION_READ", file, e.Error()})
			continue
		}
		path := "db/migrations/" + filepath.Base(file)
		if err := policy.CheckMigration(string(b)); err != nil {
			findings = append(findings, Finding{"MIGRATION_REVIEW_REQUIRED", path, err.Error()})
		}
		current[path] = policy.Digest(b)
		if lock[path] != current[path] {
			findings = append(findings, Finding{"MIGRATION_DRIFT", path, "checksum does not match locked history"})
		}
	}
	for path := range lock {
		if _, ok := current[path]; !ok {
			findings = append(findings, Finding{"MIGRATION_REMOVED", path, "locked migration is missing"})
		}
	}
	if *base != "" {
		// An invalid ref must fail closed; bootstrap is allowed only for a real base without a policy.
		if _, e := git(*root, "rev-parse", "--verify", *base+"^{commit}"); e != nil {
			findings = append(findings, Finding{"BASE_INVALID", *base, e.Error()})
		} else {
			paths, e := git(*root, "ls-tree", "-r", "--name-only", *base, "--", "db/migrations", "policies/purchase.json")
			if e != nil {
				findings = append(findings, Finding{"BASE_READ", *base, e.Error()})
			}
			for _, path := range strings.Fields(string(paths)) {
				old, e := git(*root, "show", *base+":"+path)
				if e != nil {
					findings = append(findings, Finding{"BASE_READ", path, e.Error()})
					continue
				}
				if path == "policies/purchase.json" {
					prev, e := policy.Parse(old)
					if e != nil {
						findings = append(findings, Finding{"BASE_POLICY_INVALID", path, e.Error()})
						continue
					}
					groups := []struct {
						name     string
						old, new []string
					}{{"read", prev.ReadRoles, p.ReadRoles}, {"create", prev.CreateRoles, p.CreateRoles}, {"approve", prev.ApproveRoles, p.ApproveRoles}}
					for _, g := range groups {
						for _, r := range g.new {
							if !slices.Contains(g.old, r) {
								findings = append(findings, Finding{"POLICY_EXPANSION", path, g.name + " grants new role " + r + "; requires policy-owner review"})
							}
						}
					}
				} else if current[path] != policy.Digest(old) {
					findings = append(findings, Finding{"PUBLISHED_MIGRATION_CHANGED", path, "differs from trusted git baseline, even if lock was rewritten"})
				}
			}
			findings = append(findings, productMigrations(*root, *base)...)
		}
	}
	return report(findings)
}

// productDir holds the v0.1 golang-migrate files (NNNN_name.up.sql).
const productDir = "internal/procurement/migrations"

// productMigrations requires every product migration published at base to be
// byte-identical and present, and every new one to sort after them, since
// golang-migrate never applies a file below the database's current version.
func productMigrations(root, base string) []Finding {
	var findings []Finding
	paths, e := git(root, "ls-tree", "-r", "--name-only", base, "--", productDir)
	if e != nil {
		return []Finding{{"BASE_READ", productDir, e.Error()}}
	}
	published := strings.Fields(string(paths))
	latest := ""
	for _, path := range published {
		latest = max(latest, filepath.Base(path))
		old, e := git(root, "show", base+":"+path)
		if e != nil {
			findings = append(findings, Finding{"BASE_READ", path, e.Error()})
			continue
		}
		now, e := os.ReadFile(filepath.Join(root, path))
		switch {
		case e != nil:
			findings = append(findings, Finding{"MIGRATION_REMOVED", path, "published migration is missing"})
		case policy.Digest(now) != policy.Digest(old):
			findings = append(findings, Finding{"PUBLISHED_MIGRATION_CHANGED", path, "differs from trusted git baseline"})
		}
	}
	files, _ := filepath.Glob(filepath.Join(root, productDir, "*.sql"))
	for _, file := range files {
		path := productDir + "/" + filepath.Base(file)
		if !slices.Contains(published, path) && filepath.Base(file) <= latest {
			findings = append(findings, Finding{"MIGRATION_OUT_OF_ORDER", path, "new migration sorts before published " + latest})
		}
	}
	return findings
}
func git(root string, args ...string) ([]byte, error) {
	return exec.Command("git", append([]string{"-C", root}, args...)...).Output()
}
func report(f []Finding) int {
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": len(f) == 0, "findings": f})
	if len(f) > 0 {
		return 1
	}
	return 0
}

// extensions statically checks a candidate checkout against its trusted
// baseline commit; exit 0 only with no findings, 2 when it cannot run.
func extensions(args []string) int {
	f := flag.NewFlagSet("extensions", flag.ContinueOnError)
	root := f.String("root", ".", "candidate checkout")
	base := f.String("base", "", "trusted baseline commit (required)")
	if f.Parse(args) != nil || *base == "" {
		fmt.Fprintln(os.Stderr, "usage: appctl extensions --base REF [--root DIR]")
		return 2
	}
	findings, err := extcheck.Check(*root, *base, extcheck.Default)
	if err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "error": err.Error()})
		return 2
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": len(findings) == 0, "findings": findings})
	if len(findings) > 0 {
		return 1
	}
	return 0
}

// operations checks a candidate checkout against the reviewed operation
// catalog compiled into this binary: exit 0 pass, 1 findings, 2 cannot run,
// 3 unverifiable (blocks the gate). --legacy limits the v6 declaration types
// the Verifier knows (for the knowledge-ablation replay).
func operations(args []string) int {
	f := flag.NewFlagSet("operations", flag.ContinueOnError)
	root := f.String("root", ".", "candidate checkout")
	base := f.String("base", "", "trusted baseline commit (required)")
	legacy := f.String("legacy", "Operation,Update", "known internal/action declaration types")
	if f.Parse(args) != nil || *base == "" {
		fmt.Fprintln(os.Stderr, "usage: appctl operations --base REF [--root DIR] [--legacy Operation,Update]")
		return 2
	}
	cfg := extcheck.DefaultOps
	cfg.Kernels = extcheck.Kernels{}
	for dir, roles := range extcheck.DefaultOps.Kernels {
		cfg.Kernels[dir] = map[string]extcheck.Role{}
		for typ, role := range roles {
			if role != extcheck.RoleLegacy || slices.Contains(strings.Split(*legacy, ","), typ) {
				cfg.Kernels[dir][typ] = role
			}
		}
	}
	rep, err := extcheck.CheckOperations(*root, *base, cfg)
	if err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "catalog": rep.Catalog, "error": err.Error()})
		return 2
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": rep.Verdict == "pass", "catalog": rep.Catalog, "entries": rep.Entries,
		"verdict": rep.Verdict, "findings": rep.Findings})
	return rep.Exit()
}

// migrateProduct applies the v0.1 product migrations as the admin role in
// ADMIN_DATABASE_URL. It refuses a dirty database.
func migrateProduct(args []string) int {
	url := os.Getenv("ADMIN_DATABASE_URL")
	if len(args) != 1 || args[0] != "up" || url == "" {
		fmt.Fprintln(os.Stderr, "usage: ADMIN_DATABASE_URL=... appctl migrate up")
		return 2
	}
	version, applied, err := procurement.Migrate(url)
	out := map[string]any{"ok": err == nil, "version": version, "applied": applied}
	if err != nil {
		out["error"] = err.Error()
	}
	_ = json.NewEncoder(os.Stdout).Encode(out)
	if err != nil {
		return 1
	}
	return 0
}

// contractFile is the committed output of `appctl describe`.
const contractFile = "internal/procurement/contract.json"

// describe prints the operation/consumer contract of the procurement registry,
// the same one cmd/product hands to pg.New.
func describe() int {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		return report([]Finding{{"REGISTRY_INVALID", "internal/procurement", err.Error()}})
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	fmt.Println(string(b))
	return 0
}

// contractFindings checks that the registry is valid and that contractFile is
// its current description. With base, every operation version published there
// must keep an identical definition: changing events, MaxTargets or the
// exemption needs a new version. It cannot see behavior changes inside
// Normalize/Execute; those still need review and tests.
func contractFindings(root, base string) []Finding {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		return []Finding{{"REGISTRY_INVALID", "internal/procurement", err.Error()}}
	}
	want, _ := json.MarshalIndent(c, "", "  ")
	var findings []Finding
	got, err := os.ReadFile(filepath.Join(root, contractFile))
	if err != nil || strings.TrimSpace(string(got)) != string(want) {
		findings = append(findings, Finding{"CONTRACT_DRIFT", contractFile, "differs from the registry; run appctl describe > " + contractFile})
	}
	if base == "" {
		return findings
	}
	if _, err := git(root, "rev-parse", "--verify", base+"^{commit}"); err != nil {
		return append(findings, Finding{"BASE_INVALID", base, err.Error()})
	}
	listed, err := git(root, "ls-tree", "--name-only", base, "--", contractFile)
	if err != nil {
		return append(findings, Finding{"BASE_READ", contractFile, err.Error()})
	}
	if strings.TrimSpace(string(listed)) == "" {
		return findings // not published at base yet
	}
	old, err := git(root, "show", base+":"+contractFile)
	if err != nil {
		return append(findings, Finding{"BASE_READ", contractFile, err.Error()})
	}
	var prev pg.Contract
	if err := json.Unmarshal(old, &prev); err != nil {
		return append(findings, Finding{"BASE_CONTRACT_INVALID", contractFile, err.Error()})
	}
	// Compare raw JSON so a definition field the kernel added after base was
	// published (absent there) is not reported as an in-place change.
	var prevRaw struct{ Operations []map[string]json.RawMessage }
	_ = json.Unmarshal(old, &prevRaw)
	for i, p := range prev.Operations {
		for _, d := range c.Operations {
			if d.Ref != p.Ref || i >= len(prevRaw.Operations) {
				continue
			}
			var now map[string]json.RawMessage
			b, _ := json.Marshal(d)
			_ = json.Unmarshal(b, &now)
			was := prevRaw.Operations[i]
			for k, v := range now {
				if w, published := was[k]; !published && k == "targets" {
					continue // added in v0.1.1
				} else if !published || !jsonEqual(w, v) {
					findings = append(findings, Finding{"CONTRACT_CHANGED_WITHOUT_VERSION", contractFile, d.Ref.String() + " changed " + k + "; publish it as a new version"})
				}
			}
			for k := range was {
				if _, ok := now[k]; !ok {
					findings = append(findings, Finding{"CONTRACT_CHANGED_WITHOUT_VERSION", contractFile, d.Ref.String() + " dropped " + k + "; publish it as a new version"})
				}
			}
		}
	}
	return findings
}

func jsonEqual(a, b json.RawMessage) bool {
	var x, y any
	return json.Unmarshal(a, &x) == nil && json.Unmarshal(b, &y) == nil && reflect.DeepEqual(x, y)
}

// behaviorDir holds one frozen behavior file per operation version
// (internal/procurement/behavior_test.go runs them).
const behaviorDir = "internal/procurement/behavior"

// behaviorHarness are the files the frozen cases depend on for execution and
// comparison.
var behaviorHarness = []string{"internal/procurement/behavior_test.go", "kernel/pg/pgtest/pgtest.go"}

// behaviorFindings requires a behavior file for every registered operation
// version (BEHAVIOR_MISSING) and, with base, that every file published there
// is unchanged (BEHAVIOR_CHANGED). A behavior change of a published version
// therefore has to ship as a new version with its own file. The cases only
// see what they exercise: a change visible only in new scenarios (e.g. a new
// kind of row the old cases never create) is not detected.
func behaviorFindings(root, base string) []Finding {
	c, err := pg.Describe(procurement.Registry())
	if err != nil {
		return nil // reported by contractFindings
	}
	var findings []Finding
	for _, op := range c.Operations {
		path := behaviorDir + "/" + op.Ref.String() + ".json"
		if _, err := os.Stat(filepath.Join(root, path)); err != nil {
			findings = append(findings, Finding{"BEHAVIOR_MISSING", path, op.Ref.String() + " has no frozen behavior file"})
		}
	}
	// A TestMain in the package could exit before the frozen cases run.
	tests, _ := filepath.Glob(filepath.Join(root, "internal/procurement/*_test.go"))
	for _, f := range tests {
		if b, err := os.ReadFile(f); err == nil && strings.Contains(string(b), "func TestMain(") {
			findings = append(findings, Finding{"BEHAVIOR_HARNESS_BYPASS", "internal/procurement/" + filepath.Base(f), "TestMain is not allowed in internal/procurement; it can skip the frozen behavior cases"})
		}
	}
	if base == "" {
		return findings
	}
	if _, err := git(root, "rev-parse", "--verify", base+"^{commit}"); err != nil {
		return append(findings, Finding{"BASE_INVALID", base, err.Error()})
	}
	// The harness that runs the cases is frozen like the cases themselves.
	for _, path := range behaviorHarness {
		old, err := git(root, "show", base+":"+path)
		if err != nil {
			continue // not published at base yet
		}
		if now, err := os.ReadFile(filepath.Join(root, path)); err != nil || string(now) != string(old) {
			findings = append(findings, Finding{"BEHAVIOR_HARNESS_CHANGED", path, "the frozen behavior harness is protected; changing it needs owner review outside appctl"})
		}
	}
	listed, err := git(root, "ls-tree", "-r", "--name-only", base, "--", behaviorDir)
	if err != nil {
		return append(findings, Finding{"BASE_READ", behaviorDir, err.Error()})
	}
	for _, path := range strings.Fields(string(listed)) {
		old, err := git(root, "show", base+":"+path)
		if err != nil {
			findings = append(findings, Finding{"BASE_READ", path, err.Error()})
			continue
		}
		now, err := os.ReadFile(filepath.Join(root, path))
		if err == nil && string(now) == string(old) {
			continue
		}
		// A retired version may lose its file only together with its registration.
		ref := strings.TrimSuffix(filepath.Base(path), ".json")
		registered := false
		for _, op := range c.Operations {
			registered = registered || op.Ref.String() == ref
		}
		if err != nil && !registered {
			continue
		}
		findings = append(findings, Finding{"BEHAVIOR_CHANGED", path, ref + " is published; changed behavior needs a new version"})
	}
	return findings
}
