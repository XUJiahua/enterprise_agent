package procurement

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/XUJiahua/enterprise_agent/kernel"
	"github.com/XUJiahua/enterprise_agent/kernel/pg"
)

// Authenticate maps a request to the trusted principal; ok=false means 401.
type Authenticate func(*http.Request) (kernel.Principal, bool)

// Handler serves the procurement API:
//
//	POST /v1/operations/{name}/{version}  (Idempotency-Key header, JSON body)
//	GET  /v1/purchases
//	GET  /v1/activity
//
// Every write goes through Runner.Run; reads go through Runner.Read.
func Handler(r *pg.Runner, auth Authenticate) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/operations/{name}/{version}", func(w http.ResponseWriter, req *http.Request) {
		p, ok := auth(req)
		if !ok {
			writeJSON(w, http.StatusUnauthorized, errorBody("unauthenticated", "missing or unknown identity"))
			return
		}
		version, err := strconv.Atoi(req.PathValue("version"))
		if err != nil {
			writeError(w, kernel.Invalidf("invalid version"))
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, req.Body, pg.MaxRequestBytes))
		if err != nil {
			writeError(w, kernel.Invalidf("request too large or unreadable"))
			return
		}
		res, err := r.Run(req.Context(), p, kernel.Ref{Name: req.PathValue("name"), Version: version}, req.Header.Get("Idempotency-Key"), body)
		if err != nil {
			writeError(w, err)
			return
		}
		w.Header().Set("Idempotent-Replayed", strconv.FormatBool(res.Replayed))
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(res.Body)
	})
	mux.HandleFunc("GET /v1/purchases", func(w http.ResponseWriter, req *http.Request) {
		p, ok := auth(req)
		if !ok {
			writeJSON(w, http.StatusUnauthorized, errorBody("unauthenticated", "missing or unknown identity"))
			return
		}
		list, err := ListPurchases(req.Context(), r, p)
		if err != nil {
			writeError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"purchases": list})
	})
	mux.HandleFunc("GET /v1/activity", func(w http.ResponseWriter, req *http.Request) {
		p, ok := auth(req)
		if !ok {
			writeJSON(w, http.StatusUnauthorized, errorBody("unauthenticated", "missing or unknown identity"))
			return
		}
		a, err := ListActivity(req.Context(), r, p)
		if err != nil {
			writeError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, a)
	})
	return mux
}

// AuditEntry is one kernel audit row as shown to tenant members.
type AuditEntry struct {
	Operation string          `json:"operation"`
	Version   int             `json:"version"`
	Actor     string          `json:"actor"`
	Target    string          `json:"target"`
	Change    json.RawMessage `json:"change"`
	At        time.Time       `json:"at"`
}

// Notification is one projected notification for the caller.
type Notification struct {
	PurchaseID string    `json:"purchaseId"`
	Kind       string    `json:"kind"`
	At         time.Time `json:"at"`
}

// Activity is the tenant's latest 50 audit entries and the caller's latest 50 notifications.
type Activity struct {
	Audit         []AuditEntry   `json:"audit"`
	Notifications []Notification `json:"notifications"`
}

func ListActivity(ctx context.Context, r *pg.Runner, p kernel.Principal) (Activity, error) {
	a := Activity{Audit: []AuditEntry{}, Notifications: []Notification{}}
	err := r.Read(ctx, p, func(s *pg.Session) error {
		rows, err := s.Query(ctx, `SELECT operation, version, actor_id, target_id, change, created_at FROM kernel.audit ORDER BY id DESC LIMIT 50`)
		if err != nil {
			return err
		}
		for rows.Next() {
			var e AuditEntry
			var change []byte
			if err := rows.Scan(&e.Operation, &e.Version, &e.Actor, &e.Target, &change, &e.At); err != nil {
				rows.Close()
				return err
			}
			e.Change = change
			a.Audit = append(a.Audit, e)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			return err
		}
		rows, err = s.Query(ctx, `SELECT purchase_id, kind, created_at FROM procurement.notification WHERE recipient = $1 ORDER BY id DESC LIMIT 50`, p.Actor)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var n Notification
			if err := rows.Scan(&n.PurchaseID, &n.Kind, &n.At); err != nil {
				return err
			}
			a.Notifications = append(a.Notifications, n)
		}
		return rows.Err()
	})
	return a, err
}

// Purchase is the client-visible projection; only these fields are serialized.
type Purchase struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Amount    int64  `json:"amount"`
	Status    string `json:"status"`
	CreatedBy string `json:"createdBy"`
	Received  int64  `json:"received"`
}

// ListPurchases returns the principal's tenant's purchases (at most 500).
func ListPurchases(ctx context.Context, r *pg.Runner, p kernel.Principal) ([]Purchase, error) {
	list := []Purchase{}
	err := r.Read(ctx, p, func(s *pg.Session) error {
		rows, err := s.Query(ctx, `SELECT p.id, p.title, p.amount, p.status, p.created_by,
		  coalesce((SELECT sum(r.amount) FROM procurement.receipt r WHERE r.tenant_id = p.tenant_id AND r.purchase_id = p.id), 0)::bigint
		 FROM procurement.purchase p ORDER BY p.id COLLATE "C" LIMIT 500`)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var x Purchase
			if err := rows.Scan(&x.ID, &x.Title, &x.Amount, &x.Status, &x.CreatedBy, &x.Received); err != nil {
				return err
			}
			list = append(list, x)
		}
		return rows.Err()
	})
	return list, err
}

// Status maps a kernel error code to HTTP.
func Status(code kernel.Code) int {
	switch code {
	case kernel.Invalid:
		return http.StatusBadRequest
	case kernel.Denied:
		return http.StatusForbidden
	case kernel.Conflict:
		return http.StatusConflict
	case kernel.Retryable:
		return http.StatusServiceUnavailable
	}
	return http.StatusInternalServerError
}

func writeError(w http.ResponseWriter, err error) {
	code := kernel.CodeOf(err)
	msg := "internal error"
	var e *kernel.Error
	if errors.As(err, &e) && code != kernel.Internal {
		msg = e.Msg // Msg is client-safe; wrapped causes are never sent
	}
	writeJSON(w, Status(code), errorBody(string(code), msg))
}

func errorBody(code, msg string) map[string]string {
	return map[string]string{"error": code, "message": msg}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
