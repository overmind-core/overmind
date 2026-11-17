package overmind

import (
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"

	"github.com/overmind/overmind/store"
	"github.com/overmind/overmind/telemetry"
)

func TestOTLPTracesStayInTheirProject(t *testing.T) {
	h := newHarness(t)
	alice := h.user("alice@overmind.test")
	bob := h.user("bob@overmind.test")
	alicesProject := h.createProject(alice, "alices")
	bobsProject := h.createProject(bob, "bobs")

	start := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)
	body := traceExport(t, start)
	status, _ := h.otlp(alice, alicesProject, "/api/v1/traces", otlpJSON, body)
	if status != http.StatusOK {
		t.Fatalf("status = %d", status)
	}

	status, _ = h.otlp(bob, alicesProject, "/api/v1/traces", otlpJSON, body)
	if status != http.StatusForbidden {
		t.Fatalf("bob status = %d, want 403", status)
	}

	projects := storeOf(h.app)
	bobs, err := projects.QuerySpans(t.Context(), bobsProject, store.SpanQuery{Limit: 10})
	if err != nil {
		t.Fatal(err)
	}
	if len(bobs) != 0 {
		t.Fatalf("bob's project has %d spans, want 0", len(bobs))
	}

	traceID := "0102030405060708090a0b0c0d0e0f10"
	spans, err := projects.QuerySpans(t.Context(), alicesProject, store.SpanQuery{TraceID: &traceID, Limit: 10})
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 3 {
		t.Fatalf("trace spans = %d, want 3", len(spans))
	}
	got := spans[1]
	if got.ParentSpanID == nil || *got.ParentSpanID != "0102030405060708" {
		t.Fatalf("parent = %v", got.ParentSpanID)
	}
	var attrs map[string]any
	if err := json.Unmarshal(got.Attributes, &attrs); err != nil {
		t.Fatal(err)
	}
	if attrs["llm.model"] != "gpt-5-mini" {
		t.Fatalf("attributes = %v", attrs)
	}
	if !got.StartTime.Equal(start.Add(time.Second)) {
		t.Fatalf("start_time = %v", got.StartTime)
	}
	if got.ScopeName != "test-scope" {
		t.Fatalf("scope = %q", got.ScopeName)
	}

	traces, err := projects.QueryTraces(t.Context(), alicesProject, store.TraceQuery{
		Range: store.TimeRange{Until: start.Add(time.Minute)},
		Limit: 10,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(traces) != 1 || traces[0].TraceID != traceID || traces[0].SpanCount != 3 {
		t.Fatalf("traces = %+v", traces)
	}
	if traces[0].EndTime == nil || !traces[0].EndTime.Equal(start.Add(3*time.Second)) {
		t.Fatalf("trace end = %v", traces[0].EndTime)
	}
}

func TestOTLPProtobufAndGzip(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")

	req := ptraceotlp.NewExportRequestFromTraces(oneSpan(time.Now().UTC(), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", ""))
	raw, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	var compressed bytes.Buffer
	gz := gzip.NewWriter(&compressed)
	if _, err := gz.Write(raw); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	status := h.otlpRaw(owner, projectID, "/api/v1/traces", otlpProtobuf, "gzip", compressed.Bytes())
	if status != http.StatusOK {
		t.Fatalf("status = %d", status)
	}
	spans, err := storeOf(h.app).QuerySpans(t.Context(), projectID, store.SpanQuery{Limit: 10})
	if err != nil {
		t.Fatal(err)
	}
	if len(spans) != 1 || spans[0].EndTime != nil {
		t.Fatalf("spans = %+v", spans)
	}
}

func TestOTLPLogsMetricsAndProfiles(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	start := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)

	logs := plog.NewLogs()
	record := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords().AppendEmpty()
	record.SetTimestamp(pcommon.NewTimestampFromTime(start))
	record.SetSeverityText("INFO")
	record.SetSeverityNumber(plog.SeverityNumberInfo)
	record.Body().SetStr("hello")
	record.SetTraceID(pcommon.TraceID{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16})
	logBody, err := plogotlp.NewExportRequestFromLogs(logs).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	if status, _ := h.otlp(owner, projectID, "/api/v1/logs", otlpJSON, logBody); status != http.StatusOK {
		t.Fatalf("logs status = %d", status)
	}
	stored, err := storeOf(h.app).QueryLogs(t.Context(), projectID, nil, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(stored) != 1 || stored[0].SeverityText != "INFO" || string(stored[0].Body) != `"hello"` {
		t.Fatalf("logs = %+v body %s", stored, stored[0].Body)
	}

	metrics := pmetric.NewMetrics()
	gauge := metrics.ResourceMetrics().AppendEmpty().ScopeMetrics().AppendEmpty().Metrics().AppendEmpty()
	gauge.SetName("tokens")
	gauge.SetUnit("{token}")
	point := gauge.SetEmptyGauge().DataPoints().AppendEmpty()
	point.SetTimestamp(pcommon.NewTimestampFromTime(start))
	point.SetIntValue(12)
	metricBody, err := pmetricotlp.NewExportRequestFromMetrics(metrics).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	if status, _ := h.otlp(owner, projectID, "/api/v1/metrics", otlpJSON, metricBody); status != http.StatusOK {
		t.Fatalf("metrics status = %d", status)
	}
	points, err := storeOf(h.app).QueryMetrics(t.Context(), projectID, "tokens", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(points) != 1 || points[0].Type != "Gauge" || points[0].Unit != "{token}" {
		t.Fatalf("metrics = %+v", points)
	}
	var data map[string]any
	if err := json.Unmarshal(points[0].Data, &data); err != nil {
		t.Fatal(err)
	}
	if data["value"] != float64(12) {
		t.Fatalf("value = %v", data["value"])
	}

	if status, _ := h.otlp(owner, projectID, "/api/v1/profiles", otlpJSON, []byte(`{}`)); status != http.StatusOK {
		t.Fatalf("profiles status = %d", status)
	}
	n, err := storeOf(h.app).CountProfiles(t.Context(), projectID)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("profiles = %d", n)
	}
}

func TestOTLPRejectsBadRequests(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	body := []byte(`{}`)

	if status, _ := h.otlp(nil, projectID, "/api/v1/traces", otlpJSON, body); status != http.StatusUnauthorized {
		t.Fatalf("anonymous status = %d", status)
	}
	if status := h.otlpRaw(owner, "", "/api/v1/logs", otlpJSON, "", body); status != http.StatusBadRequest {
		t.Fatalf("missing project status = %d", status)
	}
	if status := h.otlpRaw(owner, projectID, "/api/v1/metrics", "text/plain", "", body); status != http.StatusUnsupportedMediaType {
		t.Fatalf("content type status = %d", status)
	}
	if status, _ := h.otlp(owner, projectID, "/api/v1/traces", otlpJSON, []byte(`{"resourceSpans":`)); status != http.StatusBadRequest {
		t.Fatalf("bad json status = %d", status)
	}
}

func TestOTLPBoardListsStoredSignals(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	start := time.Date(2026, 11, 17, 12, 0, 0, 0, time.UTC)

	traceBody := traceExport(t, start)
	if status, _ := h.otlp(owner, projectID, "/api/v1/traces", otlpJSON, traceBody); status != http.StatusOK {
		t.Fatalf("traces status = %d", status)
	}
	logs := plog.NewLogs()
	record := logs.ResourceLogs().AppendEmpty().ScopeLogs().AppendEmpty().LogRecords().AppendEmpty()
	record.SetTimestamp(pcommon.NewTimestampFromTime(start))
	record.SetSeverityText("INFO")
	record.Body().SetStr("hello from the page")
	logBody, err := plogotlp.NewExportRequestFromLogs(logs).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	if status, _ := h.otlp(owner, projectID, "/api/v1/logs", otlpJSON, logBody); status != http.StatusOK {
		t.Fatalf("logs status = %d", status)
	}

	req, err := http.NewRequest(http.MethodGet, h.server.URL+"/otel/board", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", h.token(owner))
	req.Header.Set(ProjectHeader, projectID)
	res, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(res.Body)
	if err != nil {
		t.Fatal(err)
	}
	if res.StatusCode != http.StatusOK {
		t.Fatalf("board status = %d body %s", res.StatusCode, raw)
	}
	body := string(raw)
	if !strings.Contains(body, "call 0102030405060708") || !strings.Contains(body, "hello from the page") {
		t.Fatalf("board = %s", body)
	}
}

func TestCallsSucceedWithoutAnOTLPEndpoint(t *testing.T) {
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
	shutdown, err := telemetry.Setup(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = shutdown(t.Context()) })

	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	h.invite(owner, projectID, "new@overmind.test")
	body, err := ptraceotlp.NewExportRequestFromTraces(oneSpan(time.Now().UTC(), "cccccccccccccccccccccccccccccccc", "dddddddddddddddd", "")).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	if status, _ := h.otlp(owner, projectID, "/api/v1/traces", otlpJSON, body); status != http.StatusOK {
		t.Fatalf("status = %d", status)
	}
}

func (h *harness) otlp(user *core.Record, projectID, path, contentType string, body []byte) (int, []byte) {
	h.t.Helper()
	req, err := http.NewRequest(http.MethodPost, h.server.URL+path, bytes.NewReader(body))
	if err != nil {
		h.t.Fatal(err)
	}
	req.Header.Set("Content-Type", contentType)
	if projectID != "" {
		req.Header.Set(ProjectHeader, projectID)
	}
	if user != nil {
		req.Header.Set("Authorization", h.token(user))
	}
	res, err := h.server.Client().Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer res.Body.Close()
	raw, err := ioReadAll(res)
	if err != nil {
		h.t.Fatal(err)
	}
	return res.StatusCode, raw
}

func (h *harness) otlpRaw(user *core.Record, projectID, path, contentType, encoding string, body []byte) int {
	h.t.Helper()
	req, err := http.NewRequest(http.MethodPost, h.server.URL+path, bytes.NewReader(body))
	if err != nil {
		h.t.Fatal(err)
	}
	req.Header.Set("Content-Type", contentType)
	if encoding != "" {
		req.Header.Set("Content-Encoding", encoding)
	}
	if projectID != "" {
		req.Header.Set(ProjectHeader, projectID)
	}
	if user != nil {
		req.Header.Set("Authorization", h.token(user))
	}
	res, err := h.server.Client().Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer res.Body.Close()
	_, _ = ioReadAll(res)
	return res.StatusCode
}

func ioReadAll(res *http.Response) ([]byte, error) {
	return io.ReadAll(res.Body)
}

func traceExport(t *testing.T, start time.Time) []byte {
	t.Helper()
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "api")
	ss := rs.ScopeSpans().AppendEmpty()
	ss.Scope().SetName("test-scope")
	root := "0102030405060708"
	addSpan(ss.Spans(), start, "0102030405060708090a0b0c0d0e0f10", root, "")
	addSpan(ss.Spans(), start.Add(time.Second), "0102030405060708090a0b0c0d0e0f10", "1111111111111111", root)
	addSpan(ss.Spans(), start.Add(2*time.Second), "0102030405060708090a0b0c0d0e0f10", "2222222222222222", root)
	later := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans()
	addSpan(later, start.Add(time.Hour), "ffffffffffffffffffffffffffffffff", "3333333333333333", "")
	raw, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func oneSpan(start time.Time, traceID, spanID, parent string) ptrace.Traces {
	td := ptrace.NewTraces()
	ss := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
	addSpan(ss.Spans(), start, traceID, spanID, parent)
	ss.Spans().At(0).SetEndTimestamp(0)
	return td
}

func addSpan(spans ptrace.SpanSlice, start time.Time, traceID, spanID, parent string) {
	sp := spans.AppendEmpty()
	var trace pcommon.TraceID
	var span pcommon.SpanID
	if _, err := hex.Decode(trace[:], []byte(traceID)); err != nil {
		panic(err)
	}
	if _, err := hex.Decode(span[:], []byte(spanID)); err != nil {
		panic(err)
	}
	sp.SetTraceID(trace)
	sp.SetSpanID(span)
	if parent != "" {
		var id pcommon.SpanID
		if _, err := hex.Decode(id[:], []byte(parent)); err != nil {
			panic(err)
		}
		sp.SetParentSpanID(id)
	}
	sp.SetName("call " + spanID)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(start))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(start.Add(time.Second)))
	sp.Attributes().PutStr("llm.model", "gpt-5-mini")
	sp.Attributes().PutInt("tokens", 12)
}
