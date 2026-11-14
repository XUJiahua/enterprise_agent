package policy

import (
	"os"
	"strings"
	"testing"
)

func TestPolicyRejectsWeakenedInvariants(t *testing.T) {
	b, e := os.ReadFile("../../policies/purchase.json")
	if e != nil {
		t.Fatal(e)
	}
	for _, test := range []struct{ name, from, to string }{
		{"self approval", `"disallowSelf": true`, `"disallowSelf": false`},
		{"missing tenant", `"tenantField": "tenantId"`, `"tenantField": ""`},
		{"SQL injection", `"createRoles": ["requester"]`, `"createRoles": ["requester'); DROP TABLE x;--"]`},
		{"unknown operator", `"version": 1`, `"operator": "eval", "version": 1`},
		{"missing protected field", `"protectedFields": ["tenantId", "createdBy", "internalNote"]`, `"protectedFields": ["tenantId"]`},
	} {
		t.Run(test.name, func(t *testing.T) {
			s := strings.Replace(string(b), test.from, test.to, 1)
			if s == string(b) {
				t.Fatal("test fixture did not change")
			}
			if _, e := Parse([]byte(s)); e == nil {
				t.Fatal("weakened policy accepted")
			}
		})
	}
}
func TestMigrationSubset(t *testing.T) {
	for _, sql := range []string{`DROP TABLE "Purchase"`, `ALTER TABLE "Purchase" DROP COLUMN title`, `DO $$ BEGIN END $$`, `CREATE TABLE x AS SELECT 1`, `CREATE TABLE x(a int); /* nested /* ok */ */ ALTER TABLE x DROP COLUMN a`} {
		if CheckMigration(sql) == nil {
			t.Fatalf("accepted unsupported SQL: %s", sql)
		}
	}
	if e := CheckMigration(`-- comment
CREATE TABLE x (id text DEFAULT 'semi;colon', value text CHECK(value IN ('drop','alter'))); CREATE INDEX idx ON x(id);`); e != nil {
		t.Fatal(e)
	}
}
