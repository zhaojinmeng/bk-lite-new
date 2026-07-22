from django.db import migrations, models


def populate_token_lookup(apps, schema_editor):
    credential_model = apps.get_model("cmdb_enterprise", "CustomReportingCredential")
    updates = []
    for credential in credential_model.objects.all().iterator(chunk_size=500):
        token_hash = (credential.credential_data or {}).get("token_hash")
        token_lookup = str(token_hash)[:16] if token_hash else ""
        if credential.token_lookup != token_lookup:
            credential.token_lookup = token_lookup
            updates.append(credential)
    if updates:
        credential_model.objects.bulk_update(updates, ["token_lookup"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("cmdb_enterprise", "0008_pending_relation_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="customreportingcredential",
            name="token_lookup",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16, verbose_name="令牌查询前缀"),
        ),
        migrations.RunPython(populate_token_lookup, migrations.RunPython.noop),
    ]
