package main

import (
	"context"
	"flag"
	"github.com/XUJiahua/enterprise_agent/internal/app"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	mode := flag.String("mode", "baseline", "baseline or guarded")
	flag.Parse()
	s, e := app.New(context.Background(), os.Getenv("DATABASE_URL"), *mode, os.Getenv("POC_MUTATION"))
	if e != nil {
		log.Fatal(e)
	}
	defer s.Pool.Close()
	srv := &http.Server{Addr: "127.0.0.1:8080", Handler: s, ReadHeaderTimeout: 5 * time.Second}
	log.Printf("POC %s listening on %s", *mode, srv.Addr)
	log.Fatal(srv.ListenAndServe())
}
