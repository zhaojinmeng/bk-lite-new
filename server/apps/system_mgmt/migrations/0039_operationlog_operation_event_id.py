from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("system_mgmt", "0038_imnotificationchannel_imnotificationusermapping"),
    ]

    operations = [
        migrations.AddField(model_name="operationlog", name="operation_event_id", field=models.UUIDField(blank=True, null=True, unique=True),),
    ]
