// Command external is a module outside this repository that imports the
// kernel. It proves the public packages build without internal/ code; CI runs
// it against PostgreSQL when EXAMPLE_DATABASE_URL is set.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ping is a no-op operation: it audits its targets and changes nothing else.
type ping struct{}

func (ping) Definition() kernel.Definition {
	return kernel.Definition{Ref: kernel.Ref{Name: "example.ping", Version: 1}, Targets: "IDs", MaxTargets: 5, NoEventReason: "example only"}
}

func (ping) Normalize(raw json.RawMessage) (kernel.Input, error) {
	var req struct{ IDs []string }
	if err := json.Unmarshal(raw, &req); err != nil {
		return kernel.Input{}, kernel.Invalidf("bad request")
	}
	return kernel.Input{Targets: req.IDs}, nil
}

func (ping) Authorize(p kernel.Principal) error { return nil }

func (ping) Execute(_ context.Context, _ *pg.Session, in kernel.Input) (kernel.Outcome, error) {
	out := kernel.Outcome{Targets: in.Targets, Result: map[string]int{"pinged": len(in.Targets)}}
	for _, id := range in.Targets {
		out.Audit = append(out.Audit, kernel.Audit{Target: id, Change: json.RawMessage(`{}`)})
	}
	return out, nil
}

func main() {
	url := os.Getenv("EXAMPLE_DATABASE_URL")
	if url == "" {
		fmt.Println("built; set EXAMPLE_DATABASE_URL (runtime role, kernel schema applied) to run")
		return
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()
	r, err := pg.New(ctx, pool, pg.Registry{Operations: []pg.Operation{ping{}}})
	if err != nil {
		log.Fatal(err)
	}
	p := kernel.Principal{Tenant: "example", Actor: "me"}
	res, err := r.Run(ctx, p, kernel.Ref{Name: "example.ping", Version: 1}, "k1", json.RawMessage(`{"IDs":["a","b"]}`))
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("%s replayed=%v\n", res.Body, res.Replayed)
}
