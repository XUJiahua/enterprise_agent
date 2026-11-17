package main

import (
	"context"
	"flag"
	"github.com/XUJiahua/enterprise_agent/internal/app"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

func main() {
	mode := flag.String("mode", "baseline", "baseline or guarded")
	flag.Parse()
	port := os.Getenv("POC_HTTP_PORT")
	if port == "" {
		port = "8080"
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		log.Fatal("invalid POC_HTTP_PORT")
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
