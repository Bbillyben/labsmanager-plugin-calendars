from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plugin_calendars", "0006_event_exceptions_revision_event_exdates_event_rdates_and_more")]

    operations = [
        migrations.AddField(
            model_name="calendar",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
