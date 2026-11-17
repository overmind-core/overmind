package overmind

import (
	"net/http"
	"os"
	"path/filepath"
	"testing"
)

func TestProjectCreateAddsOwnerMembershipAndStore(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")

	first := h.createProject(owner, "first")
	second := h.createProject(owner, "second")

	membership, err := findMembership(h.app, owner.Id, first)
	if err != nil {
		t.Fatal(err)
	}
	if role := membership.GetString("role"); role != roleOwner {
		t.Fatalf("role = %q, want %q", role, roleOwner)
	}

	dir := filepath.Join(h.app.DataDir(), "projects")
	for _, id := range []string{first, second} {
		if _, err := os.Stat(filepath.Join(dir, id+".duckdb")); err != nil {
			t.Fatalf("project %s store: %v", id, err)
		}
	}
	if first == second {
		t.Fatal("projects share an id")
	}
}

func TestProjectDeleteRemovesStore(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "doomed")

	status, body := h.rest(owner, http.MethodDelete, "/api/collections/projects/records/"+projectID, nil)
	if status != http.StatusNoContent {
		t.Fatalf("delete: status %d, body %v", status, body)
	}
	if _, err := os.Stat(filepath.Join(h.app.DataDir(), "projects", projectID+".duckdb")); !os.IsNotExist(err) {
		t.Fatalf("store still present: %v", err)
	}
}

func TestProjectRulesUseTheCallersOwnMembership(t *testing.T) {
	h := newHarness(t)
	alice := h.user("alice@overmind.test")
	bob := h.user("bob@overmind.test")
	outsider := h.user("outsider@overmind.test")

	alicesProject := h.createProject(alice, "alices")
	bobsProject := h.createProject(bob, "bobs")
	h.addMember(bob, alice, bobsProject)

	// Alice owns one project and is a member of Bob's: owning a different
	// project must not grant owner rights on this one.
	status, _ := h.rest(alice, http.MethodPatch, "/api/collections/projects/records/"+bobsProject,
		map[string]any{"name": "renamed"})
	if status != http.StatusNotFound {
		t.Fatalf("member update: status %d, want 404", status)
	}
	status, _ = h.rest(bob, http.MethodPatch, "/api/collections/projects/records/"+bobsProject,
		map[string]any{"name": "renamed"})
	if status != http.StatusOK {
		t.Fatalf("owner update: status %d, want 200", status)
	}

	status, body := h.rest(alice, http.MethodGet, "/api/collections/projects/records", nil)
	if status != http.StatusOK || body["totalItems"].(float64) != 2 {
		t.Fatalf("alice list: status %d, body %v", status, body)
	}
	status, body = h.rest(outsider, http.MethodGet, "/api/collections/projects/records", nil)
	if status != http.StatusOK || body["totalItems"].(float64) != 0 {
		t.Fatalf("outsider list: status %d, body %v", status, body)
	}
	status, _ = h.rest(outsider, http.MethodGet, "/api/collections/projects/records/"+alicesProject, nil)
	if status != http.StatusNotFound {
		t.Fatalf("outsider view: status %d, want 404", status)
	}

	status, _ = h.rest(alice, http.MethodPost, "/api/collections/project_memberships/records",
		map[string]any{"user": outsider.Id, "project": alicesProject, "role": roleOwner})
	if status != http.StatusForbidden {
		t.Fatalf("direct membership create: status %d, want 403", status)
	}
}
