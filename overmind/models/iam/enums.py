"""
Enumerations for the IAM system.
"""

from enum import Enum


class RoleScope(str, Enum):
    """Enumeration of possible role scopes"""

    ORGANISATION = "organisation"
    PROJECT = "project"
    USER = "user"
    STAFF = "staff"


class InvitationStatus(str, Enum):
    """Enumeration of possible invitation statuses"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SignOnMethod(str, Enum):
    """Enumeration of possible sign-on methods for organisations"""

    PASSWORD = "password"
    SAML_2_0 = "SAML 2.0"
    OAUTH_GOOGLE = "oauth_google"
