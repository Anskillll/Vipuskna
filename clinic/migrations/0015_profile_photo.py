from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('clinic', '0014_appointmentimage'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='photo',
            field=models.ImageField(blank=True, upload_to='patient_photos/'),
        ),
    ]
