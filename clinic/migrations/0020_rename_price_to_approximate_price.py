import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinic', '0019_medicalservice_description_medicalserviceimage'),
    ]

    operations = [
        migrations.RenameField(
            model_name='medicalservice',
            old_name='price',
            new_name='approximate_price',
        ),
        migrations.AlterField(
            model_name='medicalservice',
            name='approximate_price',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
