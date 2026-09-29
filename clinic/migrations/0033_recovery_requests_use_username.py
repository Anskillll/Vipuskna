from django.conf import settings
from django.db import migrations, models


def replace_known_emails_with_usernames(apps, schema_editor):
    RecoveryRequest = apps.get_model('clinic', 'PasswordRecoveryRequest')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    database = schema_editor.connection.alias
    for request in RecoveryRequest.objects.using(database).all().iterator():
        user = User.objects.using(database).filter(email__iexact=request.username).first()
        if user:
            request.username = user.username
            request.save(update_fields=['username'])


class Migration(migrations.Migration):

    dependencies = [
        ('clinic', '0032_passwordrecoveryrequest'),
    ]

    operations = [
        migrations.RenameField(
            model_name='passwordrecoveryrequest',
            old_name='email',
            new_name='username',
        ),
        migrations.RunPython(replace_known_emails_with_usernames, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='passwordrecoveryrequest',
            name='username',
            field=models.CharField(max_length=254, verbose_name='Логін'),
        ),
    ]
