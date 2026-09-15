from drf_spectacular.extensions import OpenApiAuthenticationExtension


class APITokenBackendScheme(OpenApiAuthenticationExtension):
    target_class = "overbae.api.authentication.APITokenBackend"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-Api-Key",
            "description": "API key authentication. Can also use `Authorization: Bearer <key>`.",
        }


class ClerkAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "overbae.auth.ClerkAuthentication"
    name = "ClerkBearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Clerk JWT bearer token in the Authorization header.",
        }
