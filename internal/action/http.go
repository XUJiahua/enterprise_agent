package action

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"time"
	"unicode/utf8"

	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/jackc/pgx/v5/pgconn"
)

const MaxBodyBytes = 65536

// HTTP adds one versioned POC Action endpoint; existing APIs use the old server.
func HTTP(executor *Executor, fallback http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/purchases/bulk-approve" {
			fallback.ServeHTTP(w, r)
			return
		}
		u, ok := app.AuthenticateDemo(r.Header.Get("Authorization"))
		if !ok {
			reject(w, 401)
			return
		}
		if u.Role != "approver" {
			reject(w, 403)
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBodyBytes))
		if err != nil {
			var large *http.MaxBytesError
			if errors.As(err, &large) {
				reject(w, 413)
			} else {
				reject(w, 400)
			}
			return
		}
		ids, ok := decode(body)
		keys := r.Header.Values("Idempotency-Key")
		if !ok || len(keys) != 1 || !validKey(keys[0]) {
			reject(w, 400)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		result, replayed, err := executor.BulkApprove(ctx, u, keys[0], ids)
		if err != nil {
			var rejected *Rejection
			var pg *pgconn.PgError
			switch {
			case errors.As(err, &rejected):
				reject(w, rejected.Status)
			case errors.As(err, &pg) && pg.Code == "23505":
				reject(w, 409)
			default:
				log.Printf("Action transaction failed: %v", err)
				reject(w, 503)
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if replayed {
			w.Header().Set("Idempotency-Replayed", "true")
		} else {
			w.Header().Set("Idempotency-Replayed", "false")
		}
		w.WriteHeader(200)
		_ = json.NewEncoder(w).Encode(result)
	})
}

func reject(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": http.StatusText(status)})
}

func decode(body []byte) ([]string, bool) {
	if !utf8.Valid(body) {
		return nil, false
	}
	d := json.NewDecoder(bytes.NewReader(body))
	if token, err := d.Token(); err != nil || token != json.Delim('{') {
		return nil, false
	}
	if key, err := d.Token(); err != nil || key != "ids" {
		return nil, false
	}
	var raw []json.RawMessage
	if d.Decode(&raw) != nil {
		return nil, false
	}
	if token, err := d.Token(); err != nil || token != json.Delim('}') {
		return nil, false
	}
	if d.Decode(new(any)) != io.EOF {
		return nil, false
	}
	ids := make([]string, len(raw))
	for i, value := range raw {
		if len(value) == 0 || value[0] != '"' || json.Unmarshal(value, &ids[i]) != nil {
			return nil, false
		}
	}
	ids, err := canonical(ids)
	return ids, err == nil
}
