from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("plugin_calendars", "0002_calendar_access_mode_calendar_employees_and_more"),
        ("staff", "0015_invitationprovisioning"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendar", name="calendar_type",
            field=models.CharField(choices=[("events", "Events"), ("assignments", "Employee assignments")], default="events", max_length=11),
        ),
        migrations.AddField(
            model_name="event", name="employees",
            field=models.ManyToManyField(blank=True, related_name="plugin_calendar_events", to="staff.employee"),
        ),
    ]
