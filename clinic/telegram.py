import html
import secrets
from datetime import time, timedelta
from math import ceil
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    Appointment,
    AuditLog,
    TelegramConnection,
    TelegramLoginChallenge,
    TelegramLinkToken,
    TelegramNotification,
)


LINK_TOKEN_LIFETIME = timedelta(minutes=10)
LOGIN_CHALLENGE_LIFETIME = timedelta(minutes=5)
LOGIN_CHALLENGE_RETRY_DELAY = timedelta(minutes=1)
REMINDER_SEND_FROM = time(18, 0)
ADMIN_BROADCAST_MAX_LENGTH = 3500
DOCTOR_REQUEST_CALLBACK_PREFIX = 'doctor_request'
PATIENT_LOGIN_CALLBACK_PREFIX = 'patient_login'


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
            'allowed_updates': ['message', 'callback_query'],
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
                'allowed_updates': ['message', 'callback_query'],
            },
        )

    def answer_callback_query(self, callback_query_id, text='', show_alert=False):
        return self.request(
            'answerCallbackQuery',
            {
                'callback_query_id': callback_query_id,
                'text': text,
                'show_alert': show_alert,
            },
        )

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        return self.request(
            'editMessageReplyMarkup',
            {
                'chat_id': chat_id,
                'message_id': message_id,
                'reply_markup': reply_markup,
            },
        )


def telegram_is_configured():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)


def send_admin_broadcast(connections, message):
    """Send a clinic announcement to the supplied active Telegram connections."""
    if not telegram_is_configured():
        raise TelegramError('Telegram-бот не налаштований на сервері.')

    message = (message or '').strip()
    if not message:
        raise TelegramError('Текст повідомлення не може бути порожнім.')
    if len(message) > ADMIN_BROADCAST_MAX_LENGTH:
        raise TelegramError(
            f'Повідомлення не може бути довшим за {ADMIN_BROADCAST_MAX_LENGTH} символів.'
        )

    text = f'<b>Повідомлення від клініки</b>\n\n{html.escape(message)}'
    client = TelegramBotClient()
    sent_count = 0
    failures = []

    for connection in connections:
        try:
            client.send_message(connection.chat_id, text)
        except (requests.RequestException, ValueError, TelegramError) as error:
            failures.append((connection, str(error)))
        else:
            sent_count += 1

    return sent_count, failures


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


def create_phone_login_challenge(user):
    if not telegram_is_configured():
        raise TelegramError('Вхід через Telegram зараз недоступний. Спробуйте увійти через Google.')

    now = timezone.now()
    with transaction.atomic():
        connection = (
            TelegramConnection.objects.select_for_update()
            .filter(user=user, user__is_active=True, is_active=True)
            .first()
        )
        if connection is None:
            raise TelegramError(
                'Вхід за номером недоступний: Telegram-бот не прив’язаний до цього акаунта. '
                'Увійдіть через Google, відкрийте свій кабінет і прив’яжіть бота — після цього '
                'зможете входити за номером телефону.'
            )

        recent_challenge = (
            TelegramLoginChallenge.objects.filter(
                user=user,
                status=TelegramLoginChallenge.STATUS_PENDING,
                expires_at__gt=now,
            )
            .order_by('-created_at')
            .first()
        )
        if (
            recent_challenge is not None
            and recent_challenge.created_at > now - LOGIN_CHALLENGE_RETRY_DELAY
        ):
            raise TelegramError(
                'Запит на підтвердження вже надсилали. Зачекайте хвилину та перевірте Telegram.'
            )

        TelegramLoginChallenge.objects.filter(
            user=user,
            status__in=[
                TelegramLoginChallenge.STATUS_PENDING,
                TelegramLoginChallenge.STATUS_APPROVED,
            ],
            consumed_at__isnull=True,
        ).update(
            status=TelegramLoginChallenge.STATUS_EXPIRED,
            resolved_at=now,
        )
        challenge = TelegramLoginChallenge.objects.create(
            user=user,
            token=secrets.token_urlsafe(18),
            expires_at=now + LOGIN_CHALLENGE_LIFETIME,
        )

    requested_at = timezone.localtime(now).strftime('%d.%m.%Y о %H:%M')
    text = (
        '<b>Підтвердження входу в MedClinic</b>\n\n'
        'Надійшов запит на вхід до вашого кабінету за номером телефону.\n'
        f'Час запиту: <b>{requested_at}</b>\n\n'
        'Підтверджуйте вхід лише якщо саме ви щойно відкрили сайт клініки.'
    )
    reply_markup = {
        'inline_keyboard': [[
            {
                'text': '✅ Підтвердити вхід',
                'callback_data': (
                    f'{PATIENT_LOGIN_CALLBACK_PREFIX}:approve:{challenge.token}'
                ),
            },
            {
                'text': '❌ Це не я',
                'callback_data': (
                    f'{PATIENT_LOGIN_CALLBACK_PREFIX}:reject:{challenge.token}'
                ),
            },
        ]],
    }
    try:
        TelegramBotClient().send_message(
            connection.chat_id,
            text,
            reply_markup=reply_markup,
        )
    except (requests.RequestException, ValueError, TelegramError) as error:
        TelegramLoginChallenge.objects.filter(pk=challenge.pk).update(
            status=TelegramLoginChallenge.STATUS_EXPIRED,
            resolved_at=timezone.now(),
        )
        raise TelegramError(
            'Не вдалося надіслати підтвердження в Telegram. Спробуйте ще раз пізніше.'
        ) from error

    return challenge


