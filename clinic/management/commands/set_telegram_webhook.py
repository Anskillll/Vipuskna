import secrets

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from clinic.telegram import TelegramBotClient, TelegramError


class Command(BaseCommand):
    help = 'Підключає Telegram webhook для опублікованого HTTPS-сайту.'

    def add_arguments(self, parser):
        parser.add_argument('--url', help='Публічна HTTPS-адреса сайту без слеша в кінці.')

    def handle(self, *args, **options):
        base_url = (options.get('url') or settings.SITE_BASE_URL).rstrip('/')
        if not base_url.startswith('https://'):
            raise CommandError('Для webhook потрібна публічна адреса, що починається з https://.')
        if not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError(
                'Вкажіть TELEGRAM_WEBHOOK_SECRET у .env. Наприклад: '
                f'{secrets.token_urlsafe(32)}'
            )

        webhook_url = f'{base_url}{reverse("telegram_webhook")}'
        try:
            TelegramBotClient().set_webhook(webhook_url, settings.TELEGRAM_WEBHOOK_SECRET)
        except (requests.RequestException, ValueError, TelegramError) as error:
            raise CommandError(f'Не вдалося встановити webhook: {error}') from error
        self.stdout.write(self.style.SUCCESS(f'Webhook підключено: {webhook_url}'))
