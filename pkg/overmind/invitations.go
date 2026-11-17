package overmind

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"net/mail"
	"net/url"
	"strings"
	"time"

	"connectrpc.com/connect/v2"
	"ganigeorgiev.com/pocketbase/dbx"
	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"ganigeorgiev.com/pocketbase/pocketbase/tools/mailer"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/metric"
	"google.golang.org/protobuf/types/known/timestamppb"

	overmindv1 "github.com/overmind/gen/overmind/v1"
	"github.com/overmind/overmind/telemetry"
)

const (
	invitationTTL = 7 * 24 * time.Hour

	statusPending  = "pending"
	statusAccepted = "accepted"
	statusRevoked  = "revoked"
	statusExpired  = "expired"
)

type invitationService struct {
	app    core.App
	appURL string
}

func (s *invitationService) Invite(ctx context.Context, req *overmindv1.InviteRequest) (*overmindv1.InviteResponse, error) {
	inviter, err := requireMember(ctx, s.app, req.ProjectId)
	if err != nil {
		return nil, err
	}
	email, err := normalizeEmail(req.Email)
	if err != nil {
		return nil, err
	}
	project, err := s.app.FindRecordById(projectsCollection, req.ProjectId)
	if err != nil {
		return nil, internalError(err)
	}

	if invitee, err := s.app.FindAuthRecordByEmail(usersCollection, email); err == nil {
		if _, err := findMembership(s.app, invitee.Id, project.Id); err == nil {
			return nil, connect.NewError(connect.CodeAlreadyExists, "already a member of this project")
		} else if !errors.Is(err, errNotFound) {
			return nil, internalError(err)
		}
	} else if !isNoRows(err) {
		return nil, internalError(err)
	}

	if err := s.ensureNoPending(ctx, project.Id, email); err != nil {
		return nil, err
	}

	collection, err := s.app.FindCachedCollectionByNameOrId(invitationsCollection)
	if err != nil {
		return nil, internalError(err)
	}
	token, tokenHash, err := newInvitationToken()
	if err != nil {
		return nil, internalError(err)
	}
	now := time.Now().UTC()
	invitation := core.NewRecord(collection)
	invitation.Set("project", project.Id)
	invitation.Set("email", email)
	invitation.Set("role", roleMember)
	invitation.Set("status", statusPending)
	invitation.Set("token_hash", tokenHash)
	invitation.Set("expires_at", now.Add(invitationTTL))
	invitation.Set("last_sent_at", now)
	invitation.Set("invited_by", inviter.Id)
	if err := s.app.Save(invitation); err != nil {
		if isUniqueViolation(err) {
			return nil, connect.NewError(connect.CodeAlreadyExists, "a pending invitation exists for this email")
		}
		return nil, asConnectError(err)
	}

	if err := s.send(ctx, invitation, project, inviter, token); err != nil {
		if deleteErr := s.app.Delete(invitation); deleteErr != nil {
			slog.ErrorContext(ctx, "delete unsent invitation",
				slog.String("invitation_id", invitation.Id), slog.Any("error", deleteErr))
		}
		return nil, connect.NewError(connect.CodeUnavailable, "the invitation email could not be sent").WithCause(err)
	}

	invitationsSent.Add(ctx, 1, metric.WithAttributes(attribute.Bool("resend", false)))
	telemetry.Event(ctx, "invitation.sent", invitationAttrs(invitation)...)
	return &overmindv1.InviteResponse{Invitation: invitationProto(invitation)}, nil
}

func (s *invitationService) Resend(ctx context.Context, req *overmindv1.ResendRequest) (*overmindv1.ResendResponse, error) {
	invitation, err := s.findInvitation(req.InvitationId)
	if err != nil {
		return nil, err
	}
	inviter, err := requireMember(ctx, s.app, invitation.GetString("project"))
	if err != nil {
		return nil, err
	}

	switch invitation.GetString("status") {
	case statusPending:
	case statusExpired:
		if err := s.ensureNoPending(ctx, invitation.GetString("project"), invitation.GetString("email")); err != nil {
			return nil, err
		}
	default:
		return nil, connect.NewError(connect.CodeFailedPrecondition,
			"only a pending or expired invitation can be resent")
	}

	project, err := s.app.FindRecordById(projectsCollection, invitation.GetString("project"))
	if err != nil {
		return nil, internalError(err)
	}
	token, tokenHash, err := newInvitationToken()
	if err != nil {
		return nil, internalError(err)
	}

	// The stored token is replaced only after the new link is delivered, so a
	// failed send leaves the previous link working.
	now := time.Now().UTC()
	resent := invitation.Clone()
	resent.Set("token_hash", tokenHash)
	resent.Set("status", statusPending)
	resent.Set("expires_at", now.Add(invitationTTL))
	resent.Set("last_sent_at", now)
	resent.Set("invited_by", inviter.Id)
	if err := s.send(ctx, resent, project, inviter, token); err != nil {
		return nil, connect.NewError(connect.CodeUnavailable, "the invitation email could not be sent").WithCause(err)
	}
	if err := s.app.Save(resent); err != nil {
		if isUniqueViolation(err) {
			return nil, connect.NewError(connect.CodeAlreadyExists, "a pending invitation exists for this email")
		}
		return nil, asConnectError(err)
	}

	invitationsSent.Add(ctx, 1, metric.WithAttributes(attribute.Bool("resend", true)))
	telemetry.Event(ctx, "invitation.resent", invitationAttrs(resent)...)
	return &overmindv1.ResendResponse{Invitation: invitationProto(resent)}, nil
}

