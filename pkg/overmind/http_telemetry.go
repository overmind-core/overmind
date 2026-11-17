package overmind

import (
	"strconv"
	"time"

	"ganigeorgiev.com/pocketbase/pocketbase/apis"
	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"ganigeorgiev.com/pocketbase/pocketbase/tools/hook"
	"ganigeorgiev.com/pocketbase/pocketbase/tools/router"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

// traceRequests runs ahead of PocketBase's own middlewares so the server span
// covers CORS, auth loading, rate limiting and the handler.
var traceRequests = &hook.Handler[*core.RequestEvent]{
	Id:       "overmindTraceRequests",
	Priority: apis.DefaultCorsMiddlewarePriority - 1,
	Func: func(e *core.RequestEvent) error {
		start := time.Now()
		req := e.Request
		route := req.Pattern
		if route == "" {
			route = req.Method
		}

		ctx := otel.GetTextMapPropagator().Extract(req.Context(), propagation.HeaderCarrier(req.Header))
		ctx, span := tracer.Start(ctx, route,
			trace.WithSpanKind(trace.SpanKindServer),
			trace.WithAttributes(
				attribute.String("http.request.method", req.Method),
				attribute.String("http.route", route),
				attribute.String("url.path", req.URL.Path),
			),
		)
		defer span.End()
		e.Request = req.WithContext(ctx)

		err := e.Next()

		status := e.Status()
		if err != nil {
			status = router.ToApiError(err).Status
			span.RecordError(err)
		}
		span.SetAttributes(attribute.Int("http.response.status_code", status))
		if status >= 500 {
			span.SetStatus(codes.Error, strconv.Itoa(status))
		}
		httpRequestDuration.Record(ctx, time.Since(start).Seconds(), metric.WithAttributes(
			attribute.String("http.request.method", req.Method),
			attribute.String("http.route", route),
			attribute.Int("http.response.status_code", status),
		))
		return err
	},
}
