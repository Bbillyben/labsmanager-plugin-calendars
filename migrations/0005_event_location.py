from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("plugin_calendars", "0004_alter_calendar_calendar_type_externalcalendarsource_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="location",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