func (s *invitationService) Revoke(ctx context.Context, req *overmindv1.RevokeRequest) (*overmindv1.RevokeResponse, error) {
	invitation, err := s.findInvitation(req.InvitationId)
	if err != nil {
		return nil, err
	}
	if _, err := requireMember(ctx, s.app, invitation.GetString("project")); err != nil {
		return nil, err
	}
	if invitation.GetString("status") != statusPending {
		return nil, connect.NewError(connect.CodeFailedPrecondition, "only a pending invitation can be revoked")
	}
	invitation.Set("status", statusRevoked)
	if err := s.app.Save(invitation); err != nil {
		return nil, asConnectError(err)
	}

	telemetry.Event(ctx, "invitation.revoked", invitationAttrs(invitation)...)
	return &overmindv1.RevokeResponse{Invitation: invitationProto(invitation)}, nil
}

func (s *invitationService) Accept(ctx context.Context, req *overmindv1.AcceptRequest) (*overmindv1.AcceptResponse, error) {
	if req.Token == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, "token is required")
	}
	invitation, err := findOne(s.app, invitationsCollection, "token_hash = {:hash}",
		dbx.Params{"hash": hashInvitationToken(req.Token)})
	if errors.Is(err, errNotFound) {
		return nil, connect.NewError(connect.CodeNotFound, "invitation not found")
	} else if err != nil {
		return nil, internalError(err)
	}
	if err := s.checkAcceptable(ctx, invitation); err != nil {
		return nil, err
	}

	email := invitation.GetString("email")
	existing, err := s.app.FindAuthRecordByEmail(usersCollection, email)
	if err != nil && !isNoRows(err) {
		return nil, internalError(err)
	}
	caller := authUser(ctx)

	var membership *core.Record
	var user *core.Record
	err = s.app.RunInTransaction(func(txApp core.App) error {
		// Re-read inside the transaction so concurrent accepts and revokes
		// see one outcome.
		current, err := txApp.FindRecordById(invitationsCollection, invitation.Id)
		if err != nil {
			return err
		}
		if current.GetString("status") != statusPending {
			return connect.NewError(connect.CodeFailedPrecondition, "invitation is "+current.GetString("status"))
		}

		switch {
		case existing != nil && caller == nil:
			return connect.NewError(connect.CodeUnauthenticated, "an account exists for this email; sign in to accept")
		case existing != nil && caller.Id != existing.Id:
			return connect.NewError(connect.CodePermissionDenied, "the invitation was sent to a different account")
		case existing != nil:
			user = existing
		case caller != nil:
			return connect.NewError(connect.CodePermissionDenied, "the invitation was sent to a different email")
		case req.Password == "":
			return connect.NewError(connect.CodeInvalidArgument, "password is required to create the account")
		default:
			users, err := txApp.FindCachedCollectionByNameOrId(usersCollection)
			if err != nil {
				return err
			}
			user = core.NewRecord(users)
			user.SetEmail(email)
			user.SetPassword(req.Password)
			// Holding the emailed token proves ownership of the address.
			user.SetVerified(true)
			if err := txApp.Save(user); err != nil {
				return err
			}
		}

		membership, err = findMembership(txApp, user.Id, current.GetString("project"))
		if errors.Is(err, errNotFound) {
			memberships, err := txApp.FindCachedCollectionByNameOrId(membershipsCollection)
			if err != nil {
				return err
			}
			membership = core.NewRecord(memberships)
			membership.Set("user", user.Id)
			membership.Set("project", current.GetString("project"))
			membership.Set("role", current.GetString("role"))
			if err := txApp.Save(membership); err != nil {
				return err
			}
		} else if err != nil {
			return err
		}

		current.Set("status", statusAccepted)
		current.Set("accepted_by", user.Id)
		if err := txApp.Save(current); err != nil {
			return err
		}
		invitation = current
		return nil
	})
	if err != nil {
		return nil, asConnectError(err)
	}

	invitationsAccepted.Add(ctx, 1)
	telemetry.Event(ctx, "invitation.accepted",
		append(invitationAttrs(invitation), attribute.String("user_id", user.Id))...)
	return &overmindv1.AcceptResponse{
		ProjectId:    invitation.GetString("project"),
		MembershipId: membership.Id,
		UserId:       user.Id,
	}, nil
}

