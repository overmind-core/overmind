package overmind

import (
	"errors"
	"testing"
	"time"

	"connectrpc.com/connect/v2"
	"ganigeorgiev.com/pocketbase/pocketbase/core"

	overmindv1 "github.com/overmind/gen/overmind/v1"
)

func TestInviteStoresHashAndSendsOneEmail(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")

	invitation, token := h.invite(owner, projectID, " New@Overmind.test ")

	if h.app.TestMailer.TotalSend() != 1 {
		t.Fatalf("emails sent = %d, want 1", h.app.TestMailer.TotalSend())
	}
	if invitation.Email != "new@overmind.test" || invitation.Role != roleMember || invitation.Status != statusPending {
		t.Fatalf("invitation = %+v", invitation)
	}
	stored := h.record(invitationsCollection, invitation.Id)
	if got := stored.GetString("token_hash"); got != hashInvitationToken(token) || got == token {
		t.Fatalf("token_hash = %q", got)
	}

	_, err := h.invitations.Invite(h.as(owner), &overmindv1.InviteRequest{ProjectId: projectID, Email: "new@overmind.test"})
	assertCode(t, err, connect.CodeAlreadyExists)

	outsider := h.user("outsider@overmind.test")
	_, err = h.invitations.Invite(h.as(outsider), &overmindv1.InviteRequest{ProjectId: projectID, Email: "x@overmind.test"})
	assertCode(t, err, connect.CodePermissionDenied)
}

func TestInviteRejectsExistingMember(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	member := h.user("member@overmind.test")
	projectID := h.createProject(owner, "proj")
	h.addMember(owner, member, projectID)

	_, err := h.invitations.Invite(h.as(owner), &overmindv1.InviteRequest{ProjectId: projectID, Email: member.Email()})
	assertCode(t, err, connect.CodeAlreadyExists)
}

func TestResendRotatesTokenAndExtendsExpiry(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	invitation, oldToken := h.invite(owner, projectID, "new@overmind.test")

	stored := h.record(invitationsCollection, invitation.Id)
	stored.Set("expires_at", time.Now().Add(time.Hour))
	if err := h.app.Save(stored); err != nil {
		t.Fatal(err)
	}

	res, err := h.invitations.Resend(h.as(owner), &overmindv1.ResendRequest{InvitationId: invitation.Id})
	if err != nil {
		t.Fatal(err)
	}
	newToken := h.lastMailedToken()
	if newToken == oldToken {
		t.Fatal("resend reused the token")
	}
	stored = h.record(invitationsCollection, invitation.Id)
	if stored.GetString("token_hash") != hashInvitationToken(newToken) {
		t.Fatal("stored hash does not match the new token")
	}
	if !res.Invitation.ExpiresAt.AsTime().After(time.Now().Add(invitationTTL - time.Minute)) {
		t.Fatalf("expires_at = %v, want about %v from now", res.Invitation.ExpiresAt.AsTime(), invitationTTL)
	}

	_, err = h.invitations.Accept(h.as(owner), &overmindv1.AcceptRequest{Token: oldToken})
	assertCode(t, err, connect.CodeNotFound)
}

func TestResendRevivesExpiredInvitation(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	invitation, _ := h.invite(owner, projectID, "new@overmind.test")
	expire(t, h, invitation.Id)
	expireInvitationsJob(h.app)

	res, err := h.invitations.Resend(h.as(owner), &overmindv1.ResendRequest{InvitationId: invitation.Id})
	if err != nil {
		t.Fatal(err)
	}
	if res.Invitation.Status != statusPending {
		t.Fatalf("status = %q, want pending", res.Invitation.Status)
	}
}

func TestFailedResendKeepsPreviousToken(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	invitee := h.user("invitee@overmind.test")
	projectID := h.createProject(owner, "proj")
	invitation, token := h.invite(owner, projectID, invitee.Email())

	h.app.OnMailerSend().BindFunc(func(e *core.MailerEvent) error {
		return errors.New("smtp down")
	})
	_, err := h.invitations.Resend(h.as(owner), &overmindv1.ResendRequest{InvitationId: invitation.Id})
	assertCode(t, err, connect.CodeUnavailable)

	if got := h.record(invitationsCollection, invitation.Id).GetString("token_hash"); got != hashInvitationToken(token) {
		t.Fatal("failed resend replaced the token")
	}
	if _, err := h.invitations.Accept(h.as(invitee), &overmindv1.AcceptRequest{Token: token}); err != nil {
		t.Fatalf("previous token: %v", err)
	}
}

