package overmind

import (
	"context"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"connectrpc.com/connect/v2"
	"connectrpc.com/otelconnect"
	"ganigeorgiev.com/pocketbase/pocketbase"
	"ganigeorgiev.com/pocketbase/pocketbase/apis"
	"ganigeorgiev.com/pocketbase/pocketbase/core"

	"github.com/overmind/gen/overmind/v1/overmindv1connect"
	_ "github.com/overmind/overmind/migrations"
	"github.com/overmind/overmind/store"
	"github.com/overmind/overmind/telemetry"
)

const defaultAppURL = "http://127.0.0.1:8090"

var projectStores sync.Map

func storeOf(app core.App) *store.Store {
	value, ok := projectStores.Load(app)
	if !ok {
		return nil
	}
	return value.(*store.Store)
}

type Config struct {
	// AppURL is the public origin used in invitation links.
	AppURL string
}

func Run() {
	shutdownTelemetry, err := telemetry.Setup(context.Background())
	if err != nil {
		slog.Error("telemetry setup failed", slog.Any("error", err))
		os.Exit(1)
	}

	appURL := os.Getenv("APP_URL")
	if appURL == "" {
		appURL = defaultAppURL
	}

	app := pocketbase.New()
	if err := Register(app, Config{AppURL: appURL}); err != nil {
		slog.Error("register app", slog.Any("error", err))
		os.Exit(1)
	}
	app.OnTerminate().BindFunc(func(e *core.TerminateEvent) error {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := shutdownTelemetry(ctx); err != nil {
			slog.Error("telemetry shutdown failed", slog.Any("error", err))
		}
		return e.Next()
	})

	if err := app.Start(); err != nil {
		slog.Error("app stopped", slog.Any("error", err))
		os.Exit(1)
	}
}

// Register wires projects, invitations, the per-project stores, the OTLP
// collector endpoints and the invitation RPC into app.
func Register(app core.App, cfg Config) error {
	projects := store.New(filepath.Join(app.DataDir(), "projects"))
	projectStores.Store(app, projects)
	bindProjectHooks(app, projects)

	otelInterceptor, err := otelconnect.NewServerInterceptor()
	if err != nil {
		return err
	}
	server := connect.NewServer(otelInterceptor, logRPCErrors)
	overmindv1connect.RegisterInvitationServiceHandler(server, &invitationService{app: app, appURL: cfg.AppURL})

	app.OnServe().BindFunc(func(se *core.ServeEvent) error {
		se.Router.Bind(traceRequests)
		otlpHandler := &otlpHandler{app: app, store: projects}
		otlp := http.StripPrefix("/api", otlpHandler.Handler())
		se.Router.POST("/api/v1/{signal}", func(e *core.RequestEvent) error {
			otlp.ServeHTTP(e.Response, e.Request)
			return nil
		})
		se.Router.GET("/otel/projects", func(e *core.RequestEvent) error {
			otlpHandler.projects(e.Response, e.Request)
			return nil
		})
		se.Router.GET("/otel/board", func(e *core.RequestEvent) error {
			otlpHandler.board(e.Response, e.Request)
			return nil
		})
		mountRPC(se.Router, server)
		public := os.DirFS("./pb_public")
		se.Router.GET("/otel", func(e *core.RequestEvent) error {
			return htmlFile(e, public, "otel.html")
		})
		se.Router.GET("/accept-invite", func(e *core.RequestEvent) error {
			return htmlFile(e, public, "accept-invite.html")
		})
		se.Router.GET("/{path...}", apis.Static(os.DirFS("./pb_public"), false))
		return se.Next()
	})

	app.Cron().MustAdd("expire_invitations", "*/15 * * * *", func() {
		expireInvitationsJob(app)
	})

	app.OnTerminate().BindFunc(func(e *core.TerminateEvent) error {
		if err := projects.Close(); err != nil {
			slog.Error("close project stores", slog.Any("error", err))
		}
		return e.Next()
	})
	return nil
}

func htmlFile(e *core.RequestEvent, fsys fs.FS, name string) error {
	body, err := fs.ReadFile(fsys, name)
	if err != nil {
		return e.NotFoundError("", err)
	}
	return e.HTML(http.StatusOK, string(body))
}
