// Package store keeps each project's spans, traces and other telemetry
// resources in its own DuckDB file.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	_ "github.com/duckdb/duckdb-go/v2"
)

var ErrNotFound = errors.New("project store not found")

// Project ids become file names, so they are restricted to PocketBase's id
// alphabet.
var projectIDPattern = regexp.MustCompile(`^[a-z0-9]+$`)

const schema = `
CREATE TABLE IF NOT EXISTS spans (
	span_id VARCHAR PRIMARY KEY,
	trace_id VARCHAR NOT NULL,
	parent_span_id VARCHAR,
	name VARCHAR NOT NULL,
	start_time TIMESTAMPTZ NOT NULL,
	end_time TIMESTAMPTZ,
	status_code VARCHAR,
	attributes JSON,
	resource_attributes JSON
);
ALTER TABLE spans ADD COLUMN IF NOT EXISTS scope_name VARCHAR;
ALTER TABLE spans ADD COLUMN IF NOT EXISTS scope_version VARCHAR;
CREATE OR REPLACE VIEW traces AS
SELECT trace_id, min(start_time) AS start_time, max(end_time) AS end_time, count(*) AS span_count
FROM spans GROUP BY trace_id;
CREATE TABLE IF NOT EXISTS logs (
	log_id VARCHAR PRIMARY KEY,
	timestamp TIMESTAMPTZ,
	observed_timestamp TIMESTAMPTZ,
	severity_number INTEGER,
	severity_text VARCHAR,
	body JSON,
	trace_id VARCHAR,
	span_id VARCHAR,
	attributes JSON,
	resource_attributes JSON,
	scope_name VARCHAR
);
CREATE TABLE IF NOT EXISTS metrics (
	metric_id VARCHAR PRIMARY KEY,
	name VARCHAR NOT NULL,
	description VARCHAR,
	unit VARCHAR,
	type VARCHAR NOT NULL,
	timestamp TIMESTAMPTZ,
	attributes JSON,
	resource_attributes JSON,
	scope_name VARCHAR,
	data JSON
);
CREATE TABLE IF NOT EXISTS profiles (
	profile_id VARCHAR PRIMARY KEY,
	received_at TIMESTAMPTZ NOT NULL,
	data JSON NOT NULL
);
`

type Store struct {
	dir string

	mu  sync.Mutex
	dbs map[string]*sql.DB
}

func New(dir string) *Store {
	return &Store{dir: dir, dbs: map[string]*sql.DB{}}
}

func (s *Store) Path(projectID string) string {
	return filepath.Join(s.dir, projectID+".duckdb")
}

// Create makes the project's DuckDB file and applies the schema.
func (s *Store) Create(ctx context.Context, projectID string) error {
	if err := os.MkdirAll(s.dir, 0o755); err != nil {
		return err
	}
	_, err := s.open(ctx, projectID, true)
	return err
}

