"""Create the local load-test user, project and project API key; print them as env lines.

Run inside the API container, where Django and the database are configured:
  docker compose exec -T api python - < scripts/loadtest/bootstrap_local.py > .env.loadtest.local
Each run issues a fresh key; earlier load-test keys are deactivated.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")
django.setup()

from overbae.models import APIToken, Project, ProjectMembership, User  # noqa: E402

EMAIL = "loadtest@overmind.test"
SLUG = "loadtest"

user, _ = User.objects.get_or_create(email=EMAIL)
project, _ = Project.objects.get_or_create(slug=SLUG, defaults={"name": "Load test"})
ProjectMembership.objects.get_or_create(user=user, project=project)
APIToken.objects.filter(user=user, name="loadtest").update(is_active=False)
raw, _ = APIToken.create_for_user(user, name="loadtest", project=project)

print("LOADTEST_BASE_URL=http://localhost:8000")
print(f"LOADTEST_PROJECT_ID={project.pk}")
print(f"LOADTEST_API_KEY={raw}")
