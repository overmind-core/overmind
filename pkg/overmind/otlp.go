package overmind

import (
	"compress/gzip"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"

	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/pprofile/pprofileotlp"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"go.opentelemetry.io/otel/attribute"

	"github.com/overmind/overmind/store"
	"github.com/overmind/overmind/telemetry"
)

const (
	otlpJSON     = "application/json"
	otlpProtobuf = "application/x-protobuf"
	// ProjectHeader selects the DuckDB file for an OTLP export. Set it with
	// OTEL_EXPORTER_OTLP_HEADERS; the caller must be a member of that project.
	ProjectHeader = "X-Overmind-Project"
	maxOTLPBody   = 32 << 20
)

// Handler is the OTLP/HTTP collector surface. Paths are /v1/traces, /v1/metrics,
// /v1/logs and /v1/profiles; mount it under /api.
func (h *otlpHandler) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/traces", h.traces)
	mux.HandleFunc("POST /v1/metrics", h.metrics)
	mux.HandleFunc("POST /v1/logs", h.logs)
	mux.HandleFunc("POST /v1/profiles", h.profiles)
	return mux
}

type otlpHandler struct {
	app   core.App
	store *store.Store
}

func (h *otlpHandler) traces(w http.ResponseWriter, r *http.Request) {
	projectID, body, enc, ok := h.begin(w, r)
	if !ok {
		return
	}
	req := ptraceotlp.NewExportRequest()
	if err := unmarshal(enc, body, req.UnmarshalProto, req.UnmarshalJSON); err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, "invalid traces payload")
		return
	}
	spans, err := spansFromTraces(req.Traces())
	if err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, err.Error())
		return
	}
	n, err := h.store.WriteSpans(r.Context(), projectID, spans)
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to store traces")
		return
	}
	if n > 0 {
		spansWritten.Add(r.Context(), int64(n))
		telemetry.Event(r.Context(), "spans.written",
			attribute.String("project_id", projectID), attribute.Int("count", n))
	}
	writeExport(w, enc, func() ([]byte, error) { return ptraceotlp.NewExportResponse().MarshalProto() }, func() ([]byte, error) {
		return ptraceotlp.NewExportResponse().MarshalJSON()
	})
}

func (h *otlpHandler) metrics(w http.ResponseWriter, r *http.Request) {
	projectID, body, enc, ok := h.begin(w, r)
	if !ok {
		return
	}
	req := pmetricotlp.NewExportRequest()
	if err := unmarshal(enc, body, req.UnmarshalProto, req.UnmarshalJSON); err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, "invalid metrics payload")
		return
	}
	points, err := metricsFrom(req.Metrics())
	if err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, err.Error())
		return
	}
	n, err := h.store.WriteMetrics(r.Context(), projectID, points)
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to store metrics")
		return
	}
	if n > 0 {
		metricsWritten.Add(r.Context(), int64(n))
		telemetry.Event(r.Context(), "metrics.written",
			attribute.String("project_id", projectID), attribute.Int("count", n))
	}
	writeExport(w, enc, func() ([]byte, error) { return pmetricotlp.NewExportResponse().MarshalProto() }, func() ([]byte, error) {
		return pmetricotlp.NewExportResponse().MarshalJSON()
	})
}

func (h *otlpHandler) logs(w http.ResponseWriter, r *http.Request) {
	projectID, body, enc, ok := h.begin(w, r)
	if !ok {
		return
	}
	req := plogotlp.NewExportRequest()
	if err := unmarshal(enc, body, req.UnmarshalProto, req.UnmarshalJSON); err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, "invalid logs payload")
		return
	}
	records, err := logsFrom(req.Logs())
	if err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, err.Error())
		return
	}
	n, err := h.store.WriteLogs(r.Context(), projectID, records)
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to store logs")
		return
	}
	if n > 0 {
		logsWritten.Add(r.Context(), int64(n))
		telemetry.Event(r.Context(), "logs.written",
			attribute.String("project_id", projectID), attribute.Int("count", n))
	}
	writeExport(w, enc, func() ([]byte, error) { return plogotlp.NewExportResponse().MarshalProto() }, func() ([]byte, error) {
		return plogotlp.NewExportResponse().MarshalJSON()
	})
}

