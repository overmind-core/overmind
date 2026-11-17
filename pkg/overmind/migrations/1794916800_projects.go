package migrations

import (
	"ganigeorgiev.com/pocketbase/pocketbase/core"
	m "ganigeorgiev.com/pocketbase/pocketbase/migrations"
	"ganigeorgiev.com/pocketbase/pocketbase/tools/types"
)

// Rules that join project_memberships compare several columns of the same
// joined row, so each rule uses one alias per membership lookup.
const (
	isProjectMember = `@collection.project_memberships:viewer.project ?= id && ` +
		`@collection.project_memberships:viewer.user ?= @request.auth.id`
	isProjectOwner          = isProjectMember + ` && @collection.project_memberships:viewer.role ?= "owner"`
	isMemberOfRecordProject = `@collection.project_memberships:viewer.project ?= project && ` +
		`@collection.project_memberships:viewer.user ?= @request.auth.id`
)

func init() {
	m.Register(func(app core.App) error {
		users, err := app.FindCollectionByNameOrId("users")
		if err != nil {
			return err
		}

		// The rules join project_memberships, so they are set once it exists.
		projects := core.NewBaseCollection("projects")
		projects.Fields.Add(
			&core.TextField{Name: "name", Required: true, Max: 255},
			&core.TextField{Name: "slug", Required: true, Max: 255, Pattern: `^[a-z0-9]+(?:-[a-z0-9]+)*$`},
			&core.AutodateField{Name: "created", OnCreate: true},
			&core.AutodateField{Name: "updated", OnCreate: true, OnUpdate: true},
		)
		projects.AddIndex("idx_projects_slug", true, "slug", "")
		if err := app.Save(projects); err != nil {
			return err
		}

		memberships := core.NewBaseCollection("project_memberships")
		memberships.ListRule = types.Pointer(isMemberOfRecordProject)
		memberships.ViewRule = types.Pointer(isMemberOfRecordProject)
		memberships.Fields.Add(
			&core.RelationField{Name: "user", Required: true, CollectionId: users.Id, CascadeDelete: true, MaxSelect: 1},
			&core.RelationField{Name: "project", Required: true, CollectionId: projects.Id, CascadeDelete: true, MaxSelect: 1},
			&core.SelectField{Name: "role", Required: true, Values: []string{"owner", "member"}, MaxSelect: 1},
			&core.AutodateField{Name: "created", OnCreate: true},
			&core.AutodateField{Name: "updated", OnCreate: true, OnUpdate: true},
		)
		memberships.AddIndex("idx_project_memberships_user_project", true, "user, project", "")
		if err := app.Save(memberships); err != nil {
			return err
		}

		projects.CreateRule = types.Pointer(`@request.auth.collectionName = "users"`)
		projects.ListRule = types.Pointer(isProjectMember)
		projects.ViewRule = types.Pointer(isProjectMember)
		projects.UpdateRule = types.Pointer(isProjectOwner)
		projects.DeleteRule = types.Pointer(isProjectOwner)
		if err := app.Save(projects); err != nil {
			return err
		}

		invitations := core.NewBaseCollection("project_invitations")
		invitations.ListRule = types.Pointer(isMemberOfRecordProject)
		invitations.ViewRule = types.Pointer(isMemberOfRecordProject)
		invitations.Fields.Add(
			&core.RelationField{Name: "project", Required: true, CollectionId: projects.Id, CascadeDelete: true, MaxSelect: 1},
			&core.EmailField{Name: "email", Required: true},
			&core.SelectField{Name: "role", Required: true, Values: []string{"owner", "member"}, MaxSelect: 1},
			&core.SelectField{Name: "status", Required: true, Values: []string{"pending", "accepted", "revoked", "expired"}, MaxSelect: 1},
			&core.TextField{Name: "token_hash", Required: true, Hidden: true},
			&core.DateField{Name: "expires_at", Required: true},
			&core.DateField{Name: "last_sent_at"},
			&core.RelationField{Name: "invited_by", CollectionId: users.Id, MaxSelect: 1},
			&core.RelationField{Name: "accepted_by", CollectionId: users.Id, MaxSelect: 1},
			&core.AutodateField{Name: "created", OnCreate: true},
			&core.AutodateField{Name: "updated", OnCreate: true, OnUpdate: true},
		)
		invitations.AddIndex("idx_project_invitations_pending_email", true, "project, email", "status = 'pending'")
		invitations.AddIndex("idx_project_invitations_token_hash", true, "token_hash", "")
		return app.Save(invitations)
	}, func(app core.App) error {
		for _, name := range []string{"project_invitations", "project_memberships", "projects"} {
			collection, err := app.FindCollectionByNameOrId(name)
			if err != nil {
				return err
			}
			if err := app.Delete(collection); err != nil {
				return err
			}
		}
		return nil
	})
}
