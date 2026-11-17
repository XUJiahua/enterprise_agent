package main

import (
	"context"
	"flag"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/procurement"
	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
	"github.com/jackc/pgx/v5/pgxpool"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

func main() {
	mode := flag.String("mode", "baseline", "baseline, guarded, or product (v0.1 kernel API)")
	flag.Parse()
	port := os.Getenv("POC_HTTP_PORT")
	if port == "" {
		port = "8080"
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		log.Fatal("invalid POC_HTTP_PORT")
	}
	if *mode == "product" {
		log.Fatal(product(port))
	}
	s, e := app.New(context.Background(), os.Getenv("DATABASE_URL"), *mode, os.Getenv("POC_MUTATION"))
	if e != nil {
		log.Fatal(e)
	}
	defer s.Pool.Close()
	srv := &http.Server{Addr: "127.0.0.1:" + port, Handler: s, ReadHeaderTimeout: 5 * time.Second}
	log.Printf("POC %s listening on %s", *mode, srv.Addr)
	log.Fatal(srv.ListenAndServe())
}

// product serves the v0.1 procurement API on the kernel runner. Migrations are
// applied beforehand with `appctl migrate up`; DATABASE_URL is the runtime role.
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
	r, err := pg.New(ctx, pool, procurement.Operations()...)
	if err != nil {
		return err
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
