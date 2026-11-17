package overmind

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"regexp"
	"testing"

	"connectrpc.com/connect/v2"
	"connectrpc.com/connect/v2/connecthttp"
	"ganigeorgiev.com/pocketbase/pocketbase/apis"
	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"ganigeorgiev.com/pocketbase/pocketbase/tests"

	overmindv1 "github.com/overmind/gen/overmind/v1"
	"github.com/overmind/gen/overmind/v1/overmindv1connect"
)

type harness struct {
	t           *testing.T
	app         *tests.TestApp
	server      *httptest.Server
	invitations overmindv1connect.InvitationServiceClient
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	app, err := tests.NewTestApp(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(app.Cleanup)
	app.Settings().Meta.SenderAddress = "noreply@overmind.test"

	if err := Register(app, Config{AppURL: "http://overmind.test"}); err != nil {
		t.Fatal(err)
	}
	r, err := apis.NewRouter(app)
	if err != nil {
		t.Fatal(err)
	}
	var handler http.Handler
	serveEvent := &core.ServeEvent{App: app, Router: r}
	if err := app.OnServe().Trigger(serveEvent, func(e *core.ServeEvent) error {
		mux, err := e.Router.BuildMux()
		handler = mux
		return err
	}); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)

	client := connect.NewClient(connecthttp.NewTransport(server.Client(), server.URL+rpcPrefix))
	return &harness{
		t:           t,
		app:         app,
		server:      server,
		invitations: overmindv1connect.NewInvitationServiceClient(client),
	}
}

func (h *harness) user(email string) *core.Record {
	h.t.Helper()
	users, err := h.app.FindCachedCollectionByNameOrId(usersCollection)
	if err != nil {
		h.t.Fatal(err)
	}
	user := core.NewRecord(users)
	user.SetEmail(email)
	user.SetPassword("password-123")
	if err := h.app.Save(user); err != nil {
		h.t.Fatal(err)
	}
	return user
}

func (h *harness) token(user *core.Record) string {
	h.t.Helper()
	token, err := user.NewAuthToken()
	if err != nil {
		h.t.Fatal(err)
	}
	return token
}

// as returns a context whose RPCs carry user's auth token.
func (h *harness) as(user *core.Record) context.Context {
	ctx, info := connect.NewClientContext(context.Background())
	info.RequestHeader().Set("Authorization", h.token(user))
	return ctx
}

// rest calls the PocketBase REST API and returns the status and decoded body.
func (h *harness) rest(user *core.Record, method, path string, body any) (int, map[string]any) {
	h.t.Helper()
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			h.t.Fatal(err)
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}
	req, err := http.NewRequest(method, h.server.URL+path, reader)
	if err != nil {
		h.t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if user != nil {
		req.Header.Set("Authorization", h.token(user))
	}
	res, err := h.server.Client().Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer res.Body.Close()
	decoded := map[string]any{}
	_ = json.NewDecoder(res.Body).Decode(&decoded)
	return res.StatusCode, decoded
}

func (h *harness) createProject(owner *core.Record, slug string) string {
	h.t.Helper()
	status, body := h.rest(owner, http.MethodPost, "/api/collections/projects/records",
		map[string]any{"name": slug, "slug": slug})
	if status != http.StatusOK {
		h.t.Fatalf("create project %s: status %d, body %v", slug, status, body)
	}
	return body["id"].(string)
}

func (h *harness) invite(inviter *core.Record, projectID, email string) (*overmindv1.Invitation, string) {
	h.t.Helper()
	res, err := h.invitations.Invite(h.as(inviter), &overmindv1.InviteRequest{ProjectId: projectID, Email: email})
	if err != nil {
		h.t.Fatalf("invite %s: %v", email, err)
	}
	return res.Invitation, h.lastMailedToken()
}

// addMember invites email into projectID and accepts as member.
func (h *harness) addMember(owner, member *core.Record, projectID string) {
	h.t.Helper()
	_, token := h.invite(owner, projectID, member.Email())
	if _, err := h.invitations.Accept(h.as(member), &overmindv1.AcceptRequest{Token: token}); err != nil {
		h.t.Fatalf("accept: %v", err)
	}
}

var tokenPattern = regexp.MustCompile(`token=([A-Za-z0-9_-]+)`)

func (h *harness) lastMailedToken() string {
	h.t.Helper()
	if h.app.TestMailer.TotalSend() == 0 {
		h.t.Fatal("no email sent")
	}
	match := tokenPattern.FindStringSubmatch(h.app.TestMailer.LastMessage().Text)
	if match == nil {
		h.t.Fatalf("no token in email: %q", h.app.TestMailer.LastMessage().Text)
	}
	return match[1]
}

func (h *harness) record(collection, id string) *core.Record {
	h.t.Helper()
	record, err := h.app.FindRecordById(collection, id)
	if err != nil {
		h.t.Fatal(err)
	}
	return record
}

func assertCode(t *testing.T, err error, want connect.Code) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected %v, got success", want)
	}
	if got := connect.CodeOf(err); got != want {
		t.Fatalf("expected %v, got %v (%v)", want, got, err)
	}
}