func (h *otlpHandler) profiles(w http.ResponseWriter, r *http.Request) {
	if err := pprofileotlp.ValidateDevelopmentVersion(r.Header.Values(pprofileotlp.DevelopmentVersionHeader)); err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, err.Error())
		return
	}
	projectID, body, enc, ok := h.begin(w, r)
	if !ok {
		return
	}
	req := pprofileotlp.NewExportRequest()
	if err := unmarshal(enc, body, req.UnmarshalProto, req.UnmarshalJSON); err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, "invalid profiles payload")
		return
	}
	raw, err := req.MarshalJSON()
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to store profiles")
		return
	}
	n, err := h.store.WriteProfiles(r.Context(), projectID, []store.Profile{{
		ID:         newID(),
		ReceivedAt: time.Now().UTC(),
		Data:       raw,
	}})
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to store profiles")
		return
	}
	profilesWritten.Add(r.Context(), int64(n))
	telemetry.Event(r.Context(), "profiles.written",
		attribute.String("project_id", projectID), attribute.Int("count", n))
	writeExport(w, enc, func() ([]byte, error) { return pprofileotlp.NewExportResponse().MarshalProto() }, func() ([]byte, error) {
		return pprofileotlp.NewExportResponse().MarshalJSON()
	})
}

// begin checks membership and reads the body. ok is false when the response
// has already been written.
func (h *otlpHandler) begin(w http.ResponseWriter, r *http.Request) (projectID string, body []byte, enc string, ok bool) {
	user, err := h.user(r)
	if err != nil {
		writeOTLPStatus(w, http.StatusUnauthorized, 16, "sign in as a user")
		return "", nil, "", false
	}
	projectID = r.Header.Get(ProjectHeader)
	if projectID == "" {
		writeOTLPStatus(w, http.StatusBadRequest, 3, ProjectHeader+" is required")
		return "", nil, "", false
	}
	if _, err := findMembership(h.app, user.Id, projectID); err != nil {
		if errors.Is(err, errNotFound) {
			writeOTLPStatus(w, http.StatusForbidden, 7, "not a member of this project")
		} else {
			writeOTLPStatus(w, http.StatusInternalServerError, 13, "internal error")
		}
		return "", nil, "", false
	}
	enc, ok = otlpContentType(r)
	if !ok {
		writeOTLPStatus(w, http.StatusUnsupportedMediaType, 3, "unsupported content type")
		return "", nil, "", false
	}
	body, err = readOTLPBody(r)
	if err != nil {
		writeOTLPStatus(w, http.StatusBadRequest, 3, err.Error())
		return "", nil, "", false
	}
	return projectID, body, enc, true
}

func (h *otlpHandler) user(r *http.Request) (*core.Record, error) {
	token := r.Header.Get("Authorization")
	if len(token) > 7 && strings.EqualFold(token[:7], "Bearer ") {
		token = token[7:]
	}
	if token == "" {
		return nil, errors.New("missing token")
	}
	record, err := h.app.FindAuthRecordByToken(token, core.TokenTypeAuth)
	if err != nil || record == nil || record.Collection().Name != usersCollection {
		return nil, errors.New("invalid token")
	}
	return record, nil
}

func otlpContentType(r *http.Request) (string, bool) {
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil {
		return "", false
	}
	switch mediaType {
	case otlpJSON, otlpProtobuf:
		return mediaType, true
	default:
		return "", false
	}
}

