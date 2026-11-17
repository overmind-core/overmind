package overmind

import (
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/metric"

	"github.com/overmind/overmind/telemetry"
)

// Instruments come from the global providers, which delegate to whatever
// telemetry.Setup installs, so they can be created at package init.
var (
	tracer = otel.Tracer(telemetry.ScopeName)
	meter  = otel.Meter(telemetry.ScopeName)

	invitationsSent = must(meter.Int64Counter("invitation.sent",
		metric.WithDescription("Invitation emails sent, including resends."),
		metric.WithUnit("{invitation}")))
	invitationsAccepted = must(meter.Int64Counter("invitation.accepted",
		metric.WithDescription("Invitations accepted."),
		metric.WithUnit("{invitation}")))
	spansWritten = must(meter.Int64Counter("spans.written",
		metric.WithDescription("Spans written to project stores."),
		metric.WithUnit("{span}")))
	logsWritten = must(meter.Int64Counter("logs.written",
		metric.WithDescription("Log records written to project stores."),
		metric.WithUnit("{log}")))
	metricsWritten = must(meter.Int64Counter("metrics.written",
		metric.WithDescription("Metric data points written to project stores."),
		metric.WithUnit("{datapoint}")))
	profilesWritten = must(meter.Int64Counter("profiles.written",
		metric.WithDescription("Profile exports written to project stores."),
		metric.WithUnit("{profile}")))
	httpRequestDuration = must(meter.Float64Histogram("http.server.request.duration",
		metric.WithDescription("Duration of HTTP server requests."),
		metric.WithUnit("s")))
)

func must[T any](instrument T, err error) T {
	if err != nil {
		panic(err)
	}
	return instrument
}