func TestFailedInviteSendLeavesNoInvitation(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")

	h.app.OnMailerSend().BindFunc(func(e *core.MailerEvent) error {
		return errors.New("smtp down")
	})
	_, err := h.invitations.Invite(h.as(owner), &overmindv1.InviteRequest{ProjectId: projectID, Email: "new@overmind.test"})
	assertCode(t, err, connect.CodeUnavailable)

	count, err := h.app.CountRecords(invitationsCollection)
	if err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("invitations = %d, want 0", count)
	}
}

func TestAcceptBySignedInUserCreatesMemberMembership(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	invitee := h.user("invitee@overmind.test")
	projectID := h.createProject(owner, "proj")
	invitation, token := h.invite(owner, projectID, invitee.Email())

	_, err := h.invitations.Accept(h.as(owner), &overmindv1.AcceptRequest{Token: token})
	assertCode(t, err, connect.CodePermissionDenied)
	_, err = h.invitations.Accept(t.Context(), &overmindv1.AcceptRequest{Token: token})
	assertCode(t, err, connect.CodeUnauthenticated)

	res, err := h.invitations.Accept(h.as(invitee), &overmindv1.AcceptRequest{Token: token})
	if err != nil {
		t.Fatal(err)
	}
	membership := h.record(membershipsCollection, res.MembershipId)
	if membership.GetString("role") != roleMember || membership.GetString("user") != invitee.Id {
		t.Fatalf("membership = %v", membership)
	}
	stored := h.record(invitationsCollection, invitation.Id)
	if stored.GetString("status") != statusAccepted || stored.GetString("accepted_by") != invitee.Id {
		t.Fatalf("invitation status = %q", stored.GetString("status"))
	}

	_, err = h.invitations.Accept(h.as(invitee), &overmindv1.AcceptRequest{Token: token})
	assertCode(t, err, connect.CodeFailedPrecondition)
}

func TestAcceptForNewEmailCreatesUser(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	_, token := h.invite(owner, projectID, "fresh@overmind.test")

	_, err := h.invitations.Accept(t.Context(), &overmindv1.AcceptRequest{Token: token})
	assertCode(t, err, connect.CodeInvalidArgument)

	res, err := h.invitations.Accept(t.Context(), &overmindv1.AcceptRequest{Token: token, Password: "password-123"})
	if err != nil {
		t.Fatal(err)
	}
	user := h.record(usersCollection, res.UserId)
	if user.Email() != "fresh@overmind.test" || !user.Verified() || !user.ValidatePassword("password-123") {
		t.Fatalf("user = %v", user)
	}
	if _, err := findMembership(h.app, user.Id, projectID); err != nil {
		t.Fatal(err)
	}
}

func TestAcceptRejectsExpiredAndRevoked(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	invitee := h.user("invitee@overmind.test")
	projectID := h.createProject(owner, "proj")

	expired, expiredToken := h.invite(owner, projectID, invitee.Email())
	expire(t, h, expired.Id)
	_, err := h.invitations.Accept(h.as(invitee), &overmindv1.AcceptRequest{Token: expiredToken})
	assertCode(t, err, connect.CodeFailedPrecondition)
	if status := h.record(invitationsCollection, expired.Id).GetString("status"); status != statusExpired {
		t.Fatalf("status = %q, want expired", status)
	}

	revoked, revokedToken := h.invite(owner, projectID, invitee.Email())
	if _, err := h.invitations.Revoke(h.as(owner), &overmindv1.RevokeRequest{InvitationId: revoked.Id}); err != nil {
		t.Fatal(err)
	}
	_, err = h.invitations.Accept(h.as(invitee), &overmindv1.AcceptRequest{Token: revokedToken})
	assertCode(t, err, connect.CodeFailedPrecondition)

	if _, err := findMembership(h.app, invitee.Id, projectID); !errors.Is(err, errNotFound) {
		t.Fatalf("membership lookup = %v, want none", err)
	}
}

func TestExpireJobMarksPastPendingInvitations(t *testing.T) {
	h := newHarness(t)
	owner := h.user("owner@overmind.test")
	projectID := h.createProject(owner, "proj")
	past, _ := h.invite(owner, projectID, "past@overmind.test")
	live, _ := h.invite(owner, projectID, "live@overmind.test")
	expire(t, h, past.Id)

	expireInvitationsJob(h.app)

	if status := h.record(invitationsCollection, past.Id).GetString("status"); status != statusExpired {
		t.Fatalf("past status = %q, want expired", status)
	}
	if status := h.record(invitationsCollection, live.Id).GetString("status"); status != statusPending {
		t.Fatalf("live status = %q, want pending", status)
	}
}

func expire(t *testing.T, h *harness, invitationID string) {
	t.Helper()
	record := h.record(invitationsCollection, invitationID)
	record.Set("expires_at", time.Now().Add(-time.Minute))
	if err := h.app.Save(record); err != nil {
		t.Fatal(err)
	}
}
