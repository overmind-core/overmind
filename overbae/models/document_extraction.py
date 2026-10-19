from django.db import models


class DocumentExtractionPage(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    document_sha256 = models.CharField(max_length=64)
    options_sha256 = models.CharField(max_length=64)
    recipe_sha256 = models.CharField(max_length=64)
    page = models.PositiveIntegerField()
    result = models.JSONField()
    result_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "document_sha256", "options_sha256", "recipe_sha256", "page"],
                name="unique_document_ocr_page",
            )
        ]
