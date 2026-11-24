from django.db import models


class Leased(models.Model):
    """A background job one worker holds at a time. ``attempt`` names the holder and
    fences its writes, its heartbeat renews ``lease_until``, and ``attempts`` bounds
    how often a lost job starts again."""

    attempt = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempts = models.SmallIntegerField(db_default=0)

    class Meta:
        abstract = True