def _process_patient_login_callback(callback_query, client):
    callback_id = callback_query.get('id', '')
    data = callback_query.get('data', '')
    message = callback_query.get('message') or {}
    chat = message.get('chat') or {}
    sender = callback_query.get('from') or {}
    chat_id = chat.get('id')
    message_id = message.get('message_id')
    parts = data.split(':')

    if (
        len(parts) != 3
        or parts[0] != PATIENT_LOGIN_CALLBACK_PREFIX
        or parts[1] not in {'approve', 'reject'}
        or not parts[2]
        or not callback_id
        or not chat_id
        or chat.get('type') != 'private'
        or sender.get('id') != chat_id
    ):
        if callback_id:
            client.answer_callback_query(
                callback_id,
                'Цей запит на вхід не вдалося розпізнати.',
                show_alert=True,
            )
        return False

    now = timezone.now()
    response_text = ''
    processed = False
    remove_actions = False
    try:
        with transaction.atomic():
            challenge = (
                TelegramLoginChallenge.objects.select_for_update()
                .select_related('user')
                .filter(token=parts[2])
                .first()
            )
            if challenge is None:
                response_text = 'Запит не знайдено або він уже застарів.'
            elif not TelegramConnection.objects.filter(
                user=challenge.user,
                chat_id=chat_id,
                is_active=True,
            ).exists():
                response_text = 'Цей запит належить іншому Telegram-акаунту.'
            elif challenge.status != TelegramLoginChallenge.STATUS_PENDING:
                response_text = 'Цей запит на вхід уже опрацьовано.'
                remove_actions = True
            elif challenge.expires_at <= now:
                challenge.status = TelegramLoginChallenge.STATUS_EXPIRED
                challenge.resolved_at = now
                challenge.save(update_fields=['status', 'resolved_at'])
                response_text = 'Час підтвердження минув. Створіть новий запит на сайті.'
                remove_actions = True
            else:
                challenge.status = (
                    TelegramLoginChallenge.STATUS_APPROVED
                    if parts[1] == 'approve'
                    else TelegramLoginChallenge.STATUS_REJECTED
                )
                challenge.resolved_at = now
                challenge.save(update_fields=['status', 'resolved_at'])
                processed = True
                remove_actions = True
                response_text = (
                    'Вхід підтверджено. Поверніться до браузера.'
                    if parts[1] == 'approve'
                    else 'Запит на вхід відхилено.'
                )
    except DatabaseError:
        response_text = 'База даних тимчасово недоступна. Спробуйте ще раз.'

    client.answer_callback_query(
        callback_id,
        response_text,
        show_alert=not processed,
    )
    if message_id and remove_actions:
        client.edit_message_reply_markup(
            chat_id,
            message_id,
            {'inline_keyboard': []},
        )
    return processed


def _site_only_markup(appointment):
    return {
        'inline_keyboard': [[{
            'text': 'Деталі на сайті',
            'url': _site_url('doctor_appointment_detail', [appointment.pk]),
        }]],
    }


def _audit_telegram_decision(actor, action, appointment):
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=str(appointment._meta.verbose_name),
        target_id=str(appointment.pk),
        target_label=str(appointment)[:240],
        details='Дію виконано кнопкою Telegram-бота.',
    )


