// Package pgtest creates a throwaway PostgreSQL database for tests. Set
// KERNEL_TEST_ADMIN_URL to a superuser URL on a disposable server; each call
// creates a fresh database and drops it on cleanup. Without the variable the
// test is skipped, unless KERNEL_REQUIRE_DB=1 (CI), where it fails.
package pgtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Runtime is the non-owner login role tests connect as.
const Runtime, runtimePassword = "kernel_test_runtime", "test-only"

// DB is one fresh database.
type DB struct {
	Admin      *pgx.Conn // superuser connection, owner of everything created through it
	AdminURL   string
	RuntimeURL string
}

// New creates the database and the Runtime role (NOSUPERUSER NOBYPASSRLS).
func New(t testing.TB) *DB {
	t.Helper()
	base := os.Getenv("KERNEL_TEST_ADMIN_URL")
	if base == "" {
		if os.Getenv("KERNEL_REQUIRE_DB") == "1" {
			t.Fatal("KERNEL_REQUIRE_DB=1 but KERNEL_TEST_ADMIN_URL is unset")
		}
		t.Skip("set KERNEL_TEST_ADMIN_URL to run PostgreSQL tests")
	}
	ctx := context.Background()
	root, err := pgx.Connect(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	defer root.Close(ctx)
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	name := "kernel_test_" + hex.EncodeToString(b)
	if _, err = root.Exec(ctx, `CREATE DATABASE `+name); err != nil {
		t.Fatal(err)
	}
	if _, err = root.Exec(ctx, `DO $$ BEGIN
	  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='`+Runtime+`') THEN
	    CREATE ROLE `+Runtime+` LOGIN PASSWORD '`+runtimePassword+`' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
	  END IF; END $$`); err != nil && !isDuplicate(err) {
		t.Fatal(err)
	}
	u, _ := url.Parse(base)
	u.Path = "/" + name
	d := &DB{AdminURL: u.String()}
	u.User = url.UserPassword(Runtime, runtimePassword)
	d.RuntimeURL = u.String()
	if d.Admin, err = pgx.Connect(ctx, d.AdminURL); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		d.Admin.Close(ctx)
		if c, err := pgx.Connect(ctx, base); err == nil {
			_, _ = c.Exec(ctx, `DROP DATABASE `+name+` WITH (FORCE)`)
			c.Close(ctx)
		}
	})
	return d
}

// isDuplicate tolerates two packages racing to create the cluster-wide role.
func isDuplicate(err error) bool {
	return strings.Contains(err.Error(), "already exists") || strings.Contains(err.Error(), "duplicate key")
}

// Exec runs admin SQL or fails the test.
func (d *DB) Exec(t testing.TB, sql string, args ...any) {
	t.Helper()
	if _, err := d.Admin.Exec(context.Background(), sql, args...); err != nil {
		t.Fatalf("%v\n%.300s", err, sql)
	}
}

// Pool opens a pool as the Runtime role.
func (d *DB) Pool(t testing.TB) *pgxpool.Pool {
	t.Helper()
	p, err := pgxpool.New(context.Background(), d.RuntimeURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	return p
}

// Strings returns each row of sql as its columns joined by spaces.
func (d *DB) Strings(t testing.TB, sql string, args ...any) []string {
	t.Helper()
	rows, err := d.Admin.Query(context.Background(), sql, args...)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		vals, err := rows.Values()
		if err != nil {
			t.Fatal(err)
		}
		s := ""
		for i, v := range vals {
			if i > 0 {
				s += " "
			}
			s += toString(v)
		}
		out = append(out, s)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

func toString(v any) string {
	switch x := v.(type) {
	case nil:
		return "<nil>"
	case string:
		return x
	case []byte:
		return string(x)
	case map[string]any, []any:
		b, _ := json.Marshal(x)
		return string(b)
	}
	return fmt.Sprint(v)
}
