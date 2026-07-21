import html
import secrets
from datetime import time, timedelta
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    Appointment,
    TelegramConnection,
    TelegramLinkToken,
    TelegramNotification,
)


LINK_TOKEN_LIFETIME = timedelta(minutes=10)
REMINDER_SEND_FROM = time(18, 0)


class TelegramError(Exception):
    pass


class TelegramBotClient:
    api_root = 'https://api.telegram.org'

    def __init__(self, token=None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        if not self.token:
            raise TelegramError('Не вказано TELEGRAM_BOT_TOKEN.')

    def request(self, method, payload=None, timeout=12):
        try:
            response = requests.post(
                f'{self.api_root}/bot{self.token}/{method}',
                json=payload or {},
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise TelegramError('Не вдалося з’єднатися з Telegram API.') from error
        if not response.ok:
            raise TelegramError(f'Telegram API повернув HTTP {response.status_code}.')
        try:
            data = response.json()
        except ValueError as error:
            raise TelegramError('Telegram API повернув некоректну відповідь.') from error
        if not data.get('ok'):
            raise TelegramError(data.get('description') or 'Telegram повернув помилку.')
        return data.get('result')

    def get_me(self):
        return self.request('getMe')

    def send_message(self, chat_id, text, reply_markup=None):
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        if reply_markup:
            payload['reply_markup'] = reply_markup
        return self.request('sendMessage', payload)

    def get_updates(self, offset=None, timeout=25):
        payload = {
            'timeout': timeout,
            'allowed_updates': ['message'],
        }
        if offset is not None:
            payload['offset'] = offset
        return self.request('getUpdates', payload, timeout=timeout + 10)

    def delete_webhook(self):
        return self.request('deleteWebhook', {'drop_pending_updates': False})

    def set_webhook(self, url, secret_token):
        return self.request(
            'setWebhook',
            {
                'url': url,
                'secret_token': secret_token,
                'allowed_updates': ['message'],
            },
        )


def telegram_is_configured():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)


def create_link_url(user):
    if not telegram_is_configured():
        raise TelegramError('Telegram-бот ще не налаштований.')

    TelegramLinkToken.objects.filter(user=user, used_at__isnull=True).delete()
    link_token = TelegramLinkToken.objects.create(
        user=user,
        token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + LINK_TOKEN_LIFETIME,
    )
    return (
        f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}'
        f'?start={quote(link_token.token)}'
    )


