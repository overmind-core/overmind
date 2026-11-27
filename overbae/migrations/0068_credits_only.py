from django.db import migrations, models
from django.db.models import F, Value
from django.db.models.functions import Concat, Substr

# Only live-mode Checkout sessions carried real money. Every other credit row,
# including test-mode sessions and subscription invoices, was handed out for free.
LIVE_PURCHASE_PREFIX = "topup:cs_live_"
SIGNUP_PREFIX = "free-credits:"


def label_credit_rows(apps, schema_editor):
    BillingTelemetry = apps.get_model("overbae", "BillingTelemetry")
    rows = BillingTelemetry.objects.using(schema_editor.connection.alias)
    rows.filter(service="stripe-topup", idempotency_key__startswith=LIVE_PURCHASE_PREFIX).update(
        service="purchase"
    )
    rows.filter(service="stripe-topup").update(service="grant")
    rows.filter(service="free-credits", idempotency_key__startswith=SIGNUP_PREFIX).update(
        idempotency_key=Concat(
            Value("signup:"),
            Substr(F("idempotency_key"), len(SIGNUP_PREFIX) + 1),
            output_field=models.CharField(),
        )
    )
    rows.filter(service="free-credits").update(service="grant")


class Migration(migrations.Migration):
    dependencies = [("overbae", "0067_delete_retired_beat_tasks")]

    operations = [
        migrations.AlterField(
            model_name="billingtelemetry",
            name="service",
            field=models.CharField(
                choices=[
                    ("grant", "Grant"),
                    ("purchase", "Purchase"),
                    ("finetuning-job", "Finetuning job"),
                    ("inference", "Inference"),
                    ("inference-ft-model", "Fine-tuned inference"),
                    ("dedicated-inference", "Dedicated inference"),
                    ("data-workshop", "Data workshop"),
                    ("evaluation", "Evaluation"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(label_credit_rows, migrations.RunPython.noop),
        migrations.DeleteModel(name="Subscription"),
    ]
