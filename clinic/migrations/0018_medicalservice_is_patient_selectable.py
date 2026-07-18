from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('clinic', '0017_medicalservice_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='medicalservice',
            name='is_patient_selectable',
            field=models.BooleanField(default=True),
        ),
    ]
