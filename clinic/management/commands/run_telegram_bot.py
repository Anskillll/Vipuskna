import time

import requests
from django.core.management.base import BaseCommand, CommandError

from clinic.telegram import (
    TelegramBotClient,
    TelegramError,
    process_update,
    send_tomorrow_reminders,
)


class Command(BaseCommand):
    help = 'Запускає Telegram-бота у режимі long polling і перевіряє нагадування.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Виконати один короткий цикл для перевірки та завершити роботу.',
        )

    def handle(self, *args, **options):
        try:
            client = TelegramBotClient()
            client.delete_webhook()
            bot = client.get_me()
        except (requests.RequestException, ValueError, TelegramError) as error:
            raise CommandError(f'Не вдалося підключитися до Telegram: {error}') from error

        self.stdout.write(self.style.SUCCESS(
            f'Бот @{bot.get("username", "")} запущений. Для зупинки натисніть Ctrl+C.'
        ))
        offset = None
        last_reminder_check = 0.0

        try:
            while True:
                try:
                    updates = client.get_updates(
                        offset=offset,
                        timeout=1 if options['once'] else 25,
                    )
                    for update in updates:
                        offset = update['update_id'] + 1
                        process_update(update, client=client)

                    now = time.monotonic()
                    if now - last_reminder_check >= 60 or options['once']:
                        sent = send_tomorrow_reminders()
                        if sent:
                            self.stdout.write(f'Надіслано нагадувань: {sent}')
                        last_reminder_check = now
                except (requests.RequestException, ValueError, TelegramError) as error:
                    self.stderr.write(f'Помилка Telegram: {error}. Повтор через 5 секунд.')
                    if options['once']:
                        raise CommandError(str(error)) from error
                    time.sleep(5)

                if options['once']:
                    break
        except KeyboardInterrupt:
            self.stdout.write('\nTelegram-бот зупинено.')
