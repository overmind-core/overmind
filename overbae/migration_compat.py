"""Schema guards for the two independently applied Workshop migration histories."""

from django.core.exceptions import FieldDoesNotExist
from django.db import migrations


def has_column(schema_editor, model, field):
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(
            cursor, model._meta.db_table
        )
    return field.column in {column.name for column in columns}


class AddFieldIfMissing(migrations.AddField):
    def state_forwards(self, app_label, state):
        if self.name not in state.models[app_label, self.model_name_lower].fields:
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not has_column(schema_editor, model, model._meta.get_field(self.name)):
            super().database_forwards(app_label, schema_editor, from_state, to_state)


class RemoveFieldIfPresent(migrations.RemoveField):
    def state_forwards(self, app_label, state):
        if self.name in state.models[app_label, self.model_name_lower].fields:
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        try:
            field = model._meta.get_field(self.name)
        except FieldDoesNotExist:
            # A missing historical field is valid only if the physical column is absent too.
            with schema_editor.connection.cursor() as cursor:
                columns = {
                    column.name
                    for column in schema_editor.connection.introspection.get_table_description(
                        cursor, model._meta.db_table
                    )
                }
            if self.name in columns or self.name + "_id" in columns:
                raise RuntimeError(
                    f"Unrepresented column {model._meta.db_table}.{self.name}"
                ) from None
            return
        if has_column(schema_editor, model, field):
            super().database_forwards(app_label, schema_editor, from_state, to_state)


class RetireLegacyCellField(RemoveFieldIfPresent):
    def state_forwards(self, app_label, state):
        if (app_label, "datasetpipeline") not in state.models:
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if "overbae_datasetpipeline" not in schema_editor.connection.introspection.table_names():
            super().database_forwards(app_label, schema_editor, from_state, to_state)


class RemoveIndexIfPresent(migrations.RemoveIndex):
    def state_forwards(self, app_label, state):
        indexes = state.models[app_label, self.model_name_lower].options.get("indexes", [])
        if any(index.name == self.name for index in indexes):
            super().state_forwards(app_label, state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        with schema_editor.connection.cursor() as cursor:
            constraints = schema_editor.connection.introspection.get_constraints(
                cursor, model._meta.db_table
            )
        if self.name in constraints:
            super().database_forwards(app_label, schema_editor, from_state, to_state)
