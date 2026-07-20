import shutil
from pathlib import Path

from django.conf import settings
from django.db import migrations


PRIVATE_DIRECTORIES = (
    'appointment_images',
    'patient_records',
)


def move_files(source_root, target_root):
    for directory in PRIVATE_DIRECTORIES:
        source_directory = source_root / directory
        if not source_directory.exists():
            continue

        for source_file in source_directory.rglob('*'):
            if not source_file.is_file():
                continue
            relative_path = source_file.relative_to(source_root)
            target_file = target_root / relative_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if not target_file.exists():
                shutil.copy2(source_file, target_file)
            source_file.unlink()


def move_to_private_storage(apps, schema_editor):
    move_files(Path(settings.MEDIA_ROOT), Path(settings.PRIVATE_MEDIA_ROOT))


def move_back_to_public_storage(apps, schema_editor):
    move_files(Path(settings.PRIVATE_MEDIA_ROOT), Path(settings.MEDIA_ROOT))


class Migration(migrations.Migration):
    dependencies = [
        ('clinic', '0025_appointmentvideo_auditlog_medicalservicevideo_and_more'),
    ]

    operations = [
        migrations.RunPython(
            move_to_private_storage,
            reverse_code=move_back_to_public_storage,
        ),
    ]