def _process_doctor_request_callback(callback_query, client):
    callback_id = callback_query.get('id', '')
    data = callback_query.get('data', '')
    message = callback_query.get('message') or {}
    chat = message.get('chat') or {}
    sender = callback_query.get('from') or {}
    chat_id = chat.get('id')
    message_id = message.get('message_id')

    parts = data.split(':')
    if (
        len(parts) != 3
        or parts[0] != DOCTOR_REQUEST_CALLBACK_PREFIX
        or parts[1] not in {'approve', 'reject'}
        or not parts[2].isdigit()
        or not callback_id
        or not chat_id
        or chat.get('type') != 'private'
        or sender.get('id') != chat_id
    ):
        if callback_id:
            client.answer_callback_query(
                callback_id,
                'Цю дію не вдалося розпізнати.',
                show_alert=True,
            )
        return False

    try:
        connection = (
            TelegramConnection.objects.filter(chat_id=chat_id, is_active=True)
            .select_related('user')
            .first()
        )
    except DatabaseError:
        client.answer_callback_query(
            callback_id,
            'База даних тимчасово недоступна. Спробуйте ще раз.',
            show_alert=True,
        )
        return False
    if connection is None:
        client.answer_callback_query(
            callback_id,
            'Спочатку підключіть Telegram у своєму кабінеті.',
            show_alert=True,
        )
        return False

    action = parts[1]
    appointment_id = int(parts[2])
    notification = None
    response_text = ''
    appointment = None
    remove_actions = False

    try:
        with transaction.atomic():
            appointment = (
                # PostgreSQL cannot lock nullable patient/service outer joins.
                Appointment.objects.select_for_update(of=('self',))
                .select_related('doctor__user', 'patient', 'service')
                .filter(pk=appointment_id, doctor__user=connection.user)
                .first()
            )
            if appointment is None:
                response_text = 'Ця заявка не належить вашому профілю лікаря.'
            elif appointment.status != Appointment.STATUS_PENDING:
                response_text = 'Цю заявку вже опрацьовано.'
                remove_actions = True
            elif action == 'reject':
                from .views import ensure_patient_card_from_appointment

                appointment.status = Appointment.STATUS_REJECTED
                appointment.save(update_fields=['status'])
                ensure_patient_card_from_appointment(appointment)
                _audit_telegram_decision(
                    connection.user,
                    'Відхилено заявку лікарем у Telegram',
                    appointment,
                )
                notification = (
                    'rejected',
                    'Лікар відхилив заявку на прийом',
                )
                response_text = 'Заявку відхилено.'
                remove_actions = True
            else:
                from .views import (
                    appointment_conflicts,
                    ensure_patient_card_from_appointment,
                    is_past_appointment,
                    patient_appointment_conflicts,
                    schedule_for_date,
                )

                schedule = schedule_for_date(appointment.doctor, appointment.date)
                if schedule is None:
                    response_text = 'Для цього дня більше немає робочого графіка.'
                else:
                    duration_minutes = (
                        appointment.duration_minutes_exact
                        or appointment.duration_slots * schedule.slot_minutes
                    )
                    duration_slots = ceil(duration_minutes / schedule.slot_minutes)
                    if is_past_appointment(appointment.date, appointment.time):
                        response_text = 'Не можна підтвердити заявку на минулий час.'
                    elif appointment_conflicts(
                        appointment.doctor,
                        appointment.date,
                        appointment.time,
                        duration_slots=duration_slots,
                        duration_minutes=duration_minutes,
                        exclude_id=appointment.id,
                    ):
                        response_text = 'Цей час уже зайнятий. Перевірте заявку на сайті.'
                    elif patient_appointment_conflicts(
                        appointment.patient,
                        appointment.date,
                        appointment.time,
                        duration_minutes,
                        patient_phone=appointment.patient_phone,
                        exclude_id=appointment.id,
                    ):
                        response_text = 'Пацієнт уже має інший запис на цей час.'
                    else:
                        appointment.duration_slots = duration_slots
                        appointment.duration_minutes_exact = duration_minutes
                        appointment.status = Appointment.STATUS_APPROVED
                        appointment.approved_at = timezone.now()
                        appointment.save(update_fields=[
                            'duration_slots',
                            'duration_minutes_exact',
                            'status',
                            'approved_at',
                        ])
                        ensure_patient_card_from_appointment(appointment)
                        _audit_telegram_decision(
                            connection.user,
                            'Підтверджено заявку лікарем у Telegram',
                            appointment,
                        )
                        notification = (
                            'approved',
                            'Лікар підтвердив вашу заявку',
                        )
                        response_text = 'Заявку підтверджено.'
                        remove_actions = True
    except IntegrityError:
        response_text = 'Цей час щойно зайняли. Перевірте заявку на сайті.'
    except DatabaseError:
        response_text = 'База даних тимчасово недоступна. Спробуйте ще раз.'

    succeeded = notification is not None
    client.answer_callback_query(
        callback_id,
        response_text,
        show_alert=not succeeded,
    )
    if appointment is not None and message_id and remove_actions:
        client.edit_message_reply_markup(
            chat_id,
            message_id,
            _site_only_markup(appointment),
        )
    if notification is not None:
        notify_patient_status(appointment, *notification)
    return succeeded


