package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/XUJiahua/enterprise_agent/internal/action"
	"github.com/XUJiahua/enterprise_agent/internal/app"
)

func main() {
	mutation := flag.String("mutation", "", "controlled negative probe; requires POC_ALLOW_MUTATIONS=1")
	flag.Parse()
	port := os.Getenv("POC_HTTP_PORT")
	if port == "" {
		port = "8080"
	}
	if n, err := strconv.Atoi(port); err != nil || n < 1 || n > 65535 {
		log.Fatal("invalid POC_HTTP_PORT")
	}
	ctx := context.Background()
	core, err := app.New(ctx, os.Getenv("DATABASE_URL"), "guarded", "")
	if err != nil {
		log.Fatal(err)
	}
	defer core.Pool.Close()
	executor, err := action.New(ctx, core.Pool, *mutation)
	if err != nil {
		log.Fatal(err)
	}
	server := &http.Server{Addr: "127.0.0.1:" + port, Handler: action.HTTP(executor, core),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second}
	log.Printf("transactional Action POC listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}
