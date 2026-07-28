from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinic', '0028_alter_clinicsettings_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='booked_for_other',
            field=models.BooleanField(
                default=False,
                verbose_name='Запис створено для іншої людини',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='doctorpatientcard',
            name='unique_doctor_patient_phone_card',
        ),
        migrations.AddConstraint(
            model_name='doctorpatientcard',
            constraint=models.UniqueConstraint(
                condition=models.Q(('patient__isnull', False)),
                fields=('doctor', 'patient'),
                name='unique_doctor_registered_patient_card',
            ),
        ),
        migrations.AddConstraint(
            model_name='doctorpatientcard',
            constraint=models.UniqueConstraint(
                condition=models.Q(('patient__isnull', True)),
                fields=(
                    'doctor',
                    'patient_phone',
                    'patient_first_name',
                    'patient_last_name',
                ),
                name='unique_doctor_unregistered_patient_card',
            ),
        ),
    ]
