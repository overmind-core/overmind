import uuid

from django.conf import settings
from django.db import models


class Feedback(models.Model):
    class Category(models.TextChoices):
        BUG = "bug", "Report Bug"
        IMPROVEMENT = "improvement", "Suggest Improvement"
        QUESTION = "question", "Ask a Question"
        LOVE = "love", "Share What You Love"
        FEATURE = "feature", "Request Feature"
        PERFORMANCE = "performance", "Performance Issue"
        ACCESSIBILITY = "accessibility", "Accessibility Issue"
        SECURITY = "security", "Security Concern"
        DOCUMENTATION = "documentation", "Documentation Issue"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_submissions",
    )
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.OTHER,
    )
    feedback = models.TextField()
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Feedback"
        verbose_name_plural = "Feedback"

    def __str__(self):
        user_label = self.user.email if self.user_id else "anonymous"
        preview = self.feedback[:60].replace("\n", " ")
        return f"[{self.get_category_display()}] {user_label} — {preview}"