func readOTLPBody(r *http.Request) ([]byte, error) {
	defer r.Body.Close()
	limited := io.LimitReader(r.Body, maxOTLPBody+1)
	var reader io.Reader = limited
	if r.Header.Get("Content-Encoding") == "gzip" {
		gz, err := gzip.NewReader(limited)
		if err != nil {
			return nil, fmt.Errorf("gzip: %w", err)
		}
		defer gz.Close()
		reader = gz
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		return nil, err
	}
	if len(body) > maxOTLPBody {
		return nil, errors.New("payload is too large")
	}
	return body, nil
}

func unmarshal(enc string, body []byte, protoFn, jsonFn func([]byte) error) error {
	if enc == otlpProtobuf {
		return protoFn(body)
	}
	return jsonFn(body)
}

func writeExport(w http.ResponseWriter, enc string, protoFn, jsonFn func() ([]byte, error)) {
	var (
		body []byte
		err  error
	)
	if enc == otlpProtobuf {
		body, err = protoFn()
	} else {
		body, err = jsonFn()
	}
	if err != nil {
		writeOTLPStatus(w, http.StatusInternalServerError, 13, "failed to encode response")
		return
	}
	w.Header().Set("Content-Type", enc)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

// writeOTLPStatus writes a google.rpc.Status JSON body. code is the gRPC code.
func writeOTLPStatus(w http.ResponseWriter, httpStatus, code int, message string) {
	w.Header().Set("Content-Type", otlpJSON)
	w.WriteHeader(httpStatus)
	_ = json.NewEncoder(w).Encode(map[string]any{"code": code, "message": message})
}

func spansFromTraces(td ptrace.Traces) ([]store.Span, error) {
	var spans []store.Span
	resources := td.ResourceSpans()
	for i := 0; i < resources.Len(); i++ {
		rs := resources.At(i)
		resource, err := jsonRaw(rs.Resource().Attributes().AsRaw())
		if err != nil {
			return nil, err
		}
		scopes := rs.ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			ss := scopes.At(j)
			scope := ss.Scope()
			items := ss.Spans()
			for k := 0; k < items.Len(); k++ {
				sp := items.At(k)
				if sp.SpanID().IsEmpty() || sp.TraceID().IsEmpty() {
					return nil, errors.New("span_id and trace_id are required")
				}
				attrs, err := jsonRaw(sp.Attributes().AsRaw())
				if err != nil {
					return nil, err
				}
				out := store.Span{
					SpanID:             sp.SpanID().String(),
					TraceID:            sp.TraceID().String(),
					Name:               sp.Name(),
					StartTime:          sp.StartTimestamp().AsTime(),
					Attributes:         attrs,
					ResourceAttributes: resource,
					ScopeName:          scope.Name(),
					ScopeVersion:       scope.Version(),
				}
				if !sp.ParentSpanID().IsEmpty() {
					parent := sp.ParentSpanID().String()
					out.ParentSpanID = &parent
				}
				if end := otelTime(sp.EndTimestamp()); end != nil {
					out.EndTime = end
				}
				if code := sp.Status().Code(); code != ptrace.StatusCodeUnset {
					status := code.String()
					out.StatusCode = &status
				}
				spans = append(spans, out)
			}
		}
	}
	return spans, nil
}

func logsFrom(ld plog.Logs) ([]store.Log, error) {
	var logs []store.Log
	resources := ld.ResourceLogs()
	for i := 0; i < resources.Len(); i++ {
		rl := resources.At(i)
		resource, err := jsonRaw(rl.Resource().Attributes().AsRaw())
		if err != nil {
			return nil, err
		}
		scopes := rl.ScopeLogs()
		for j := 0; j < scopes.Len(); j++ {
			sl := scopes.At(j)
			records := sl.LogRecords()
			for k := 0; k < records.Len(); k++ {
				rec := records.At(k)
				attrs, err := jsonRaw(rec.Attributes().AsRaw())
				if err != nil {
					return nil, err
				}
				body, err := jsonRaw(rec.Body().AsRaw())
				if err != nil {
					return nil, err
				}
				log := store.Log{
					ID:                 newID(),
					Timestamp:          otelTime(rec.Timestamp()),
					ObservedTimestamp:  otelTime(rec.ObservedTimestamp()),
					SeverityNumber:     int32(rec.SeverityNumber()),
					SeverityText:       rec.SeverityText(),
					Body:               body,
					Attributes:         attrs,
					ResourceAttributes: resource,
					ScopeName:          sl.Scope().Name(),
				}
				if !rec.TraceID().IsEmpty() {
					id := rec.TraceID().String()
					log.TraceID = &id
				}
				if !rec.SpanID().IsEmpty() {
					id := rec.SpanID().String()
					log.SpanID = &id
				}
				logs = append(logs, log)
			}
		}
	}
	return logs, nil
}

