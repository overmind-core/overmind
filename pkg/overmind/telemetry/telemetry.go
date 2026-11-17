// Package telemetry exports traces, metrics and logs over OTLP/HTTP.
//
// Exporters read the standard OTEL_EXPORTER_OTLP_* variables. Without
// OTEL_EXPORTER_OTLP_ENDPOINT the global OTEL providers stay no-op and logs go
// to stdout only.
package telemetry

import (
	"context"
	"errors"
	"log/slog"
	"os"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

const (
	ScopeName          = "github.com/overmind/overmind"
	defaultServiceName = "overmind"
)

// Setup installs the global logger, propagator and OTEL providers. The
// returned function flushes and stops the exporters.
func Setup(ctx context.Context) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))
	stdout := traceContextHandler{slog.NewJSONHandler(os.Stdout, nil)}

	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		slog.SetDefault(slog.New(stdout))
		return func(context.Context) error { return nil }, nil
	}

	var shutdowns []func(context.Context) error
	shutdown := func(ctx context.Context) error {
		var errs []error
		for _, fn := range shutdowns {
			errs = append(errs, fn(ctx))
		}
		return errors.Join(errs...)
	}
	fail := func(err error) (func(context.Context) error, error) {
		return nil, errors.Join(err, shutdown(ctx))
	}

	// WithFromEnv comes after the default so OTEL_SERVICE_NAME and
	// OTEL_RESOURCE_ATTRIBUTES override it.
	res, err := resource.New(ctx,
		resource.WithAttributes(attribute.String("service.name", defaultServiceName)),
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
		resource.WithHost(),
	)
	if err != nil {
		return fail(err)
	}

	traceExporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return fail(err)
	}
	tracerProvider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExporter),
		sdktrace.WithResource(res),
	)
	shutdowns = append(shutdowns, tracerProvider.Shutdown)

	metricExporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		return fail(err)
	}
	meterProvider := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExporter)),
		sdkmetric.WithResource(res),
	)
	shutdowns = append(shutdowns, meterProvider.Shutdown)

	logExporter, err := otlploghttp.New(ctx)
	if err != nil {
		return fail(err)
	}
	loggerProvider := sdklog.NewLoggerProvider(
		sdklog.WithProcessor(sdklog.NewBatchProcessor(logExporter)),
		sdklog.WithResource(res),
	)
	shutdowns = append(shutdowns, loggerProvider.Shutdown)

	otel.SetTracerProvider(tracerProvider)
	otel.SetMeterProvider(meterProvider)
	global.SetLoggerProvider(loggerProvider)
	slog.SetDefault(slog.New(slog.NewMultiHandler(
		stdout,
		otelslog.NewHandler(ScopeName, otelslog.WithLoggerProvider(loggerProvider)),
	)))

	return shutdown, nil
}

// Event records a domain event as a structured log line and as an event on
// the span in ctx.
func Event(ctx context.Context, name string, attrs ...attribute.KeyValue) {
	trace.SpanFromContext(ctx).AddEvent(name, trace.WithAttributes(attrs...))

	logAttrs := make([]slog.Attr, 0, len(attrs)+1)
	logAttrs = append(logAttrs, slog.String("event", name))
	for _, kv := range attrs {
		logAttrs = append(logAttrs, slog.Any(string(kv.Key), kv.Value.AsInterface()))
	}
	slog.LogAttrs(ctx, slog.LevelInfo, name, logAttrs...)
}

// traceContextHandler adds trace_id and span_id to stdout records so they can
// be joined with exported spans.
type traceContextHandler struct{ slog.Handler }

func (h traceContextHandler) Handle(ctx context.Context, record slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		record.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return h.Handler.Handle(ctx, record)
}

func (h traceContextHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceContextHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceContextHandler) WithGroup(name string) slog.Handler {
	return traceContextHandler{h.Handler.WithGroup(name)}
}
