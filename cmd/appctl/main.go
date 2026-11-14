package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"

	"github.com/XUJiahua/enterprise_agent/internal/policy"
)

type Finding struct {
	Code   string `json:"code"`
	Path   string `json:"path"`
	Detail string `json:"detail"`
}

func main() { os.Exit(run()) }
func run() int {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: appctl compile|check|plan|verify [--base REF] [--root DIR]")
		return 1
	}
	command := os.Args[1]
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
		}
	}
	return report(findings)
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
