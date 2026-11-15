// Batch Actions (approve and reject) through the platform Action kernel.
package candidate

import (
	"context"
	"errors"
	"net/http"

	"github.com/XUJiahua/enterprise_agent/internal/action"
	"github.com/XUJiahua/enterprise_agent/internal/app"
	"github.com/XUJiahua/enterprise_agent/internal/protocol"
	"github.com/jackc/pgx/v5/pgxpool"
)

func New(ctx context.Context, pool *pgxpool.Pool, fallback http.Handler) (http.Handler, error) {
	executor, err := action.New(ctx, pool)
	if err != nil {
		return nil, err
	}
	approve := func(ctx context.Context, u app.Identity, key string, ids []string) (protocol.Result, bool, error) {
		approved, replayed, err := executor.Run(ctx, u, action.Approve, key, ids)
		var denied *action.Rejection
		if errors.As(err, &denied) {
			return protocol.Result{}, false, &protocol.Rejection{Status: denied.Status}
		}
		if err != nil {
			return protocol.Result{}, false, err
		}
		return protocol.Result{Field: action.Approve.Field, IDs: approved}, replayed, nil
	}
	reject := func(ctx context.Context, u app.Identity, key string, ids []string) (protocol.Result, bool, error) {
		rejected, replayed, err := executor.Run(ctx, u, action.Reject, key, ids)
		var denied *action.Rejection
		if errors.As(err, &denied) {
			return protocol.Result{}, false, &protocol.Rejection{Status: denied.Status}
		}
		if err != nil {
			return protocol.Result{}, false, err
		}
		return protocol.Result{Field: action.Reject.Field, IDs: rejected}, replayed, nil
	}
	return protocol.HTTP(protocol.Routes{
		"/purchases/bulk-approve": approve,
		"/purchases/bulk-reject":  reject,
	}, fallback), nil
}
