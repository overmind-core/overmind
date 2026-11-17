package overmind

import (
	"log/slog"

	"ganigeorgiev.com/pocketbase/pocketbase/core"
	"go.opentelemetry.io/otel/attribute"

	"github.com/overmind/overmind/store"
	"github.com/overmind/overmind/telemetry"
)

const (
	usersCollection       = "users"
	projectsCollection    = "projects"
	membershipsCollection = "project_memberships"
	invitationsCollection = "project_invitations"

	roleOwner  = "owner"
	roleMember = "member"
)

func bindProjectHooks(app core.App, projects *store.Store) {
	// The owner membership and the DuckDB file are part of the create: if
	// either fails the project row is rolled back and the request fails.
	app.OnRecordCreateRequest(projectsCollection).BindFunc(func(e *core.RecordRequestEvent) error {
		if e.Auth == nil || e.Auth.Collection().Name != usersCollection {
			return e.BadRequestError("A project is created by a user account.", nil)
		}
		owner := e.Auth
		ctx := e.Request.Context()

		storeAttempted := false
		err := e.App.RunInTransaction(func(txApp core.App) error {
			e.App = txApp
			if err := e.Next(); err != nil {
				return err
			}

			memberships, err := txApp.FindCachedCollectionByNameOrId(membershipsCollection)
			if err != nil {
				return err
			}
			membership := core.NewRecord(memberships)
			membership.Set("user", owner.Id)
			membership.Set("project", e.Record.Id)
			membership.Set("role", roleOwner)
			if err := txApp.Save(membership); err != nil {
				return err
			}

			storeAttempted = true
			return projects.Create(ctx, e.Record.Id)
		})
		if err != nil {
			if storeAttempted {
				if removeErr := projects.Remove(e.Record.Id); removeErr != nil {
					slog.ErrorContext(ctx, "remove project store after failed create",
						slog.String("project_id", e.Record.Id), slog.Any("error", removeErr))
				}
			}
			return err
		}

		telemetry.Event(ctx, "project.created",
			attribute.String("project_id", e.Record.Id),
			attribute.String("user_id", owner.Id),
		)
		return nil
	})

	app.OnRecordAfterDeleteSuccess(projectsCollection).BindFunc(func(e *core.RecordEvent) error {
		projectID := e.Record.Id
		if err := projects.Remove(projectID); err != nil {
			slog.ErrorContext(e.Context, "remove project store",
				slog.String("project_id", projectID), slog.Any("error", err))
		}
		telemetry.Event(e.Context, "project.deleted", attribute.String("project_id", projectID))
		return e.Next()
	})
}
