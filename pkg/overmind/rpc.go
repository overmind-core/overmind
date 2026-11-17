package overmind

import (
	"context"
	"database/sql"
	"errors"
	"log/slog"
	"net/http"
	"strings"

	"connectrpc.com/connect/v2"
	"connectrpc.com/connect/v2/connecthttp"
	"ganigeorgiev.com/pocketbase/dbx"
	validation "ganigeorgiev.com/pocketbase/ozzo-validation/v4"
	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"ganigeorgiev.com/pocketbase/pocketbase/tools/router"
)

const rpcPrefix = "/rpc"

type authKey struct{}

// mountRPC serves server under /rpc. PocketBase's auth middleware has already
// resolved the Authorization header, so handlers read the caller from ctx.
func mountRPC(r *router.Router[*core.RequestEvent], server *connect.Server) {
	mux := http.NewServeMux()
	connecthttp.Mount(mux, server)
	handler := http.StripPrefix(rpcPrefix, mux)

	r.POST(rpcPrefix+"/{path...}", func(e *core.RequestEvent) error {
		ctx := context.WithValue(e.Request.Context(), authKey{}, e.Auth)
		handler.ServeHTTP(e.Response, e.Request.WithContext(ctx))
		return nil
	})
}

// authUser returns the signed-in record from the users collection, or nil.
func authUser(ctx context.Context) *core.Record {
	auth, _ := ctx.Value(authKey{}).(*core.Record)
	if auth == nil || auth.Collection().Name != usersCollection {
		return nil
	}
	return auth
}

func requireUser(ctx context.Context) (*core.Record, error) {
	user := authUser(ctx)
	if user == nil {
		return nil, connect.NewError(connect.CodeUnauthenticated, "sign in as a user")
	}
	return user, nil
}

// requireMember returns the caller when they belong to projectID. A missing
// project and a non-member caller get the same error.
func requireMember(ctx context.Context, app core.App, projectID string) (*core.Record, error) {
	user, err := requireUser(ctx)
	if err != nil {
		return nil, err
	}
	if projectID == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, "project_id is required")
	}
	if _, err := findMembership(app, user.Id, projectID); err != nil {
		if errors.Is(err, errNotFound) {
			return nil, connect.NewError(connect.CodePermissionDenied, "not a member of this project")
		}
		return nil, internalError(err)
	}
	return user, nil
}

var errNotFound = errors.New("not found")

func findMembership(app core.App, userID, projectID string) (*core.Record, error) {
	return findOne(app, membershipsCollection, "user = {:user} && project = {:project}",
		dbx.Params{"user": userID, "project": projectID})
}

func findOne(app core.App, collection, filter string, params dbx.Params) (*core.Record, error) {
	record, err := app.FindFirstRecordByFilter(collection, filter, params)
	if err != nil {
		if isNoRows(err) {
			return nil, errNotFound
		}
		return nil, err
	}
	return record, nil
}

func isNoRows(err error) bool {
	return errors.Is(err, sql.ErrNoRows)
}

func internalError(err error) *connect.Error {
	return connect.NewError(connect.CodeInternal, "internal error").WithCause(err)
}

// asConnectError keeps connect errors raised inside transactions and maps
// record validation failures to InvalidArgument.
func asConnectError(err error) error {
	var connectErr *connect.Error
	if errors.As(err, &connectErr) {
		return connectErr
	}
	var validationErrs validation.Errors
	if errors.As(err, &validationErrs) {
		return connect.NewError(connect.CodeInvalidArgument, validationErrs.Error())
	}
	return internalError(err)
}

func isUniqueViolation(err error) bool {
	return err != nil && strings.Contains(err.Error(), "UNIQUE constraint failed")
}

func logRPCErrors(next connect.ServerFunc) connect.ServerFunc {
	return func(ctx context.Context, spec connect.Spec, stream connect.ServerStream) error {
		err := next(ctx, spec, stream)
		if err == nil {
			return nil
		}
		level := slog.LevelWarn
		switch connect.CodeOf(err) {
		case connect.CodeInternal, connect.CodeUnknown, connect.CodeDataLoss, connect.CodeUnavailable:
			level = slog.LevelError
		}
		slog.Log(ctx, level, "rpc failed",
			slog.String("rpc.procedure", spec.Procedure),
			slog.String("rpc.code", connect.CodeOf(err).String()),
			slog.Any("error", err),
		)
		return err
	}
}