def process_update(update, client=None):
    client = client or TelegramBotClient()
    callback_query = update.get('callback_query') or {}
    if callback_query:
        callback_data = callback_query.get('data', '')
        if callback_data.startswith(f'{PATIENT_LOGIN_CALLBACK_PREFIX}:'):
            return _process_patient_login_callback(callback_query, client)
        return _process_doctor_request_callback(callback_query, client)

    message = update.get('message') or {}
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    text = (message.get('text') or '').strip()
    chat_id = chat.get('id')
    if not chat_id or chat.get('type') != 'private':
        return False

    if text == '/stop':
        updated = TelegramConnection.objects.filter(chat_id=chat_id).update(is_active=False)
        reply = (
            'Сповіщення вимкнено. Підключити їх знову можна у своєму кабінеті на сайті.'
            if updated
            else 'Цей Telegram не підключений до MedClinic.'
        )
        client.send_message(chat_id, reply)
        return bool(updated)

    if text == '/start':
        connection = (
            TelegramConnection.objects.filter(chat_id=chat_id, is_active=True)
            .select_related('user')
            .first()
        )
        if connection is not None:
            client.send_message(
                chat_id,
                '<b>Ви вже зареєстровані в MedClinic.</b>\n'
                'Telegram-сповіщення про записи та нагадування увімкнені.',
            )
            return True

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


def _deliver(recipient, appointment, kind, event_key, text, url=None, actions=None):
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

    keyboard = []
    if actions:
        keyboard.append(actions)
    if url:
        keyboard.append([{'text': 'Деталі на сайті', 'url': url}])
    reply_markup = {'inline_keyboard': keyboard} if keyboard else None

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
    owner_line = (
        f'Заявку створив: <b>{html.escape(appointment.booking_owner_name)}</b>\n'
        if appointment.booked_for_other
        else ''
    )
    text = (
        '<b>Нова заявка на прийом</b>\n'
        f'Пацієнт: <b>{patient_name}</b>\n'
        f'{owner_line}'
        f'{_appointment_lines(appointment)}'
    )
    return _deliver(
        appointment.doctor.user,
        appointment,
        'new_request',
        f'new_request:{appointment.pk}:{event}',
        text,
        _site_url('doctor_appointment_detail', [appointment.pk]),
        actions=[
            {
                'text': '✅ Прийняти',
                'callback_data': (
                    f'{DOCTOR_REQUEST_CALLBACK_PREFIX}:approve:{appointment.pk}'
                ),
            },
            {
                'text': '❌ Відхилити',
                'callback_data': (
                    f'{DOCTOR_REQUEST_CALLBACK_PREFIX}:reject:{appointment.pk}'
                ),
            },
        ],
    )


def notify_patient_status(appointment, event, heading):
    if appointment.patient_id is None:
        return False
    visitor_line = (
        f'Записано для: <b>{html.escape(appointment.patient_name)}</b>\n'
        if appointment.booked_for_other
        else ''
    )
    text = (
        f'<b>{html.escape(heading)}</b>\n'
        f'Лікар: <b>{html.escape(appointment.doctor.full_name)}</b>\n'
        f'{visitor_line}'
        f'{_appointment_lines(appointment)}'
    )
    return _deliver(
        appointment.patient,
        appointment,
        event,
        f'patient_status:{appointment.pk}:{event}',
        text,
        _site_url('patient_appointment_detail', [appointment.pk]),
    )


def notify_doctor_patient_action(appointment, event, heading):
    owner_line = (
        f'Заявку створив: <b>{html.escape(appointment.booking_owner_name)}</b>\n'
        if appointment.booked_for_other
        else ''
    )
    text = (
        f'<b>{html.escape(heading)}</b>\n'
        f'Пацієнт: <b>{html.escape(appointment.patient_name)}</b>\n'
        f'{owner_line}'
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
        visitor_line = (
            f'Записано для: <b>{html.escape(appointment.patient_name)}</b>\n'
            if appointment.booked_for_other
            else ''
        )
        text = (
            '<b>Нагадування про прийом завтра</b>\n'
            f'Лікар: <b>{html.escape(appointment.doctor.full_name)}</b>\n'
            f'{visitor_line}'
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
