// Command product serves the v0.1 procurement API on the kernel runner. It is
// separate from cmd/server, whose source the frozen benchmarks export as-is.
// Apply migrations first with `appctl migrate up`; DATABASE_URL is the
// runtime role and POC_HTTP_PORT the port (default 8090). The notification
// consumer runs as a goroutine of the same process.
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	port := os.Getenv("POC_HTTP_PORT")
	if port == "" {
		port = "8090"
	}
	log.Fatal(product(port))
}

func product(port string) error {
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		return err
	}
	defer pool.Close()
	if err = pg.CheckRuntime(ctx, pool, procurement.Tables...); err != nil {
		return err
	}
	r, err := pg.New(ctx, pool, procurement.Registry())
	if err != nil {
		return err
	}
	// The notification consumer runs in this process; on start it first drains
	// whatever a previous process left pending. PRODUCT_CONSUMER=off disables it.
	if os.Getenv("PRODUCT_CONSUMER") != "off" {
		go r.Consume(ctx, procurement.Notifier, time.Second, func(err error) { log.Printf("consumer: %v", err) })
	}
	// Demo identities only; production authentication replaces this adapter.
	auth := func(req *http.Request) (kernel.Principal, bool) {
		u, ok := app.AuthenticateDemo(req.Header.Get("Authorization"))
		return kernel.Principal{Tenant: u.Tenant, Actor: u.User, Roles: []string{u.Role}}, ok
	}
	srv := &http.Server{Addr: "127.0.0.1:" + port, Handler: procurement.Handler(r, auth), ReadHeaderTimeout: 5 * time.Second}
	log.Printf("product API listening on %s", srv.Addr)
	return srv.ListenAndServe()
}