// checkAcceptable rejects a non-pending invitation and marks a pending one
// past its expiry as expired.
func (s *invitationService) checkAcceptable(ctx context.Context, invitation *core.Record) error {
	status := invitation.GetString("status")
	if status != statusPending {
		return connect.NewError(connect.CodeFailedPrecondition, "invitation is "+status)
	}
	if invitation.GetDateTime("expires_at").Time().After(time.Now()) {
		return nil
	}
	if err := expireInvitation(ctx, s.app, invitation); err != nil {
		return internalError(err)
	}
	return connect.NewError(connect.CodeFailedPrecondition, "invitation is "+statusExpired)
}

// ensureNoPending fails when a live pending invitation exists for email. A
// pending one past its expiry is marked expired so a new one can be issued.
func (s *invitationService) ensureNoPending(ctx context.Context, projectID, email string) error {
	pending, err := findOne(s.app, invitationsCollection,
		"project = {:project} && email = {:email} && status = {:status}",
		dbx.Params{"project": projectID, "email": email, "status": statusPending})
	if errors.Is(err, errNotFound) {
		return nil
	} else if err != nil {
		return internalError(err)
	}
	if pending.GetDateTime("expires_at").Time().After(time.Now()) {
		return connect.NewError(connect.CodeAlreadyExists, "a pending invitation exists for this email")
	}
	if err := expireInvitation(ctx, s.app, pending); err != nil {
		return internalError(err)
	}
	return nil
}

func (s *invitationService) findInvitation(id string) (*core.Record, error) {
	if id == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, "invitation_id is required")
	}
	invitation, err := s.app.FindRecordById(invitationsCollection, id)
	if isNoRows(err) {
		return nil, connect.NewError(connect.CodeNotFound, "invitation not found")
	} else if err != nil {
		return nil, internalError(err)
	}
	return invitation, nil
}

func (s *invitationService) send(ctx context.Context, invitation, project, inviter *core.Record, token string) error {
	ctx, span := tracer.Start(ctx, "invitation.send_email")
	defer span.End()

	link := strings.TrimRight(s.appURL, "/") + "/accept-invite?token=" + url.QueryEscape(token)
	expiresAt := invitation.GetDateTime("expires_at").Time().UTC().Format("2 January 2006 15:04 MST")
	meta := s.app.Settings().Meta
	message := &mailer.Message{
		From:    mail.Address{Name: meta.SenderName, Address: meta.SenderAddress},
		To:      []mail.Address{{Address: invitation.GetString("email")}},
		Subject: fmt.Sprintf("Invitation to %s", project.GetString("name")),
		Text: fmt.Sprintf(
			"%s invited you to the %s project.\n\nAccept the invitation:\n%s\n\nThe link expires on %s.\n",
			inviter.Email(), project.GetString("name"), link, expiresAt,
		),
	}
	if err := s.app.NewMailClient().Send(message); err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "send failed")
		return err
	}
	return nil
}

func expireInvitation(ctx context.Context, app core.App, invitation *core.Record) error {
	invitation.Set("status", statusExpired)
	if err := app.Save(invitation); err != nil {
		return err
	}
	telemetry.Event(ctx, "invitation.expired", invitationAttrs(invitation)...)
	return nil
}

func expireInvitationsJob(app core.App) {
	ctx, span := tracer.Start(context.Background(), "expire_invitations")
	defer span.End()

	invitations, err := app.FindRecordsByFilter(invitationsCollection,
		"status = {:status} && expires_at < @now", "", 0, 0, dbx.Params{"status": statusPending})
	if err != nil {
		span.RecordError(err)
		span.SetStatus(codes.Error, "query failed")
		slog.ErrorContext(ctx, "find expired invitations", slog.Any("error", err))
		return
	}
	for _, invitation := range invitations {
		if err := expireInvitation(ctx, app, invitation); err != nil {
			span.RecordError(err)
			slog.ErrorContext(ctx, "expire invitation",
				slog.String("invitation_id", invitation.Id), slog.Any("error", err))
		}
	}
	span.SetAttributes(attribute.Int("invitations.expired", len(invitations)))
}

func normalizeEmail(raw string) (string, error) {
	email := strings.ToLower(strings.TrimSpace(raw))
	address, err := mail.ParseAddress(email)
	if err != nil || address.Address != email {
		return "", connect.NewError(connect.CodeInvalidArgument, "email is not a valid address")
	}
	return email, nil
}

// newInvitationToken returns the emailed token and the hash that is stored.
func newInvitationToken() (string, string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", "", err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	return token, hashInvitationToken(token), nil
}

func hashInvitationToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func invitationAttrs(invitation *core.Record) []attribute.KeyValue {
	return []attribute.KeyValue{
		attribute.String("invitation_id", invitation.Id),
		attribute.String("project_id", invitation.GetString("project")),
		attribute.String("role", invitation.GetString("role")),
	}
}

func invitationProto(invitation *core.Record) *overmindv1.Invitation {
	return &overmindv1.Invitation{
		Id:        invitation.Id,
		ProjectId: invitation.GetString("project"),
		Email:     invitation.GetString("email"),
		Role:      invitation.GetString("role"),
		Status:    invitation.GetString("status"),
		ExpiresAt: timestamppb.New(invitation.GetDateTime("expires_at").Time()),
	}
}