func metricsFrom(md pmetric.Metrics) ([]store.Metric, error) {
	var points []store.Metric
	resources := md.ResourceMetrics()
	for i := 0; i < resources.Len(); i++ {
		rm := resources.At(i)
		resource, err := jsonRaw(rm.Resource().Attributes().AsRaw())
		if err != nil {
			return nil, err
		}
		scopes := rm.ScopeMetrics()
		for j := 0; j < scopes.Len(); j++ {
			sm := scopes.At(j)
			metrics := sm.Metrics()
			for k := 0; k < metrics.Len(); k++ {
				metric := metrics.At(k)
				converted, err := metricPoints(metric, resource, sm.Scope().Name())
				if err != nil {
					return nil, err
				}
				points = append(points, converted...)
			}
		}
	}
	return points, nil
}

func metricPoints(metric pmetric.Metric, resource []byte, scope string) ([]store.Metric, error) {
	base := store.Metric{
		Name:               metric.Name(),
		Description:        metric.Description(),
		Unit:               metric.Unit(),
		Type:               metric.Type().String(),
		ResourceAttributes: resource,
		ScopeName:          scope,
	}
	switch metric.Type() {
	case pmetric.MetricTypeGauge:
		return numberPoints(base, metric.Gauge().DataPoints(), nil)
	case pmetric.MetricTypeSum:
		sum := metric.Sum()
		extra := map[string]any{
			"aggregationTemporality": sum.AggregationTemporality().String(),
			"isMonotonic":            sum.IsMonotonic(),
		}
		return numberPoints(base, sum.DataPoints(), extra)
	case pmetric.MetricTypeHistogram:
		return histogramPoints(base, metric.Histogram())
	case pmetric.MetricTypeExponentialHistogram:
		return exponentialPoints(base, metric.ExponentialHistogram())
	case pmetric.MetricTypeSummary:
		return summaryPoints(base, metric.Summary())
	default:
		return nil, nil
	}
}

func numberPoints(base store.Metric, points pmetric.NumberDataPointSlice, extra map[string]any) ([]store.Metric, error) {
	out := make([]store.Metric, 0, points.Len())
	for i := 0; i < points.Len(); i++ {
		dp := points.At(i)
		data := map[string]any{}
		for k, v := range extra {
			data[k] = v
		}
		switch dp.ValueType() {
		case pmetric.NumberDataPointValueTypeInt:
			data["value"] = dp.IntValue()
		case pmetric.NumberDataPointValueTypeDouble:
			data["value"] = dp.DoubleValue()
		}
		point, err := pointFrom(base, dp.Timestamp(), dp.Attributes(), data)
		if err != nil {
			return nil, err
		}
		out = append(out, point)
	}
	return out, nil
}