def process_update(update, client=None):
    message = update.get('message') or {}
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    text = (message.get('text') or '').strip()
    chat_id = chat.get('id')
    if not chat_id or chat.get('type') != 'private':
        return False

    client = client or TelegramBotClient()
    if text == '/stop':
        updated = TelegramConnection.objects.filter(chat_id=chat_id).update(is_active=False)
        reply = (
            'Сповіщення вимкнено. Підключити їх знову можна у своєму кабінеті на сайті.'
            if updated
            else 'Цей Telegram не підключений до MedClinic.'
        )
        client.send_message(chat_id, reply)
        return bool(updated)

    if text in {'/help', '/start'}:
        client.send_message(
            chat_id,
            'Щоб підключити сповіщення, відкрийте свій кабінет MedClinic та натисніть '
            '«Приєднати Telegram-бота».',
        )
        return False

    if not text.startswith('/start '):
        return False

    token_value = text.split(maxsplit=1)[1].strip()
    with transaction.atomic():
        link_token = (
            TelegramLinkToken.objects.select_for_update()
            .select_related('user')
            .filter(token=token_value, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if link_token is None:
            client.send_message(
                chat_id,
                'Посилання вже використане або застаріло. Створіть нове у своєму кабінеті.',
            )
            return False

        occupied = TelegramConnection.objects.filter(chat_id=chat_id).exclude(user=link_token.user).first()
        if occupied and occupied.is_active:
            client.send_message(
                chat_id,
                'Цей Telegram уже підключений до іншого профілю MedClinic. '
                'Спочатку надішліть боту команду /stop.',
            )
            return False
        if occupied:
            occupied.delete()

        TelegramConnection.objects.update_or_create(
            user=link_token.user,
            defaults={
                'chat_id': chat_id,
                'username': sender.get('username', ''),
                'first_name': sender.get('first_name', ''),
                'is_active': True,
            },
        )
        link_token.used_at = timezone.now()
        link_token.save(update_fields=['used_at'])

    client.send_message(
        chat_id,
        '<b>Telegram успішно підключено до MedClinic.</b>\n'
        'Тепер сюди надходитимуть повідомлення про записи та нагадування.',
    )
    return True


def _site_url(route_name, args=None):
    return f'{settings.SITE_BASE_URL}{reverse(route_name, args=args)}'


def _appointment_lines(appointment):
    service_name = appointment.service.name if appointment.service else 'Прийом лікаря'
    location = ', '.join(part for part in (appointment.city, appointment.address) if part)
    return (
        f'Послуга: <b>{html.escape(service_name)}</b>\n'
        f'Дата: <b>{appointment.date:%d.%m.%Y}</b>\n'
        f'Час: <b>{appointment.time:%H:%M}</b>\n'
        f'Місце: {html.escape(location or "Не вказано")}'
    )


def _deliver(recipient, appointment, kind, event_key, text, url=None):
    if not telegram_is_configured() or recipient is None:
        return False

    connection = TelegramConnection.objects.filter(user=recipient, is_active=True).first()
    if connection is None:
        return False

    notification, _ = TelegramNotification.objects.get_or_create(
        event_key=event_key,
        defaults={
            'appointment': appointment,
            'recipient': recipient,
            'kind': kind,
        },
    )
    if notification.status == TelegramNotification.STATUS_SENT:
        return False

    reply_markup = None
    if url:
        reply_markup = {
            'inline_keyboard': [[{'text': 'Відкрити на сайті', 'url': url}]],
        }

    try:
        TelegramBotClient().send_message(connection.chat_id, text, reply_markup=reply_markup)
    except (requests.RequestException, ValueError, TelegramError) as error:
        notification.status = TelegramNotification.STATUS_FAILED
        notification.error = str(error)[:1000]
        notification.save(update_fields=['status', 'error'])
        return False

    notification.status = TelegramNotification.STATUS_SENT
    notification.error = ''
    notification.sent_at = timezone.now()
    notification.save(update_fields=['status', 'error', 'sent_at'])
    return True


def notify_doctor_new_request(appointment, event='created'):
    patient_name = html.escape(appointment.patient_name)
    text = (
        '<b>Нова заявка на прийом</b>\n'
        f'Пацієнт: <b>{patient_name}</b>\n'
        f'{_appointment_lines(appointment)}'
    )
    return _deliver(
        appointment.doctor.user,
        appointment,
        'new_request',
        f'new_request:{appointment.pk}:{event}',
        text,
        _site_url('doctor_appointment_detail', [appointment.pk]),
    )


def notify_patient_status(appointment, event, heading):
    if appointment.patient_id is None:
        return False
    text = f'<b>{html.escape(heading)}</b>\nЛікар: <b>{html.escape(appointment.doctor.full_name)}</b>\n{_appointment_lines(appointment)}'
    return _deliver(
        appointment.patient,
        appointment,
        event,
        f'patient_status:{appointment.pk}:{event}',
        text,
        _site_url('patient_appointment_detail', [appointment.pk]),
    )


def notify_doctor_patient_action(appointment, event, heading):
    text = (
        f'<b>{html.escape(heading)}</b>\n'
        f'Пацієнт: <b>{html.escape(appointment.patient_name)}</b>\n'
        f'{_appointment_lines(appointment)}'
    )
    return _deliver(
        appointment.doctor.user,
        appointment,
        event,
        f'doctor_action:{appointment.pk}:{event}',
        text,
        _site_url('doctor_appointment_detail', [appointment.pk]),
    )


def send_tomorrow_reminders(now=None, force=False):
    now = timezone.localtime(now or timezone.now())
    if not force and now.time() < REMINDER_SEND_FROM:
        return 0

    tomorrow = now.date() + timedelta(days=1)
    appointments = (
        Appointment.objects.filter(
            date=tomorrow,
            status=Appointment.STATUS_APPROVED,
            patient__isnull=False,
        )
        .select_related('patient', 'doctor__user', 'service')
        .order_by('time')
    )
    sent = 0
    for appointment in appointments:
        text = (
            '<b>Нагадування про прийом завтра</b>\n'
            f'Лікар: <b>{html.escape(appointment.doctor.full_name)}</b>\n'
            f'{_appointment_lines(appointment)}'
        )
        if _deliver(
            appointment.patient,
            appointment,
            'day_before_reminder',
            f'reminder:{appointment.pk}:{appointment.date.isoformat()}',
            text,
            _site_url('patient_appointment_detail', [appointment.pk]),
        ):
            sent += 1
    return sent