// Remove closes the project's database and deletes its files.
func (s *Store) Remove(projectID string) error {
	if !projectIDPattern.MatchString(projectID) {
		return fmt.Errorf("invalid project id %q", projectID)
	}
	s.mu.Lock()
	db := s.dbs[projectID]
	delete(s.dbs, projectID)
	s.mu.Unlock()

	var errs []error
	if db != nil {
		errs = append(errs, db.Close())
	}
	path := s.Path(projectID)
	for _, p := range []string{path, path + ".wal"} {
		if err := os.Remove(p); err != nil && !errors.Is(err, os.ErrNotExist) {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var errs []error
	for id, db := range s.dbs {
		errs = append(errs, db.Close())
		delete(s.dbs, id)
	}
	return errors.Join(errs...)
}

func (s *Store) db(ctx context.Context, projectID string) (*sql.DB, error) {
	return s.open(ctx, projectID, false)
}

func (s *Store) open(ctx context.Context, projectID string, create bool) (*sql.DB, error) {
	if !projectIDPattern.MatchString(projectID) {
		return nil, fmt.Errorf("invalid project id %q", projectID)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if db, ok := s.dbs[projectID]; ok {
		return db, nil
	}

	path := s.Path(projectID)
	if !create {
		if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
			return nil, ErrNotFound
		} else if err != nil {
			return nil, err
		}
	}

	db, err := sql.Open("duckdb", path)
	if err != nil {
		return nil, err
	}
	if _, err := db.ExecContext(ctx, schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply schema to %s: %w", path, err)
	}
	s.dbs[projectID] = db
	return db, nil
}

type Span struct {
	SpanID       string
	TraceID      string
	ParentSpanID *string
	Name         string
	StartTime    time.Time
	EndTime      *time.Time
	StatusCode   *string
	// JSON objects; nil is stored as NULL.
	Attributes         []byte
	ResourceAttributes []byte
	ScopeName          string
	ScopeVersion       string
}

// Log is one OTLP log record.
type Log struct {
	ID                 string
	Timestamp          *time.Time
	ObservedTimestamp  *time.Time
	SeverityNumber     int32
	SeverityText       string
	Body               []byte
	TraceID            *string
	SpanID             *string
	Attributes         []byte
	ResourceAttributes []byte
	ScopeName          string
}

// Metric is one OTLP metric data point. Data is the point payload as JSON.
type Metric struct {
	ID                 string
	Name               string
	Description        string
	Unit               string
	Type               string
	Timestamp          *time.Time
	Attributes         []byte
	ResourceAttributes []byte
	ScopeName          string
	Data               []byte
}

// Profile is one ingested OTLP profiles request, kept as JSON.
type Profile struct {
	ID         string
	ReceivedAt time.Time
	Data       []byte
}

type Trace struct {
	TraceID   string
	StartTime time.Time
	EndTime   *time.Time
	SpanCount int64
}

// TimeRange bounds start times: From is inclusive, Until is exclusive. A zero
// value leaves that side open.
type TimeRange struct {
	From  time.Time
	Until time.Time
}

func (r TimeRange) where(column string, conds []string, args []any) ([]string, []any) {
	if !r.From.IsZero() {
		conds = append(conds, column+" >= ?")
		args = append(args, r.From)
	}
	if !r.Until.IsZero() {
		conds = append(conds, column+" < ?")
		args = append(args, r.Until)
	}
	return conds, args
}

type SpanQuery struct {
	TraceID *string
	Range   TimeRange
	Limit   int
}

type TraceQuery struct {
	Range TimeRange
	Limit int
}

// WriteSpans upserts spans by span_id in one transaction.
func (s *Store) WriteSpans(ctx context.Context, projectID string, spans []Span) (int, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return 0, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()

	stmt, err := tx.PrepareContext(ctx, `
		INSERT OR REPLACE INTO spans
			(span_id, trace_id, parent_span_id, name, start_time, end_time, status_code, attributes, resource_attributes, scope_name, scope_version)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
	if err != nil {
		return 0, err
	}
	defer stmt.Close()

	for _, sp := range spans {
		if _, err := stmt.ExecContext(ctx,
			sp.SpanID, sp.TraceID, sp.ParentSpanID, sp.Name, sp.StartTime, sp.EndTime, sp.StatusCode,
			jsonParam(sp.Attributes), jsonParam(sp.ResourceAttributes), sp.ScopeName, sp.ScopeVersion,
		); err != nil {
			return 0, fmt.Errorf("write span %s: %w", sp.SpanID, err)
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return len(spans), nil
}

func (s *Store) QuerySpans(ctx context.Context, projectID string, q SpanQuery) ([]Span, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return nil, err
	}
	var conds []string
	var args []any
	if q.TraceID != nil {
		conds = append(conds, "trace_id = ?")
		args = append(args, *q.TraceID)
	}
	conds, args = q.Range.where("start_time", conds, args)
	args = append(args, q.Limit)

	rows, err := db.QueryContext(ctx, `
		SELECT span_id, trace_id, parent_span_id, name, start_time, end_time, status_code,
			CAST(attributes AS VARCHAR), CAST(resource_attributes AS VARCHAR), scope_name, scope_version
		FROM spans`+whereClause(conds)+`
		ORDER BY start_time, span_id
		LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var spans []Span
	for rows.Next() {
		var (
			sp                   Span
			parent, status       sql.NullString
			end                  sql.NullTime
			attrs, resourceAttrs sql.NullString
		)
		if err := rows.Scan(&sp.SpanID, &sp.TraceID, &parent, &sp.Name, &sp.StartTime, &end, &status, &attrs, &resourceAttrs, &sp.ScopeName, &sp.ScopeVersion); err != nil {
			return nil, err
		}
		if parent.Valid {
			sp.ParentSpanID = &parent.String
		}
		if status.Valid {
			sp.StatusCode = &status.String
		}
		if end.Valid {
			sp.EndTime = &end.Time
		}
		if attrs.Valid {
			sp.Attributes = []byte(attrs.String)
		}
		if resourceAttrs.Valid {
			sp.ResourceAttributes = []byte(resourceAttrs.String)
		}
		spans = append(spans, sp)
	}
	return spans, rows.Err()
}

// QueryTraces returns traces newest first.
func (s *Store) QueryTraces(ctx context.Context, projectID string, q TraceQuery) ([]Trace, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return nil, err
	}
	conds, args := q.Range.where("start_time", nil, nil)
	args = append(args, q.Limit)

	rows, err := db.QueryContext(ctx, `
		SELECT trace_id, start_time, end_time, span_count
		FROM traces`+whereClause(conds)+`
		ORDER BY start_time DESC, trace_id
		LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var traces []Trace
	for rows.Next() {
		var (
			t   Trace
			end sql.NullTime
		)
		if err := rows.Scan(&t.TraceID, &t.StartTime, &end, &t.SpanCount); err != nil {
			return nil, err
		}
		if end.Valid {
			t.EndTime = &end.Time
		}
		traces = append(traces, t)
	}
	return traces, rows.Err()
}

func (s *Store) WriteLogs(ctx context.Context, projectID string, logs []Log) (int, error) {
	return writeRows(ctx, s, projectID, logs, `
		INSERT INTO logs
			(log_id, timestamp, observed_timestamp, severity_number, severity_text, body, trace_id, span_id, attributes, resource_attributes, scope_name)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		func(log Log) []any {
			return []any{log.ID, log.Timestamp, log.ObservedTimestamp, log.SeverityNumber, log.SeverityText, jsonParam(log.Body), log.TraceID, log.SpanID, jsonParam(log.Attributes), jsonParam(log.ResourceAttributes), log.ScopeName}
		})
}

func (s *Store) WriteMetrics(ctx context.Context, projectID string, metrics []Metric) (int, error) {
	return writeRows(ctx, s, projectID, metrics, `
		INSERT INTO metrics
			(metric_id, name, description, unit, type, timestamp, attributes, resource_attributes, scope_name, data)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		func(metric Metric) []any {
			return []any{metric.ID, metric.Name, metric.Description, metric.Unit, metric.Type, metric.Timestamp, jsonParam(metric.Attributes), jsonParam(metric.ResourceAttributes), metric.ScopeName, jsonParam(metric.Data)}
		})
}

func (s *Store) WriteProfiles(ctx context.Context, projectID string, profiles []Profile) (int, error) {
	return writeRows(ctx, s, projectID, profiles, `
		INSERT INTO profiles (profile_id, received_at, data) VALUES (?, ?, ?)`,
		func(profile Profile) []any {
			return []any{profile.ID, profile.ReceivedAt, jsonParam(profile.Data)}
		})
}

// QueryLogs returns logs oldest first, optionally limited to one trace.
func (s *Store) QueryLogs(ctx context.Context, projectID string, traceID *string, limit int) ([]Log, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return nil, err
	}
	var conds []string
	var args []any
	if traceID != nil {
		conds = append(conds, "trace_id = ?")
		args = append(args, *traceID)
	}
	args = append(args, limit)
	rows, err := db.QueryContext(ctx, `
		SELECT log_id, timestamp, observed_timestamp, severity_number, severity_text, CAST(body AS VARCHAR),
			trace_id, span_id, CAST(attributes AS VARCHAR), CAST(resource_attributes AS VARCHAR), scope_name
		FROM logs`+whereClause(conds)+`
		ORDER BY timestamp, log_id
		LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var logs []Log
	for rows.Next() {
		var (
			log                                Log
			timestamp, observed                sql.NullTime
			body, attrs, resource, trace, span sql.NullString
		)
		if err := rows.Scan(&log.ID, &timestamp, &observed, &log.SeverityNumber, &log.SeverityText, &body, &trace, &span, &attrs, &resource, &log.ScopeName); err != nil {
			return nil, err
		}
		if timestamp.Valid {
			log.Timestamp = &timestamp.Time
		}
		if observed.Valid {
			log.ObservedTimestamp = &observed.Time
		}
		log.Body = nullBytes(body)
		log.TraceID = nullString(trace)
		log.SpanID = nullString(span)
		log.Attributes = nullBytes(attrs)
		log.ResourceAttributes = nullBytes(resource)
		logs = append(logs, log)
	}
	return logs, rows.Err()
}

// ListMetrics returns the newest data points in the project.
func (s *Store) ListMetrics(ctx context.Context, projectID string, limit int) ([]Metric, error) {
	return s.queryMetrics(ctx, projectID, "", limit)
}

// QueryMetrics returns data points for one metric name, oldest first.
func (s *Store) QueryMetrics(ctx context.Context, projectID, name string, limit int) ([]Metric, error) {
	return s.queryMetrics(ctx, projectID, name, limit)
}

func (s *Store) queryMetrics(ctx context.Context, projectID, name string, limit int) ([]Metric, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return nil, err
	}
	query := `
		SELECT metric_id, name, description, unit, type, timestamp,
			CAST(attributes AS VARCHAR), CAST(resource_attributes AS VARCHAR), scope_name, CAST(data AS VARCHAR)
		FROM metrics`
	args := []any{}
	if name != "" {
		query += ` WHERE name = ?`
		args = append(args, name)
	}
	order := ` ORDER BY timestamp, metric_id`
	if name == "" {
		order = ` ORDER BY timestamp DESC, metric_id`
	}
	query += order + ` LIMIT ?`
	args = append(args, limit)
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var metrics []Metric
	for rows.Next() {
		var (
			metric                Metric
			timestamp             sql.NullTime
			attrs, resource, data sql.NullString
		)
		if err := rows.Scan(&metric.ID, &metric.Name, &metric.Description, &metric.Unit, &metric.Type, &timestamp, &attrs, &resource, &metric.ScopeName, &data); err != nil {
			return nil, err
		}
		if timestamp.Valid {
			metric.Timestamp = &timestamp.Time
		}
		metric.Attributes = nullBytes(attrs)
		metric.ResourceAttributes = nullBytes(resource)
		metric.Data = nullBytes(data)
		metrics = append(metrics, metric)
	}
	return metrics, rows.Err()
}

func (s *Store) CountProfiles(ctx context.Context, projectID string) (int, error) {
	db, err := s.db(ctx, projectID)
	if err != nil {
		return 0, err
	}
	var n int
	err = db.QueryRowContext(ctx, `SELECT count(*) FROM profiles`).Scan(&n)
	return n, err
}

func writeRows[T any](ctx context.Context, s *Store, projectID string, rows []T, query string, args func(T) []any) (int, error) {
	if len(rows) == 0 {
		return 0, nil
	}
	db, err := s.db(ctx, projectID)
	if err != nil {
		return 0, err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	stmt, err := tx.PrepareContext(ctx, query)
	if err != nil {
		return 0, err
	}
	defer stmt.Close()
	for _, row := range rows {
		if _, err := stmt.ExecContext(ctx, args(row)...); err != nil {
			return 0, err
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return len(rows), nil
}

func whereClause(conds []string) string {
	if len(conds) == 0 {
		return ""
	}
	return " WHERE " + strings.Join(conds, " AND ")
}

func jsonParam(b []byte) any {
	if b == nil {
		return nil
	}
	return string(b)
}

func nullBytes(v sql.NullString) []byte {
	if !v.Valid {
		return nil
	}
	return []byte(v.String)
}

func nullString(v sql.NullString) *string {
	if !v.Valid {
		return nil
	}
	return &v.String
}