func histogramPoints(base store.Metric, histogram pmetric.Histogram) ([]store.Metric, error) {
	extra := map[string]any{"aggregationTemporality": histogram.AggregationTemporality().String()}
	points := histogram.DataPoints()
	out := make([]store.Metric, 0, points.Len())
	for i := 0; i < points.Len(); i++ {
		dp := points.At(i)
		data := map[string]any{
			"count":          dp.Count(),
			"bucketCounts":   uintSlice(dp.BucketCounts()),
			"explicitBounds": floatSlice(dp.ExplicitBounds()),
		}
		for k, v := range extra {
			data[k] = v
		}
		if dp.HasSum() {
			data["sum"] = dp.Sum()
		}
		point, err := pointFrom(base, dp.Timestamp(), dp.Attributes(), data)
		if err != nil {
			return nil, err
		}
		out = append(out, point)
	}
	return out, nil
}

func exponentialPoints(base store.Metric, histogram pmetric.ExponentialHistogram) ([]store.Metric, error) {
	points := histogram.DataPoints()
	out := make([]store.Metric, 0, points.Len())
	for i := 0; i < points.Len(); i++ {
		dp := points.At(i)
		data := map[string]any{
			"aggregationTemporality": histogram.AggregationTemporality().String(),
			"count":                  dp.Count(),
			"scale":                  dp.Scale(),
			"zeroCount":              dp.ZeroCount(),
			"positive": map[string]any{
				"offset":       dp.Positive().Offset(),
				"bucketCounts": uintSlice(dp.Positive().BucketCounts()),
			},
			"negative": map[string]any{
				"offset":       dp.Negative().Offset(),
				"bucketCounts": uintSlice(dp.Negative().BucketCounts()),
			},
		}
		if dp.HasSum() {
			data["sum"] = dp.Sum()
		}
		point, err := pointFrom(base, dp.Timestamp(), dp.Attributes(), data)
		if err != nil {
			return nil, err
		}
		out = append(out, point)
	}
	return out, nil
}

func summaryPoints(base store.Metric, summary pmetric.Summary) ([]store.Metric, error) {
	points := summary.DataPoints()
	out := make([]store.Metric, 0, points.Len())
	for i := 0; i < points.Len(); i++ {
		dp := points.At(i)
		quantiles := dp.QuantileValues()
		values := make([]map[string]any, 0, quantiles.Len())
		for q := 0; q < quantiles.Len(); q++ {
			qv := quantiles.At(q)
			values = append(values, map[string]any{"quantile": qv.Quantile(), "value": qv.Value()})
		}
		point, err := pointFrom(base, dp.Timestamp(), dp.Attributes(), map[string]any{
			"count":          dp.Count(),
			"sum":            dp.Sum(),
			"quantileValues": values,
		})
		if err != nil {
			return nil, err
		}
		out = append(out, point)
	}
	return out, nil
}

func pointFrom(base store.Metric, ts pcommon.Timestamp, attrs pcommon.Map, data map[string]any) (store.Metric, error) {
	rawAttrs, err := jsonRaw(attrs.AsRaw())
	if err != nil {
		return store.Metric{}, err
	}
	rawData, err := jsonRaw(data)
	if err != nil {
		return store.Metric{}, err
	}
	base.ID = newID()
	base.Timestamp = otelTime(ts)
	base.Attributes = rawAttrs
	base.Data = rawData
	return base, nil
}

func uintSlice(values pcommon.UInt64Slice) []uint64 {
	out := make([]uint64, values.Len())
	for i := 0; i < values.Len(); i++ {
		out[i] = values.At(i)
	}
	return out
}

func floatSlice(values pcommon.Float64Slice) []float64 {
	out := make([]float64, values.Len())
	for i := 0; i < values.Len(); i++ {
		out[i] = values.At(i)
	}
	return out
}

func jsonRaw(v any) ([]byte, error) {
	if v == nil {
		return nil, nil
	}
	return json.Marshal(v)
}

func otelTime(ts pcommon.Timestamp) *time.Time {
	if ts == 0 {
		return nil
	}
	t := ts.AsTime()
	return &t
}

func newID() string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return hex.EncodeToString([]byte(time.Now().UTC().Format(time.RFC3339Nano)))
	}
	return hex.EncodeToString(raw[:])
}
