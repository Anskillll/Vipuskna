from django.db import migrations, models


def preserve_service_order(apps, schema_editor):
    MedicalService = apps.get_model('clinic', 'MedicalService')
    doctor_ids = MedicalService.objects.values_list('doctor_id', flat=True).distinct()
    for doctor_id in doctor_ids:
        services = MedicalService.objects.filter(doctor_id=doctor_id).order_by('name', 'id')
        for position, service in enumerate(services):
            MedicalService.objects.filter(pk=service.pk).update(sort_order=position)


class Migration(migrations.Migration):
    dependencies = [
        ('clinic', '0016_profile_age'),
    ]

    operations = [
        migrations.AddField(
            model_name='medicalservice',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(preserve_service_order, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name='medicalservice',
            options={
                'ordering': ['sort_order', 'id'],
                'verbose_name': 'Медична послуга',
                'verbose_name_plural': 'Медичні послуги',
            },
        ),
    ]
