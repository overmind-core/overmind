package overmind

import (
	"errors"
	"html/template"
	"net/http"
	"time"

	"ganigeorgiev.com/pocketbase/dbx"

	"github.com/overmind/overmind/store"
)

var (
	projectsTmpl = template.Must(template.New("projects").Parse(`
<option value="">Select a project</option>
{{range .}}<option value="{{.ID}}">{{.Label}}</option>{{end}}
`))
	boardTmpl = template.Must(template.New("board").Parse(`
<section>
  <h2>Spans <span>{{len .Spans}}</span></h2>
  {{if .Spans}}
  <table>
    <thead><tr><th>Name</th><th>Trace</th><th>Start</th></tr></thead>
    <tbody>
    {{range .Spans}}<tr><td>{{.Name}}</td><td>{{.Trace}}</td><td>{{.When}}</td></tr>{{end}}
    </tbody>
  </table>
  {{else}}<p>0 spans</p>{{end}}
</section>
<section>
  <h2>Logs <span>{{len .Logs}}</span></h2>
  {{if .Logs}}
  <table>
    <thead><tr><th>Severity</th><th>Body</th><th>When</th></tr></thead>
    <tbody>
    {{range .Logs}}<tr><td>{{.Severity}}</td><td>{{.Body}}</td><td>{{.When}}</td></tr>{{end}}
    </tbody>
  </table>
  {{else}}<p>0 logs</p>{{end}}
</section>
<section>
  <h2>Metrics <span>{{len .Metrics}}</span></h2>
  {{if .Metrics}}
  <table>
    <thead><tr><th>Name</th><th>Type</th><th>Data</th><th>When</th></tr></thead>
    <tbody>
    {{range .Metrics}}<tr><td>{{.Name}}</td><td>{{.Type}}</td><td>{{.Data}}</td><td>{{.When}}</td></tr>{{end}}
    </tbody>
  </table>
  {{else}}<p>0 metrics</p>{{end}}
</section>
<p>Profiles {{.Profiles}}</p>
`))
)

type projectOption struct {
	ID    string
	Label string
}

type boardView struct {
	Spans    []boardRow
	Logs     []boardRow
	Metrics  []boardRow
	Profiles int
}

type boardRow struct {
	Name     string
	Trace    string
	Severity string
	Body     string
	Type     string
	Data     string
	When     string
}

func (h *otlpHandler) projects(w http.ResponseWriter, r *http.Request) {
	user, err := h.user(r)
	if err != nil {
		http.Error(w, "Sign in.", http.StatusUnauthorized)
		return
	}
	memberships, err := h.app.FindRecordsByFilter(membershipsCollection, "user = {:user}", "-created", 50, 0, dbx.Params{"user": user.Id})
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	options := make([]projectOption, 0, len(memberships))
	for _, membership := range memberships {
		project, err := h.app.FindRecordById(projectsCollection, membership.GetString("project"))
		if err != nil {
			continue
		}
		options = append(options, projectOption{ID: project.Id, Label: project.GetString("name")})
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = projectsTmpl.Execute(w, options)
}

func (h *otlpHandler) board(w http.ResponseWriter, r *http.Request) {
	user, err := h.user(r)
	if err != nil {
		http.Error(w, "Sign in.", http.StatusUnauthorized)
		return
	}
	projectID := r.Header.Get(ProjectHeader)
	if projectID == "" {
		http.Error(w, "Select a project.", http.StatusBadRequest)
		return
	}
	if _, err := findMembership(h.app, user.Id, projectID); err != nil {
		if errors.Is(err, errNotFound) {
			http.Error(w, "Not a member of this project.", http.StatusForbidden)
		} else {
			http.Error(w, "internal error", http.StatusInternalServerError)
		}
		return
	}

	view := boardView{}
	spans, err := h.store.QuerySpans(r.Context(), projectID, store.SpanQuery{Limit: 20})
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	for i := len(spans) - 1; i >= 0; i-- {
		sp := spans[i]
		view.Spans = append(view.Spans, boardRow{Name: sp.Name, Trace: sp.TraceID, When: sp.StartTime.UTC().Format(time.RFC3339)})
	}
	logs, err := h.store.QueryLogs(r.Context(), projectID, nil, 20)
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	for i := len(logs) - 1; i >= 0; i-- {
		log := logs[i]
		when := ""
		if log.Timestamp != nil {
			when = log.Timestamp.UTC().Format(time.RFC3339)
		}
		view.Logs = append(view.Logs, boardRow{Severity: log.SeverityText, Body: string(log.Body), When: when})
	}
	metrics, err := h.store.ListMetrics(r.Context(), projectID, 20)
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	for _, metric := range metrics {
		when := ""
		if metric.Timestamp != nil {
			when = metric.Timestamp.UTC().Format(time.RFC3339)
		}
		view.Metrics = append(view.Metrics, boardRow{Name: metric.Name, Type: metric.Type, Data: string(metric.Data), When: when})
	}
	n, err := h.store.CountProfiles(r.Context(), projectID)
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	view.Profiles = n

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = boardTmpl.Execute(w, view)
}
