from django.core.management.base import BaseCommand

from clinic.telegram import send_tomorrow_reminders


class Command(BaseCommand):
    help = 'Надсилає пацієнтам Telegram-нагадування про підтверджені прийоми на завтра.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Не чекати 18:00. Зручно для ручної перевірки.',
        )

    def handle(self, *args, **options):
        sent = send_tomorrow_reminders(force=options['force'])
        self.stdout.write(self.style.SUCCESS(f'Надіслано нагадувань: {sent}'))
