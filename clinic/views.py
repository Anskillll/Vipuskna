from datetime import datetime, timedelta
from functools import wraps
import hmac
import json
from math import ceil
import mimetypes
import requests
from urllib.parse import urlencode

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.static import serve

from .forms import (
    AdminDoctorCreateForm,
    AdminTelegramBroadcastForm,
    AdminUserEditForm,
    AppointmentDecisionForm,
    AppointmentRescheduleForm,
    BookingReasonForm,
    ClaimPatientForm,
    ClinicSettingsForm,
    DoctorPatientBookingForm,
    DoctorPatientCardForm,
    DoctorProfileForm,
    DoctorWorkplaceForm,
    EmailForm,
    GalleryImageForm,
    HomeHeroSlideForm,
    NewsPostForm,
    PatientRecordEntryForm,
    PatientProfileForm,
    ServiceForm,
    UsernameLoginForm,
    WorkScheduleForm,
    normalize_phone_number,
    patient_phone_is_used,
)
from .models import (
    Appointment,
    AppointmentImage,
    AppointmentVideo,
    AuditLog,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    DoctorWorkplace,
    GalleryImage,
    HomeHeroSlide,
    MedicalService,
    MedicalServiceImage,
    MedicalServiceVideo,
    NewsPost,
    PatientRecordEntry,
    PatientRecordImage,
    PatientRecordVideo,
    Profile,
    TelegramConnection,
    WorkSchedule,
)
from .telegram import (
    TelegramError,
    create_link_url,
    notify_doctor_new_request,
    notify_doctor_patient_action,
    notify_patient_status,
    process_update,
    send_admin_broadcast,
)


BLOCKING_APPOINTMENT_STATUSES = [
    Appointment.STATUS_PENDING,
    Appointment.STATUS_APPROVED,
    Appointment.STATUS_COMPLETED,
    Appointment.STATUS_RESCHEDULE_PROPOSED,
]
PATIENT_DAILY_BOOKING_LIMIT = 2

UKRAINIAN_WEEKDAYS = (
    'Понеділок',
    'Вівторок',
    'Середа',
    'Четвер',
    "П'ятниця",
    'Субота',
    'Неділя',
)


def write_audit_log(request, action, target, details=''):
    AuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=str(target._meta.verbose_name),
        target_id=str(target.pk or ''),
        target_label=str(target)[:240],
        details=details,
    )


@login_required
def telegram_connect(request):
    role = user_role(request.user)
    if request.user.is_staff or role not in {Profile.ROLE_PATIENT, Profile.ROLE_DOCTOR}:
        messages.error(request, 'Підключення Telegram доступне пацієнтам і лікарям.')
        return redirect_by_role(request.user)

    if role == Profile.ROLE_PATIENT and not SocialAccount.objects.filter(
        user=request.user,
        provider='google',
    ).exists():
        messages.error(request, 'Спочатку увійдіть через Google, а потім підключіть Telegram.')
        return redirect('home')

    try:
        return redirect(create_link_url(request.user))
    except TelegramError as error:
        messages.error(request, str(error))
        return redirect_by_role(request.user)


@login_required
@require_POST
def telegram_reconnect(request):
    role = user_role(request.user)
    if request.user.is_staff or role not in {Profile.ROLE_PATIENT, Profile.ROLE_DOCTOR}:
        messages.error(request, 'Переприв’язування Telegram доступне пацієнтам і лікарям.')
        return redirect_by_role(request.user)

    if role == Profile.ROLE_PATIENT and not SocialAccount.objects.filter(
        user=request.user,
        provider='google',
    ).exists():
        messages.error(request, 'Спочатку увійдіть через Google, а потім підключіть Telegram.')
        return redirect('home')

    try:
        with transaction.atomic():
            link = create_link_url(request.user)
            old_connection = TelegramConnection.objects.filter(user=request.user).first()
            if old_connection is not None:
                write_audit_log(
                    request,
                    'Розпочато переприв’язування Telegram',
                    old_connection,
                )
                old_connection.delete()
    except TelegramError as error:
        messages.error(request, str(error))
        return redirect_by_role(request.user)

    return redirect(link)


@login_required
@require_POST
def telegram_disconnect(request):
    disconnected = TelegramConnection.objects.filter(user=request.user).update(is_active=False)
    if disconnected:
        messages.success(request, 'Telegram-сповіщення вимкнено.')
    return redirect_by_role(request.user)


@csrf_exempt
def telegram_webhook(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    expected_secret = settings.TELEGRAM_WEBHOOK_SECRET
    received_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not expected_secret:
        return JsonResponse({'ok': False}, status=503)
    if not hmac.compare_digest(received_secret, expected_secret):
        return JsonResponse({'ok': False}, status=403)

    try:
        update = json.loads(request.body.decode('utf-8'))
        process_update(update)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False}, status=400)
    except (TelegramError, requests.RequestException):
        return JsonResponse({'ok': False}, status=503)
    return JsonResponse({'ok': True})


def private_media(request, path):
    if not request.user.is_authenticated:
        raise PermissionDenied

    item = None
    field = None
    appointment = None
    entry = None

    if path.startswith('appointment_images/'):
        item = get_object_or_404(
            AppointmentImage.objects.select_related('appointment__doctor__user'),
            image=path,
        )
        field = item.image
        appointment = item.appointment
    elif path.startswith('appointment_videos/'):
        item = get_object_or_404(
            AppointmentVideo.objects.select_related('appointment__doctor__user'),
            video=path,
        )
        field = item.video
        appointment = item.appointment
    elif path.startswith('patient_records/'):
        item = get_object_or_404(
            PatientRecordImage.objects.select_related(
                'entry__doctor__user',
                'entry__card__patient',
                'entry__appointment__patient',
            ),
            image=path,
        )
        field = item.image
        entry = item.entry
    elif path.startswith('patient_record_videos/'):
        item = get_object_or_404(
            PatientRecordVideo.objects.select_related(
                'entry__doctor__user',
                'entry__card__patient',
                'entry__appointment__patient',
            ),
            video=path,
        )
        field = item.video
        entry = item.entry
    else:
        raise PermissionDenied

    allowed = request.user.is_staff
    has_patient_google = SocialAccount.objects.filter(
        user=request.user,
        provider='google',
    ).exists()
    if appointment:
        allowed = allowed or appointment.doctor.user_id == request.user.id
        allowed = allowed or (
            appointment.patient_id == request.user.id and has_patient_google
        )
    if entry:
        allowed = allowed or entry.doctor.user_id == request.user.id
        patient_can_see = entry.kind in {
            PatientRecordEntry.KIND_TREATMENT,
            PatientRecordEntry.KIND_RECOMMENDATION,
        }
        patient_id = entry.card.patient_id or (
            entry.appointment.patient_id if entry.appointment_id else None
        )
        allowed = allowed or (
            patient_can_see
            and patient_id == request.user.id
            and has_patient_google
        )
    if not allowed:
        raise PermissionDenied

    content_type = mimetypes.guess_type(field.name)[0] or 'application/octet-stream'
    response = FileResponse(field.open('rb'), content_type=content_type)
    response['Cache-Control'] = 'private, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def public_media(request, path):
    private_prefixes = (
        'appointment_images/',
        'appointment_videos/',
        'patient_records/',
        'patient_record_videos/',
    )
    if path.replace('\\', '/').startswith(private_prefixes):
        raise PermissionDenied
    return serve(request, path, document_root=settings.MEDIA_ROOT)


def user_role(user):
    if not user.is_authenticated:
        return None
    if user.is_staff:
        return 'admin'
    try:
        return user.profile.role
    except Profile.DoesNotExist:
        if hasattr(user, 'doctor_profile'):
            return Profile.ROLE_DOCTOR
        return None


def redirect_by_role(user):
    role = user_role(user)
    if role == 'admin':
        return redirect('admin_panel')
    if role == Profile.ROLE_DOCTOR:
        return redirect('doctor_dashboard')
    return redirect('patient_dashboard')


def patient_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            if user_role(request.user) == Profile.ROLE_PATIENT:
                has_google = SocialAccount.objects.filter(
                    user=request.user,
                    provider='google',
                ).exists()
                if has_google:
                    return view_func(request, *args, **kwargs)
                logout(request)
                messages.error(request, 'Спочатку увійдіть в акаунт Google.')
                return redirect('home')
            messages.error(request, 'Ця функція доступна лише пацієнтам.')
            return redirect_by_role(request.user)
        messages.error(request, 'Спочатку увійдіть в акаунт Google.')
        if request.session.get('patient_claim_phone'):
            return redirect('pending_patient_dashboard')
        return redirect('home')

    return wrapper


def doctor_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and user_role(request.user) == Profile.ROLE_DOCTOR:
            if not request.user.is_active:
                messages.error(request, 'Ваш акаунт архівовано.')
                return redirect('home')
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Увійдіть як лікар.')
        return redirect('administration_login')

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Увійдіть як адміністратор.')
        return redirect('administration_login')

    return wrapper


def parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return timezone.localdate()


def parse_time(value):
    try:
        return datetime.strptime(value, '%H:%M').time()
    except (TypeError, ValueError):
        return None


def is_past_appointment(selected_date, selected_time=None):
    now = timezone.localtime()
    if selected_date < now.date():
        return True
    if selected_time and selected_date == now.date() and selected_time <= now.time().replace(second=0, microsecond=0):
        return True
    return False


def schedule_for_date(doctor, selected_date):
    return WorkSchedule.objects.filter(
        doctor=doctor,
        weekday=selected_date.weekday(),
    ).first()


def doctor_working_weekdays(doctor):
    if doctor is None:
        return []
    return sorted(
        set(
            doctor.schedules.filter(is_working=True).values_list(
                'weekday',
                flat=True,
            )
        )
    )


def next_working_date(start_date, working_weekdays):
    working_weekdays = set(working_weekdays)
    if not working_weekdays:
        return None
    for offset in range(7):
        candidate = start_date + timedelta(days=offset)
        if candidate.weekday() in working_weekdays:
            return candidate
    return None


def working_weekday_labels(working_weekdays):
    allowed = set(working_weekdays)
    return [
        label
        for value, label in WorkSchedule.WEEKDAY_CHOICES
        if value in allowed
    ]


def appointment_range(date_value, time_value, slot_minutes, duration_slots=1, duration_minutes=None):
    start = datetime.combine(date_value, time_value)
    minutes = duration_minutes if duration_minutes is not None else slot_minutes * duration_slots
    end = start + timedelta(minutes=minutes)
    return start, end


def appointment_conflicts(
    doctor,
    selected_date,
    selected_time,
    duration_slots=1,
    duration_minutes=None,
    exclude_id=None,
):
    schedule = schedule_for_date(doctor, selected_date)
    if not schedule:
        return True

    target_start, target_end = appointment_range(
        selected_date,
        selected_time,
        schedule.slot_minutes,
        duration_slots,
        duration_minutes,
    )

    day_end = datetime.combine(selected_date, schedule.end_time)
    if target_end > day_end:
        return True

    if schedule.break_start_time and schedule.break_duration_minutes:
        break_start = datetime.combine(selected_date, schedule.break_start_time)
        break_end = break_start + timedelta(minutes=schedule.break_duration_minutes)
        if target_start < break_end and target_end > break_start:
            return True

    appointments = (
        Appointment.objects.filter(
            doctor=doctor,
            date=selected_date,
            status__in=BLOCKING_APPOINTMENT_STATUSES,
        )
        .exclude(pk=exclude_id)
        .order_by('time')
    )

    for appointment in appointments:
        item_start, item_end = appointment_range(
            appointment.date,
            appointment.time,
            schedule.slot_minutes,
            duration_minutes=appointment.duration_minutes,
        )
        if target_start < item_end and target_end > item_start:
            return True
    return False


def patient_appointment_conflicts(
    patient,
    selected_date,
    selected_time,
    duration_minutes,
    patient_phone='',
    exclude_id=None,
):
    if not selected_date or not selected_time or not duration_minutes:
        return False

    identity_filter = Q(patient=patient) if patient else None
    normalized_phone = normalize_phone_number(
        patient_phone
        or (
            patient.profile.phone
            if patient and hasattr(patient, 'profile')
            else ''
        )
    )
    if normalized_phone:
        phone_filter = Q(patient_phone=normalized_phone)
        identity_filter = (
            identity_filter | phone_filter
            if identity_filter is not None
            else phone_filter
        )
    if identity_filter is None:
        return False

    target_start = datetime.combine(selected_date, selected_time)
    target_end = target_start + timedelta(minutes=duration_minutes)
    appointments = (
        Appointment.objects.filter(
            identity_filter,
            date=selected_date,
            status__in=BLOCKING_APPOINTMENT_STATUSES,
        )
        .exclude(pk=exclude_id)
        .select_related('doctor')
        .order_by('time')
    )
    for appointment in appointments:
        item_start = datetime.combine(appointment.date, appointment.time)
        item_end = item_start + timedelta(minutes=appointment.duration_minutes)
        if target_start < item_end and target_end > item_start:
            return True
    return False


def patient_daily_appointment_count(
    patient,
    selected_date,
    patient_phone='',
):
    if not selected_date:
        return 0

    identity_filter = Q(patient=patient) if patient else None
    normalized_phone = normalize_phone_number(
        patient_phone
        or (
            patient.profile.phone
            if patient and hasattr(patient, 'profile')
            else ''
        )
    )
    if normalized_phone:
        phone_filter = Q(patient_phone=normalized_phone)
        identity_filter = (
            identity_filter | phone_filter
            if identity_filter is not None
            else phone_filter
        )
    if identity_filter is None:
        return 0

    return Appointment.objects.filter(
        identity_filter,
        date=selected_date,
        status__in=BLOCKING_APPOINTMENT_STATUSES,
    ).count()


def split_slot_option(doctor, selected_date, slot_time, schedule=None, appointment_id=None):
    schedule = schedule or schedule_for_date(doctor, selected_date)
    if (
        not schedule
        or not schedule.is_working
        or schedule.slot_minutes < 2
        or schedule.slot_minutes % 2
        or is_past_appointment(selected_date, slot_time)
    ):
        return None

    source_query = Appointment.objects.filter(
        doctor=doctor,
        date=selected_date,
        time=slot_time,
        status=Appointment.STATUS_APPROVED,
    )
    if appointment_id:
        source_query = source_query.filter(pk=appointment_id)
    source = source_query.first()
    if not source or source.duration_minutes != schedule.slot_minutes:
        return None

    source_start = datetime.combine(selected_date, slot_time)
    next_start = source_start + timedelta(minutes=schedule.slot_minutes)
    if next_start.date() != selected_date:
        return None

    has_following_appointment = Appointment.objects.filter(
        doctor=doctor,
        date=selected_date,
        time=next_start.time(),
        status__in=BLOCKING_APPOINTMENT_STATUSES,
    ).exists()
    if not has_following_appointment:
        return None

    half_minutes = schedule.slot_minutes // 2
    split_start = source_start + timedelta(minutes=half_minutes)
    return {
        'appointment': source,
        'time': split_start.time(),
        'duration_minutes': half_minutes,
        'original_duration_minutes': schedule.slot_minutes,
    }


def slots_for_doctor(
    doctor,
    selected_date,
    patient=None,
    patient_phone='',
    include_split_options=False,
):
    schedule = schedule_for_date(doctor, selected_date)
    if not schedule:
        return None, []

    now = timezone.localtime()
    slots = []
    for slot in schedule.get_slots():
        busy = appointment_conflicts(doctor, selected_date, slot, duration_slots=1) or (
                selected_date == now.date() and slot <= now.time().replace(second=0, microsecond=0)
            ) or patient_appointment_conflicts(
                patient,
                selected_date,
                slot,
                schedule.slot_minutes,
                patient_phone=patient_phone,
            )
        slot_data = {
            'time': slot,
            'busy': busy,
        }
        if busy and include_split_options:
            slot_data['split_option'] = split_slot_option(
                doctor,
                selected_date,
                slot,
                schedule=schedule,
            )
        slots.append(slot_data)
    return schedule, slots


def appointment_finished(appointment):
    visit_end = timezone.make_aware(datetime.combine(appointment.date, appointment.end_time))
    return visit_end <= timezone.localtime()


def active_appointment_for_doctor(doctor):
    now = timezone.localtime()
    appointments = doctor.appointments.filter(
        date=now.date(),
        status=Appointment.STATUS_APPROVED,
    ).select_related('service', 'patient').order_by('time')
    for appointment in appointments:
        start = timezone.make_aware(datetime.combine(appointment.date, appointment.time))
        end = timezone.make_aware(datetime.combine(appointment.date, appointment.end_time))
        if start <= now < end:
            return appointment
    return None


def refresh_completed_appointments():
    changed = []
    for appointment in Appointment.objects.filter(status=Appointment.STATUS_APPROVED).select_related('doctor'):
        if appointment_finished(appointment):
            appointment.status = Appointment.STATUS_COMPLETED
            changed.append(appointment)
    if changed:
        Appointment.objects.bulk_update(changed, ['status'])


def find_patient_by_contacts(email, phone):
    patient = None
    if email:
        patient = User.objects.filter(
            email__iexact=email,
            profile__role=Profile.ROLE_PATIENT,
        ).first()
    if not patient and phone:
        patient = User.objects.filter(
            profile__role=Profile.ROLE_PATIENT,
            profile__phone=phone,
        ).first()
    if not patient and phone:
        normalized_phone = normalize_phone_number(phone)
        patient = next(
            (
                candidate
                for candidate in User.objects.filter(
                    profile__role=Profile.ROLE_PATIENT,
                ).select_related('profile')
                if normalize_phone_number(candidate.profile.phone) == normalized_phone
            ),
            None,
        )
    return patient


def find_doctor_card_by_phone(doctor, phone):
    normalized_phone = normalize_phone_number(phone)
    return next(
        (
            card
            for card in doctor.patient_cards.select_related('patient', 'patient__profile')
            if normalize_phone_number(card.patient_phone) == normalized_phone
        ),
        None,
    )


def patient_card_for_appointment(appointment):
    cards = appointment.doctor.patient_cards.select_related(
        'patient',
        'patient__profile',
    )
    if appointment.booked_for_other or not appointment.patient_id:
        normalized_phone = normalize_phone_number(appointment.patient_phone)
        first_name = appointment.patient_first_name.strip().casefold()
        last_name = appointment.patient_last_name.strip().casefold()
        return next(
            (
                card
                for card in cards.filter(patient__isnull=True)
                if (
                    normalize_phone_number(card.patient_phone)
                    == normalized_phone
                    and card.patient_first_name.strip().casefold() == first_name
                    and card.patient_last_name.strip().casefold() == last_name
                )
            ),
            None,
        )
    if appointment.patient_id:
        return cards.filter(patient_id=appointment.patient_id).first()
    return None


def appointments_for_patient_card(doctor, card):
    if card.patient_id:
        return doctor.appointments.filter(
            patient_id=card.patient_id,
            booked_for_other=False,
        )
    return doctor.appointments.filter(
        patient_phone=card.patient_phone,
        patient_first_name__iexact=card.patient_first_name,
        patient_last_name__iexact=card.patient_last_name,
    )


def ensure_patient_card_from_appointment(appointment):
    normalized_phone = normalize_phone_number(appointment.patient_phone)
    if appointment.patient_phone != normalized_phone:
        appointment.patient_phone = normalized_phone
        appointment.save(update_fields=['patient_phone'])
    patient = (
        None
        if appointment.booked_for_other
        else (
            appointment.patient
            or find_patient_by_contacts(
                appointment.patient_email,
                appointment.patient_phone,
            )
        )
    )
    card = patient_card_for_appointment(appointment)
    created = card is None
    if created:
        card = DoctorPatientCard.objects.create(
            doctor=appointment.doctor,
            patient_phone=normalized_phone,
            patient=patient,
            patient_first_name=appointment.patient_first_name,
            patient_last_name=appointment.patient_last_name,
            patient_email=appointment.patient_email,
        )
    changed_fields = []
    if card.patient_phone != normalized_phone:
        card.patient_phone = normalized_phone
        changed_fields.append('patient_phone')
    if patient and card.patient_id != patient.id:
        card.patient = patient
        changed_fields.append('patient')
    if card.patient_first_name != appointment.patient_first_name:
        card.patient_first_name = appointment.patient_first_name
        changed_fields.append('patient_first_name')
    if card.patient_last_name != appointment.patient_last_name:
        card.patient_last_name = appointment.patient_last_name
        changed_fields.append('patient_last_name')
    if card.patient_email != appointment.patient_email:
        card.patient_email = appointment.patient_email
        changed_fields.append('patient_email')
    if changed_fields:
        card.save(update_fields=changed_fields)
    return card, created


def unclaimed_records_for_phone(phone):
    appointments = [
        appointment
        for appointment in Appointment.objects.filter(patient__isnull=True)
        .only('id', 'patient_phone', 'patient_first_name', 'patient_last_name', 'created_at')
        .order_by('-created_at')
        if normalize_phone_number(appointment.patient_phone) == phone
    ]
    cards = [
        card
        for card in DoctorPatientCard.objects.filter(patient__isnull=True)
        .only('id', 'patient_phone', 'patient_first_name', 'patient_last_name', 'updated_at')
        .order_by('-updated_at')
        if normalize_phone_number(card.patient_phone) == phone
    ]
    return appointments, cards


def pending_patient_identity(phone):
    appointments, cards = unclaimed_records_for_phone(phone)
    source = appointments[0] if appointments else (cards[0] if cards else None)
    if source:
        first_name = source.patient_first_name.strip()
        last_name = source.patient_last_name.strip()
    else:
        first_name = 'Новий'
        last_name = 'пацієнт'
    return {
        'first_name': first_name,
        'last_name': last_name,
        'full_name': f'{first_name} {last_name}'.strip(),
        'initials': f'{first_name[:1]}{last_name[:1]}'.upper(),
    }


def sync_patient_cards_for_doctor(doctor):
    appointments = doctor.appointments.exclude(
        status__in=[Appointment.STATUS_CANCELED, Appointment.STATUS_REJECTED]
    )
    for appointment in appointments:
        ensure_patient_card_from_appointment(appointment)


def home(request, claim_form=None, open_login_modal=False):
    featured_doctors = (
        Doctor.objects.filter(user__is_active=True)
        .select_related('user')
        .annotate(home_appointments=Count('appointments'))
        .order_by('-home_appointments', 'user__last_name', 'user__first_name')[:4]
    )
    if claim_form is None:
        claim_form = ClaimPatientForm()
    return render(
        request,
        'clinic/home.html',
        {
            'clinic_news': NewsPost.objects.filter(doctor__isnull=True, is_published=True)[:6],
            'doctor_news': NewsPost.objects.filter(
                doctor__isnull=False,
                is_published=True,
            ).select_related('doctor__user')[:6],
            'gallery_images': GalleryImage.objects.filter(is_published=True)[:18],
            'featured_doctors': featured_doctors,
            'hero_slides': HomeHeroSlide.objects.filter(is_active=True),
            'claim_form': claim_form,
            'open_login_modal': open_login_modal,
            'patient_google_login_url': (
                f'{reverse("google_login")}?'
                f'{urlencode({"next": reverse("claim_patient_complete")})}'
                if request.session.get('patient_claim_phone')
                else reverse('google_login')
            ),
        },
    )


def login_view(request, role='patient'):
    if role == 'patient':
        messages.info(request, 'Для пацієнта використовується вхід через Google.')
        return redirect('home')

    if request.method == 'GET':
        return render(
            request,
            'clinic/login.html',
            {
                'form': UsernameLoginForm(),
                'role': role,
            },
        )

    form = UsernameLoginForm(request.POST)
    if form.is_valid():
        user = form.get_user(request)
        if user and user.is_active:
            actual_role = user_role(user)
            if role == 'administration' and not (
                user.is_staff or actual_role == Profile.ROLE_DOCTOR
            ):
                messages.error(request, 'Цей акаунт не належить лікарю або адміністратору.')
            elif role == 'admin' and not user.is_staff:
                messages.error(request, 'Цей акаунт не є акаунтом адміністратора.')
            elif role == 'doctor' and actual_role != Profile.ROLE_DOCTOR:
                messages.error(request, 'Цей акаунт не є акаунтом лікаря.')
            elif role == 'patient' and actual_role != Profile.ROLE_PATIENT:
                messages.error(request, 'Цей акаунт не є акаунтом пацієнта.')
            else:
                login(request, user)
                messages.success(request, 'Ви успішно увійшли в систему.')
                return redirect_by_role(user)
        else:
            messages.error(request, 'Невірний логін або пароль.')

    return render(
        request,
        'clinic/login.html',
        {
            'form': form,
            'role': role,
        },
    )


def logout_view(request):
    logout(request)
    messages.success(request, 'Ви вийшли з акаунта.')
    return redirect('home')


def forgot_password(request):
    form = EmailForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        messages.success(
            request,
            'Якщо така пошта є в системі, адміністратор допоможе відновити доступ.',
        )
        return redirect('home')
    return render(request, 'clinic/forgot_password.html', {'form': form})


def claim_patient(request):
    if request.user.is_authenticated and user_role(request.user) != Profile.ROLE_PATIENT:
        messages.error(request, 'Ця функція доступна лише пацієнтам.')
        return redirect_by_role(request.user)

    form = ClaimPatientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        phone = form.cleaned_data['phone']
        exclude_user = request.user if request.user.is_authenticated else None
        if patient_phone_is_used(phone, exclude_user=exclude_user):
            form.add_error(
                'phone',
                'Цей номер уже прив’язаний до кабінету. Увійдіть через Google, щоб відкрити його.',
            )
        else:
            complete_url = reverse('claim_patient_complete')
            if request.user.is_authenticated and SocialAccount.objects.filter(
                user=request.user,
                provider='google',
            ).exists():
                request.session['patient_claim_phone'] = phone
                return redirect(complete_url)
            if request.user.is_authenticated:
                logout(request)
            request.session['patient_claim_phone'] = phone
            messages.info(
                request,
                'Увійдіть через Google для подальшої роботи із сайтом.',
                extra_tags='claim-google-message',
            )
            return redirect('pending_patient_dashboard')

    if (
        request.method == 'POST'
        and not request.user.is_authenticated
        and not request.session.get('patient_claim_phone')
    ):
        return home(request, claim_form=form, open_login_modal=True)
    return render(request, 'clinic/claim_patient.html', {'form': form})


def pending_patient_dashboard(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    phone = request.session.get('patient_claim_phone')
    if not phone:
        return redirect('claim_patient')

    google_url = reverse('google_login')
    complete_url = reverse('claim_patient_complete')
    unclaimed_appointments, _ = unclaimed_records_for_phone(phone)
    future_candidates = (
        Appointment.objects.filter(
            pk__in=[appointment.pk for appointment in unclaimed_appointments],
            status__in=[
                Appointment.STATUS_PENDING,
                Appointment.STATUS_APPROVED,
                Appointment.STATUS_RESCHEDULE_PROPOSED,
            ],
        )
        .select_related('doctor__user', 'service')
        .order_by('date', 'time')
    )
    future_appointments = [appointment for appointment in future_candidates if appointment.is_future]
    return render(
        request,
        'clinic/pending_patient_dashboard.html',
        {
            'pending_phone': phone,
            'pending_identity': pending_patient_identity(phone),
            'pending_google_login_url': f'{google_url}?{urlencode({"next": complete_url})}',
            'future_appointments': future_appointments,
        },
    )


@login_required
def claim_patient_complete(request):
    phone = request.session.get('patient_claim_phone')
    if not phone:
        return redirect('patient_dashboard')

    if request.user.is_staff:
        request.session.pop('patient_claim_phone', None)
        messages.error(request, 'Записи можна прив’язати лише до кабінету пацієнта.')
        return redirect_by_role(request.user)

    profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={'role': Profile.ROLE_PATIENT, 'phone': ''},
    )
    if profile.role != Profile.ROLE_PATIENT:
        request.session.pop('patient_claim_phone', None)
        messages.error(request, 'Записи можна прив’язати лише до кабінету пацієнта.')
        return redirect_by_role(request.user)

    if not SocialAccount.objects.filter(user=request.user, provider='google').exists():
        messages.error(request, 'Для прив’язування записів потрібно увійти через Google.')
        google_url = reverse('google_login')
        return redirect(f'{google_url}?{urlencode({"next": reverse("claim_patient_complete")})}')

    current_phone = normalize_phone_number(profile.phone)
    if current_phone and current_phone != phone:
        request.session.pop('patient_claim_phone', None)
        messages.error(request, 'У цьому кабінеті вже вказано інший номер телефону.')
        return redirect('patient_dashboard')

    phone_belongs_to_another_user = any(
        normalize_phone_number(item.phone) == phone
        for item in Profile.objects.filter(role=Profile.ROLE_PATIENT).exclude(user=request.user).exclude(phone='')
    )
    if phone_belongs_to_another_user:
        request.session.pop('patient_claim_phone', None)
        messages.error(request, 'Цей номер уже прив’язаний до іншого кабінету.')
        return redirect('patient_dashboard')

    appointments, cards = unclaimed_records_for_phone(phone)
    doctor_identity = appointments[0] if appointments else (cards[0] if cards else None)

    with transaction.atomic():
        if doctor_identity:
            request.user.first_name = doctor_identity.patient_first_name.strip()
            request.user.last_name = doctor_identity.patient_last_name.strip()
            request.user.save(update_fields=['first_name', 'last_name'])
        profile.phone = phone
        profile.save(update_fields=['phone'])
        Appointment.objects.filter(pk__in=[item.pk for item in appointments]).update(patient=request.user)
        DoctorPatientCard.objects.filter(pk__in=[item.pk for item in cards]).update(patient=request.user)

    request.session.pop('patient_claim_phone', None)
    if appointments or cards:
        messages.success(request, 'Кабінет створено, а записи лікаря прив’язано до нього.')
    else:
        messages.success(request, 'Ваш кабінет створено. Заповніть особисті дані у профілі.')
    return redirect('patient_dashboard')


@patient_required
def patient_dashboard(request):
    if request.session.get('patient_claim_phone'):
        return redirect('claim_patient_complete')
    refresh_completed_appointments()
    appointments = (
        Appointment.objects.filter(patient=request.user)
        .select_related('service', 'doctor__user')
        .order_by('date', 'time')
    )
    patient_records = (
        PatientRecordEntry.objects.filter(
            Q(card__patient=request.user) | Q(appointment__patient=request.user),
            kind__in=[PatientRecordEntry.KIND_TREATMENT, PatientRecordEntry.KIND_RECOMMENDATION],
        )
        .select_related('doctor__user', 'appointment__service')
        .prefetch_related('images', 'videos')
        .distinct()
    )
    return render(
        request,
        'clinic/patient_dashboard.html',
        {
            'pending': appointments.filter(status=Appointment.STATUS_PENDING),
            'reschedule_requests': appointments.filter(status=Appointment.STATUS_RESCHEDULE_PROPOSED),
            'approved': appointments.filter(status=Appointment.STATUS_APPROVED),
            'completed': appointments.filter(status=Appointment.STATUS_COMPLETED),
            'canceled': appointments.filter(status__in=[Appointment.STATUS_CANCELED, Appointment.STATUS_REJECTED]),
            'needs_phone': not request.user.profile.phone,
            'profile_incomplete': not (
                request.user.first_name.strip()
                and request.user.last_name.strip()
                and request.user.profile.age
            ),
            'patient_records': patient_records,
        },
    )


@patient_required
def patient_edit_profile(request):
    form = PatientProfileForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
                write_audit_log(request, 'Оновлено профіль пацієнта', request.user.profile)
        except IntegrityError:
            form.add_error('phone', 'Цей номер телефону вже прив’язаний до іншого пацієнта.')
        else:
            messages.success(request, 'Профіль оновлено.')
            return redirect('patient_dashboard')
    return render(request, 'clinic/patient_edit_profile.html', {'form': form})


@patient_required
def patient_change_password(request):
    if not request.user.has_usable_password():
        messages.info(request, 'Ви входите через Google, тому окремий пароль MedClinic не потрібен.')
        return redirect('patient_dashboard')
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        write_audit_log(request, 'Змінено пароль пацієнта', request.user.profile)
        messages.success(request, 'Пароль змінено.')
        return redirect('patient_dashboard')
    return render(request, 'clinic/change_password.html', {'form': form})


@patient_required
def cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        patient=request.user,
        status__in=[
            Appointment.STATUS_PENDING,
            Appointment.STATUS_APPROVED,
            Appointment.STATUS_RESCHEDULE_PROPOSED,
        ],
    )
    if request.method == 'POST' and appointment.can_cancel:
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        write_audit_log(request, 'Скасовано запис пацієнтом', appointment)
        notify_doctor_patient_action(
            appointment,
            'patient_canceled',
            'Пацієнт скасував запис',
        )
        messages.success(request, 'Запис скасовано.')
    elif request.method == 'POST':
        messages.error(request, 'Цей запис уже не можна скасувати.')
    return redirect('patient_dashboard')


@patient_required
def restore_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        patient=request.user,
        status=Appointment.STATUS_CANCELED,
    )
    if request.method == 'POST':
        if (
            not appointment.can_restore
            or appointment_conflicts(
                appointment.doctor,
                appointment.date,
                appointment.time,
                duration_slots=appointment.duration_slots,
                duration_minutes=appointment.duration_minutes,
                exclude_id=appointment.id,
            )
            or patient_appointment_conflicts(
                appointment.patient,
                appointment.date,
                appointment.time,
                appointment.duration_minutes,
                patient_phone=appointment.patient_phone,
                exclude_id=appointment.id,
            )
        ):
            messages.error(request, 'Цей запис уже не можна відновити: час зайнятий іншим прийомом.')
        else:
            appointment.status = Appointment.STATUS_PENDING
            appointment.save(update_fields=['status'])
            write_audit_log(request, 'Відновлено заявку пацієнтом', appointment)
            notify_doctor_new_request(appointment, event='restored')
            messages.success(request, 'Заявку відновлено і знову відправлено лікарю.')
    return redirect('patient_dashboard')


@patient_required
def patient_appointment_detail(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related('doctor__user', 'service').prefetch_related('images', 'videos'),
        pk=appointment_id,
        patient=request.user,
    )
    return render(
        request,
        'clinic/patient_appointment_detail.html',
        {'appointment': appointment},
    )


@patient_required
def patient_reschedule_response(request, appointment_id):
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        patient=request.user,
        status=Appointment.STATUS_RESCHEDULE_PROPOSED,
    )
    if request.method != 'POST':
        return redirect('patient_appointment_detail', appointment_id=appointment.id)

    action = request.POST.get('action')
    if action == 'reject':
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        write_audit_log(request, 'Відхилено запропонований час', appointment)
        notify_doctor_patient_action(
            appointment,
            'reschedule_rejected',
            'Пацієнт відхилив новий час',
        )
        messages.success(request, 'Запропонований час відхилено. Запис скасовано.')
        return redirect('patient_dashboard')

    if action != 'accept':
        messages.error(request, 'Оберіть, чи погоджуєтеся ви з новим часом.')
        return redirect('patient_appointment_detail', appointment_id=appointment.id)

    schedule = schedule_for_date(appointment.doctor, appointment.date)
    if (
        is_past_appointment(appointment.date, appointment.time)
        or not schedule
        or not schedule.is_working
        or appointment.time not in schedule.get_slots()
        or appointment_conflicts(
            appointment.doctor,
            appointment.date,
            appointment.time,
            duration_minutes=appointment.duration_minutes,
            exclude_id=appointment.id,
        )
        or patient_appointment_conflicts(
            appointment.patient,
            appointment.date,
            appointment.time,
            appointment.duration_minutes,
            patient_phone=appointment.patient_phone,
            exclude_id=appointment.id,
        )
    ):
        messages.error(
            request,
            'Цей час уже недоступний або перетинається з іншим прийомом. Зверніться до лікаря для нового перенесення.',
        )
        return redirect('patient_appointment_detail', appointment_id=appointment.id)

    appointment.status = Appointment.STATUS_APPROVED
    appointment.approved_at = timezone.now()
    appointment.save(update_fields=['status', 'approved_at'])
    ensure_patient_card_from_appointment(appointment)
    write_audit_log(request, 'Погоджено новий час', appointment)
    notify_doctor_patient_action(
        appointment,
        'reschedule_accepted',
        'Пацієнт погодив новий час',
    )
    messages.success(request, 'Новий час прийому підтверджено.')
    return redirect('patient_dashboard')


def doctors_list(request):
    refresh_completed_appointments()
    query = request.GET.get('q', '').strip()
    doctors = (
        Doctor.objects.filter(user__is_active=True)
        .select_related('user')
    )
    if query:
        doctors = doctors.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(specialization__icontains=query)
        )
    return render(
        request,
        'clinic/doctors.html',
        {
            'doctors': doctors.distinct(),
            'query': query,
        },
    )


def doctor_detail(request, doctor_id):
    doctor = get_object_or_404(
        Doctor.objects.filter(user__is_active=True)
        .select_related('user')
        .prefetch_related('services', 'schedules__workplace'),
        pk=doctor_id,
    )
    return render(
        request,
        'clinic/doctor_detail.html',
        {
            'doctor': doctor,
            'doctor_news': doctor.news_posts.filter(is_published=True)[:6],
        },
    )


def service_detail(request, doctor_id, service_id):
    service = get_object_or_404(
        MedicalService.objects.filter(doctor_id=doctor_id, doctor__user__is_active=True)
        .select_related('doctor__user')
        .prefetch_related('images', 'videos', 'doctor__schedules'),
        pk=service_id,
    )
    booking_return_url = None
    if (
        request.GET.get('from') == 'booking'
        and request.user.is_authenticated
        and user_role(request.user) == Profile.ROLE_PATIENT
    ):
        booking_params = {
            'doctor': service.doctor_id,
            'service': service.id,
        }
        selected_date = request.GET.get('date', '')
        selected_time = request.GET.get('time', '')
        try:
            datetime.strptime(selected_date, '%Y-%m-%d')
        except ValueError:
            pass
        else:
            booking_params['date'] = selected_date
        try:
            datetime.strptime(selected_time, '%H:%M')
        except ValueError:
            pass
        else:
            booking_params['time'] = selected_time
        booking_return_url = f"{reverse('booking')}?{urlencode(booking_params)}"

    return render(
        request,
        'clinic/service_detail.html',
        {
            'doctor': service.doctor,
            'service': service,
            'booking_return_url': booking_return_url,
        },
    )


@patient_required
def booking(request):
    refresh_completed_appointments()
    earliest_booking_date = timezone.localdate() + timedelta(days=1)
    if not (
        request.user.first_name.strip()
        and request.user.last_name.strip()
        and request.user.profile.age
    ):
        messages.error(request, 'Спочатку перевірте ім’я та прізвище і вкажіть свій вік у профілі.')
        return redirect('patient_edit_profile')
    if not request.user.profile.phone:
        messages.error(request, 'Спочатку заповніть телефон у профілі пацієнта.')
        return redirect('patient_edit_profile')

    doctors = Doctor.objects.filter(user__is_active=True).select_related('user')
    selected_doctor = get_object_or_404(doctors, pk=request.GET.get('doctor')) if request.GET.get('doctor') else doctors.first()
    working_weekdays = doctor_working_weekdays(selected_doctor)
    selected_date = parse_date(request.GET.get('date')) if request.GET.get('date') else earliest_booking_date
    if not selected_date or selected_date < earliest_booking_date:
        if request.GET.get('date'):
            messages.error(request, 'Записатися можна лише починаючи із завтрашнього дня.')
        selected_date = earliest_booking_date
    adjusted_date = next_working_date(selected_date, working_weekdays)
    date_was_adjusted = bool(adjusted_date and adjusted_date != selected_date)
    if adjusted_date:
        selected_date = adjusted_date
    selected_time = None if date_was_adjusted else parse_time(request.GET.get('time'))

    if request.method == 'POST':
        selected_doctor = get_object_or_404(doctors, pk=request.POST.get('doctor'))
        working_weekdays = doctor_working_weekdays(selected_doctor)
        selected_date = parse_date(request.POST.get('date'))
        selected_time = parse_time(request.POST.get('time'))
        reason_form = BookingReasonForm(request.POST, request.FILES, doctor=selected_doctor)
        if not selected_date or selected_date < earliest_booking_date:
            messages.error(request, 'Записатися можна лише починаючи із завтрашнього дня.')
            selected_date = earliest_booking_date
            selected_time = None
        else:
            daily_booking_count = patient_daily_appointment_count(
                request.user,
                selected_date,
                request.user.profile.phone,
            )
            schedule, slots = slots_for_doctor(
                selected_doctor,
                selected_date,
                patient=request.user,
                patient_phone=request.user.profile.phone,
            )
            available_times = [slot['time'] for slot in slots if not slot['busy']]

            if not schedule or not schedule.is_working:
                messages.error(request, 'У цей день лікар не приймає. Оберіть робочий день у календарі.')
                selected_time = None
            elif daily_booking_count >= PATIENT_DAILY_BOOKING_LIMIT:
                messages.error(
                    request,
                    'Самостійно можна створити не більше 2 заявок або прийомів на один день. Оберіть іншу дату.',
                )
            elif is_past_appointment(selected_date, selected_time):
                messages.error(request, 'Цей час уже недоступний.')
            elif selected_time and schedule and patient_appointment_conflicts(
                request.user,
                selected_date,
                selected_time,
                schedule.slot_minutes,
                patient_phone=request.user.profile.phone,
            ):
                messages.error(
                    request,
                    'У цей час у вас уже є інша заявка або прийом. Оберіть вільний час.',
                )
            elif not selected_time or selected_time not in available_times:
                messages.error(request, 'Цей час уже недоступний.')
            elif reason_form.is_valid() and schedule:
                duration_slots = 1
                if appointment_conflicts(selected_doctor, selected_date, selected_time, duration_slots=1):
                    messages.error(request, 'Цей час уже недоступний.')
                else:
                    try:
                        appointment = None
                        daily_limit_reached_during_save = False
                        with transaction.atomic():
                            User.objects.select_for_update().get(pk=request.user.pk)
                            if patient_daily_appointment_count(
                                request.user,
                                selected_date,
                                request.user.profile.phone,
                            ) >= PATIENT_DAILY_BOOKING_LIMIT:
                                daily_limit_reached_during_save = True
                            else:
                                appointment = Appointment.objects.create(
                                    doctor=selected_doctor,
                                    service=reason_form.cleaned_data['service'],
                                    patient=request.user,
                                    patient_first_name=(
                                        reason_form.cleaned_data['other_first_name']
                                        if reason_form.cleaned_data['booked_for_other']
                                        else request.user.first_name
                                    ),
                                    patient_last_name=(
                                        reason_form.cleaned_data['other_last_name']
                                        if reason_form.cleaned_data['booked_for_other']
                                        else request.user.last_name
                                    ),
                                    booked_for_other=reason_form.cleaned_data['booked_for_other'],
                                    patient_phone=request.user.profile.phone,
                                    patient_email=request.user.email,
                                    date=selected_date,
                                    time=selected_time,
                                    city=schedule.city,
                                    address=schedule.address,
                                    reason=reason_form.cleaned_data['reason'],
                                    duration_slots=duration_slots,
                                    status=Appointment.STATUS_PENDING,
                                )
                                for photo in reason_form.cleaned_data['photos']:
                                    AppointmentImage.objects.create(appointment=appointment, image=photo)
                                for video in reason_form.cleaned_data['videos']:
                                    AppointmentVideo.objects.create(appointment=appointment, video=video)
                        if daily_limit_reached_during_save:
                            messages.error(
                                request,
                                'Самостійно можна створити не більше 2 заявок або прийомів на один день. Оберіть іншу дату.',
                            )
                        else:
                            ensure_patient_card_from_appointment(appointment)
                            write_audit_log(request, 'Створено заявку', appointment)
                            notify_doctor_new_request(appointment)
                            messages.success(request, 'Заявку відправлено лікарю на підтвердження.')
                            return redirect('patient_dashboard')
                    except IntegrityError:
                        messages.error(request, 'Цей час уже недоступний.')
    else:
        selected_service = None
        if selected_doctor and request.GET.get('service'):
            selected_service = selected_doctor.services.filter(
                pk=request.GET.get('service'),
                is_patient_selectable=True,
            ).first()
        reason_form = BookingReasonForm(
            doctor=selected_doctor,
            initial={'service': selected_service} if selected_service else None,
        )

    schedule, slots = (
        slots_for_doctor(
            selected_doctor,
            selected_date,
            patient=request.user,
            patient_phone=request.user.profile.phone,
        )
        if selected_doctor
        else (None, [])
    )
    has_bookable_services = bool(
        selected_doctor
        and selected_doctor.services.filter(is_patient_selectable=True).exists()
    )
    daily_booking_count = patient_daily_appointment_count(
        request.user,
        selected_date,
        request.user.profile.phone,
    )
    daily_booking_limit_reached = (
        daily_booking_count >= PATIENT_DAILY_BOOKING_LIMIT
    )

    return render(
        request,
        'clinic/booking.html',
        {
            'doctors': doctors,
            'selected_doctor': selected_doctor,
            'selected_date': selected_date,
            'earliest_booking_date': earliest_booking_date,
            'selected_time': selected_time,
            'selected_schedule': schedule,
            'slots': slots,
            'reason_form': reason_form,
            'has_bookable_services': has_bookable_services,
            'daily_booking_count': daily_booking_count,
            'daily_booking_limit': PATIENT_DAILY_BOOKING_LIMIT,
            'daily_booking_limit_reached': daily_booking_limit_reached,
            'working_weekdays': working_weekdays,
            'working_weekday_labels': working_weekday_labels(working_weekdays),
        },
    )


@doctor_required
def doctor_dashboard(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    return render(
        request,
        'clinic/doctor_dashboard.html',
        {
            'doctor': doctor,
            'schedules': doctor.schedules.select_related('workplace'),
        },
    )


@doctor_required
def doctor_requests(request):
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    appointments = list(
        doctor.appointments.filter(
            status=Appointment.STATUS_PENDING,
        ).select_related(
            'service',
            'patient__profile',
        ).order_by('date', 'time', 'created_at')
    )
    for appointment in appointments:
        appointment.patient_card = patient_card_for_appointment(appointment)
        appointment.weekday_name = UKRAINIAN_WEEKDAYS[appointment.date.weekday()]

    return render(
        request,
        'clinic/doctor_requests.html',
        {
            'doctor': doctor,
            'appointments': appointments,
        },
    )


@doctor_required
def doctor_appointments(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    requested_week = parse_date(request.GET.get('week'))
    selected_date = requested_week or today
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=6)
    appointments = list(
        doctor.appointments.filter(
            date__range=(week_start, week_end),
        ).select_related('service', 'patient__profile').order_by('date', 'time')
    )
    appointments_by_date = {}
    for appointment in appointments:
        appointment.patient_card = patient_card_for_appointment(appointment)
        appointment.weekday_name = UKRAINIAN_WEEKDAYS[appointment.date.weekday()]
        appointments_by_date.setdefault(appointment.date, []).append(appointment)

    schedules_by_weekday = {
        schedule.weekday: schedule
        for schedule in doctor.schedules.select_related('workplace')
    }
    week_days = []
    for day_offset, weekday_name in enumerate(UKRAINIAN_WEEKDAYS):
        day_date = week_start + timedelta(days=day_offset)
        day_schedule = schedules_by_weekday.get(day_date.weekday())
        day_appointments = appointments_by_date.get(day_date, [])
        is_working = bool(day_schedule and day_schedule.is_working)
        if not is_working and not day_appointments:
            continue
        for appointment in day_appointments:
            appointment.split_option = split_slot_option(
                doctor,
                day_date,
                appointment.time,
                schedule=day_schedule,
                appointment_id=appointment.pk,
            )
        week_days.append(
            {
                'date': day_date,
                'weekday_name': weekday_name,
                'appointments': day_appointments,
                'schedule': day_schedule,
                'is_working': is_working,
                'is_outside_schedule': bool(day_appointments and not is_working),
                'can_book': bool(
                    is_working
                    and day_date >= today
                ),
            }
        )
    appointment_week = {
        'start': week_start,
        'end': week_end,
        'days': week_days,
        'appointments_count': len(appointments),
    }

    return render(
        request,
        'clinic/doctor_appointments.html',
        {
            'doctor': doctor,
            'appointments': appointments,
            'appointment_week': appointment_week,
            'previous_week_start': week_start - timedelta(days=7),
            'next_week_start': week_start + timedelta(days=7),
            'is_current_week': week_start == current_week_start,
        },
    )


def doctor_appointments_week_url(appointment_date):
    return f'{reverse("doctor_appointments")}?week={appointment_date.isoformat()}'


@doctor_required
def doctor_appointment_detail(request, appointment_id):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    appointment = get_object_or_404(
        doctor.appointments.select_related('service', 'patient__profile').prefetch_related('images', 'videos'),
        pk=appointment_id,
    )
    appointment.weekday_name = UKRAINIAN_WEEKDAYS[appointment.date.weekday()]
    day_appointments = doctor.appointments.filter(date=appointment.date).select_related(
        'service',
        'patient__profile',
    ).order_by('time')
    active_appointment = active_appointment_for_doctor(doctor)
    patient_card, _ = ensure_patient_card_from_appointment(appointment)
    reschedule_form = AppointmentRescheduleForm(appointment=appointment)
    return render(
        request,
        'clinic/doctor_appointment_detail.html',
        {
            'doctor': doctor,
            'appointment': appointment,
            'day_appointments': day_appointments,
            'active_appointment': active_appointment,
            'patient_card': patient_card,
            'reschedule_form': reschedule_form,
            'show_reschedule_form': request.GET.get('reschedule') == '1',
        },
    )


@doctor_required
def doctor_propose_reschedule(request, appointment_id):
    doctor = request.user.doctor_profile
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        doctor=doctor,
        patient__isnull=False,
        status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_APPROVED],
    )
    if request.method != 'POST':
        return redirect(
            f'{reverse("doctor_appointment_detail", args=[appointment.id])}?reschedule=1#reschedule'
        )

    form = AppointmentRescheduleForm(request.POST, appointment=appointment)
    if not form.is_valid():
        for errors in form.errors.values():
            messages.error(request, errors[0])
        return redirect(
            f'{reverse("doctor_appointment_detail", args=[appointment.id])}?reschedule=1#reschedule'
        )

    selected_date = form.cleaned_data['date']
    selected_time = form.cleaned_data['time']
    duration_minutes = form.cleaned_data['duration_minutes']
    schedule = form.schedule
    duration_slots = ceil(duration_minutes / schedule.slot_minutes)
    if appointment_conflicts(
        doctor,
        selected_date,
        selected_time,
        duration_slots=duration_slots,
        duration_minutes=duration_minutes,
        exclude_id=appointment.id,
    ):
        messages.error(request, 'Обраний час перетинається з іншим записом, обідом або кінцем робочого дня.')
        return redirect(
            f'{reverse("doctor_appointment_detail", args=[appointment.id])}?reschedule=1#reschedule'
        )

    if patient_appointment_conflicts(
        appointment.patient,
        selected_date,
        selected_time,
        duration_minutes,
        patient_phone=appointment.patient_phone,
        exclude_id=appointment.id,
    ):
        messages.error(
            request,
            'У цей час пацієнт уже має іншу заявку або прийом. Оберіть інший час.',
        )
        return redirect(
            f'{reverse("doctor_appointment_detail", args=[appointment.id])}?reschedule=1#reschedule'
        )

    appointment.previous_date = appointment.date
    appointment.previous_time = appointment.time
    appointment.date = selected_date
    appointment.time = selected_time
    appointment.city = schedule.city
    appointment.address = schedule.address
    appointment.duration_slots = duration_slots
    appointment.duration_minutes_exact = duration_minutes
    appointment.status = Appointment.STATUS_RESCHEDULE_PROPOSED
    appointment.reschedule_requested_at = timezone.now()
    try:
        with transaction.atomic():
            appointment.save(
                update_fields=[
                    'previous_date',
                    'previous_time',
                    'date',
                    'time',
                    'city',
                    'address',
                    'duration_slots',
                    'duration_minutes_exact',
                    'status',
                    'reschedule_requested_at',
                ]
            )
    except IntegrityError:
        messages.error(request, 'Цей час щойно зайняли. Оберіть інший варіант.')
        return redirect(
            f'{reverse("doctor_appointment_detail", args=[appointment.id])}?reschedule=1#reschedule'
        )

    write_audit_log(request, 'Запропоновано новий час', appointment)
    notify_patient_status(
        appointment,
        f'reschedule_{int(appointment.reschedule_requested_at.timestamp())}',
        'Лікар пропонує змінити час прийому',
    )
    messages.success(request, 'Новий час надіслано пацієнту на погодження.')
    return redirect('doctor_appointment_detail', appointment_id=appointment.id)


@doctor_required
def doctor_review_appointment(request, appointment_id):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        doctor=doctor,
        status=Appointment.STATUS_PENDING,
    )
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reject':
            appointment.status = Appointment.STATUS_REJECTED
            appointment.save(update_fields=['status'])
            ensure_patient_card_from_appointment(appointment)
            write_audit_log(request, 'Відхилено заявку лікарем', appointment)
            notify_patient_status(
                appointment,
                'rejected',
                'Лікар відхилив заявку на прийом',
            )
            messages.success(request, 'Заявку відхилено.')
            return redirect(doctor_appointments_week_url(appointment.date))

        schedule = schedule_for_date(doctor, appointment.date)
        slot_minutes = schedule.slot_minutes if schedule else 60
        form = AppointmentDecisionForm(request.POST, slot_minutes=slot_minutes)
        if form.is_valid():
            duration_minutes = form.cleaned_data['duration_minutes']
            duration_slots = ceil(duration_minutes / slot_minutes)
            if is_past_appointment(appointment.date, appointment.time):
                messages.error(request, 'Не можна підтвердити заявку на минулий час.')
            elif appointment_conflicts(
                doctor,
                appointment.date,
                appointment.time,
                duration_slots=duration_slots,
                duration_minutes=duration_minutes,
                exclude_id=appointment.id,
            ):
                messages.error(request, 'На цей час не вистачає вільних слотів для такої тривалості.')
            elif patient_appointment_conflicts(
                appointment.patient,
                appointment.date,
                appointment.time,
                duration_minutes,
                patient_phone=appointment.patient_phone,
                exclude_id=appointment.id,
            ):
                messages.error(
                    request,
                    'У цей час пацієнт уже має іншу заявку або прийом.',
                )
            else:
                appointment.duration_slots = duration_slots
                appointment.duration_minutes_exact = duration_minutes
                appointment.status = Appointment.STATUS_APPROVED
                appointment.approved_at = timezone.now()
                appointment.save(update_fields=['duration_slots', 'duration_minutes_exact', 'status', 'approved_at'])
                ensure_patient_card_from_appointment(appointment)
                write_audit_log(request, 'Підтверджено заявку лікарем', appointment)
                notify_patient_status(
                    appointment,
                    'approved',
                    'Лікар підтвердив вашу заявку',
                )
                messages.success(request, 'Заявку підтверджено.')
                return redirect(doctor_appointments_week_url(appointment.date))
        else:
            error = form.errors.get('duration_minutes')
            messages.error(request, error[0] if error else 'Перевірте тривалість прийому.')
    return redirect(doctor_appointments_week_url(appointment.date))


@doctor_required
def doctor_cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        doctor=request.user.doctor_profile,
    )
    if request.method == 'POST' and appointment.can_cancel:
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        ensure_patient_card_from_appointment(appointment)
        write_audit_log(request, 'Скасовано запис лікарем', appointment)
        notify_patient_status(
            appointment,
            'doctor_canceled',
            'Лікар скасував прийом',
        )
        messages.success(request, 'Запис пацієнта скасовано.')
    elif request.method == 'POST':
        messages.error(request, 'Цей запис уже не можна скасувати.')
    return redirect('doctor_appointment_detail', appointment_id=appointment.id)


@doctor_required
def doctor_book_patient(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    earliest_booking_date = timezone.localdate()
    working_weekdays = doctor_working_weekdays(doctor)
    selected_date = parse_date(request.GET.get('date')) if request.GET.get('date') else timezone.localdate()
    if selected_date < timezone.localdate():
        messages.error(request, 'Не можна вибрати минулу дату.')
        selected_date = timezone.localdate()
    adjusted_date = next_working_date(selected_date, working_weekdays)
    date_was_adjusted = bool(adjusted_date and adjusted_date != selected_date)
    if adjusted_date:
        selected_date = adjusted_date
    selected_time = None if date_was_adjusted else parse_time(request.GET.get('time'))
    schedule, slots = slots_for_doctor(doctor, selected_date, include_split_options=True)
    split_option = None
    split_appointment_id = request.GET.get('split')
    if split_appointment_id and schedule:
        split_source = Appointment.objects.filter(
            pk=split_appointment_id,
            doctor=doctor,
            date=selected_date,
        ).first()
        if split_source:
            split_option = split_slot_option(
                doctor,
                selected_date,
                split_source.time,
                schedule=schedule,
                appointment_id=split_source.pk,
            )
        if split_option:
            selected_time = split_option['time']
        else:
            selected_time = None
            messages.error(request, 'Цей слот уже не можна поділити.')
    slot_minutes = schedule.slot_minutes if schedule else 60
    form = DoctorPatientBookingForm(
        request.POST or None,
        doctor=doctor,
        slot_minutes=slot_minutes,
        fixed_duration_minutes=(
            split_option['duration_minutes']
            if split_option
            else None
        ),
    )

    if request.method == 'POST':
        selected_date = parse_date(request.POST.get('date'))
        selected_time = parse_time(request.POST.get('time'))
        schedule, slots = slots_for_doctor(doctor, selected_date, include_split_options=True)
        slot_minutes = schedule.slot_minutes if schedule else 60
        split_appointment_id = request.POST.get('split_appointment')
        split_option = None
        if split_appointment_id and schedule:
            split_source = Appointment.objects.filter(
                pk=split_appointment_id,
                doctor=doctor,
                date=selected_date,
            ).first()
            if split_source:
                split_option = split_slot_option(
                    doctor,
                    selected_date,
                    split_source.time,
                    schedule=schedule,
                    appointment_id=split_source.pk,
                )
        form = DoctorPatientBookingForm(
            request.POST,
            doctor=doctor,
            slot_minutes=slot_minutes,
            fixed_duration_minutes=(
                split_option['duration_minutes']
                if split_option
                else None
            ),
        )
        available_times = [slot['time'] for slot in slots if not slot['busy']]
        split_time_is_valid = bool(
            split_option
            and selected_time == split_option['time']
        )

        if not schedule or not schedule.is_working:
            messages.error(request, 'У цей день ви не приймаєте. Оберіть робочий день у календарі.')
            selected_time = None
        elif is_past_appointment(selected_date, selected_time):
            messages.error(request, 'Не можна записати пацієнта на минулу дату або час.')
        elif split_appointment_id and not split_time_is_valid:
            messages.error(request, 'Цей слот уже не можна поділити.')
        elif not selected_time or (
            selected_time not in available_times
            and not split_time_is_valid
        ):
            messages.error(request, 'Цей час уже недоступний.')
        elif form.is_valid() and schedule:
            duration_minutes = form.cleaned_data['duration_minutes']
            duration_slots = ceil(duration_minutes / slot_minutes)
            selected_patient = form.cleaned_data.get('patient')
            patient = selected_patient
            existing_card = find_doctor_card_by_phone(doctor, form.cleaned_data['phone'])
            patient_phone = normalize_phone_number(
                patient.profile.phone if patient and patient.profile.phone else form.cleaned_data['phone']
            )
            patient_first_name = form.cleaned_data['first_name']
            patient_last_name = form.cleaned_data['last_name']
            match_message = ''
            if patient:
                patient_first_name = patient.first_name or patient_first_name
                patient_last_name = patient.last_name or patient_last_name
            elif existing_card:
                patient_first_name = existing_card.patient_first_name
                patient_last_name = existing_card.patient_last_name
                match_message = ' Номер уже був у картці пацієнта, використано наявну картку.'
            split_source_id = (
                split_option['appointment'].pk
                if split_option
                else None
            )
            if appointment_conflicts(
                doctor,
                selected_date,
                selected_time,
                duration_slots=duration_slots,
                duration_minutes=duration_minutes,
                exclude_id=split_source_id,
            ):
                messages.error(request, 'Для такої тривалості недостатньо вільного часу.')
            elif patient_appointment_conflicts(
                patient,
                selected_date,
                selected_time,
                duration_minutes,
                patient_phone=patient_phone,
                exclude_id=split_source_id,
            ):
                messages.error(
                    request,
                    'У цей час пацієнт уже має іншу заявку або прийом.',
                )
            else:
                try:
                    with transaction.atomic():
                        if split_source_id:
                            locked_source = Appointment.objects.select_for_update().get(
                                pk=split_source_id,
                                doctor=doctor,
                                date=selected_date,
                            )
                            locked_split_option = split_slot_option(
                                doctor,
                                selected_date,
                                locked_source.time,
                                schedule=schedule,
                                appointment_id=locked_source.pk,
                            )
                            if (
                                not locked_split_option
                                or selected_time != locked_split_option['time']
                                or duration_minutes != locked_split_option['duration_minutes']
                            ):
                                raise IntegrityError
                            if appointment_conflicts(
                                doctor,
                                selected_date,
                                selected_time,
                                duration_minutes=duration_minutes,
                                exclude_id=locked_source.pk,
                            ):
                                raise IntegrityError
                            locked_source.duration_minutes_exact = duration_minutes
                            locked_source.duration_slots = 1
                            locked_source.save(
                                update_fields=['duration_minutes_exact', 'duration_slots']
                            )
                            write_audit_log(
                                request,
                                'Лікар поділив слот прийому',
                                locked_source,
                                (
                                    f'Тривалість змінено з '
                                    f'{locked_split_option["original_duration_minutes"]} '
                                    f'до {duration_minutes} хв.'
                                ),
                            )

                        appointment = Appointment.objects.create(
                            doctor=doctor,
                            service=form.cleaned_data['service'],
                            patient=patient,
                            patient_first_name=patient_first_name,
                            patient_last_name=patient_last_name,
                            patient_phone=patient_phone,
                            patient_email=patient.email if patient else '',
                            date=selected_date,
                            time=selected_time,
                            city=schedule.city,
                            address=schedule.address,
                            reason=form.cleaned_data['reason'],
                            duration_slots=duration_slots,
                            duration_minutes_exact=duration_minutes,
                            status=Appointment.STATUS_APPROVED,
                            approved_at=timezone.now(),
                        )
                    ensure_patient_card_from_appointment(appointment)
                    write_audit_log(request, 'Лікар записав пацієнта', appointment)
                    notify_patient_status(
                        appointment,
                        'doctor_created',
                        'Лікар створив для вас запис',
                    )
                    split_message = ' Стандартний слот поділено на два прийоми.' if split_source_id else ''
                    messages.success(request, f'Пацієнта записано.{split_message}{match_message}')
                    return redirect(doctor_appointments_week_url(selected_date))
                except (IntegrityError, Appointment.DoesNotExist):
                    messages.error(request, 'Цей час уже недоступний.')

    return render(
        request,
        'clinic/doctor_book_patient.html',
        {
            'doctor': doctor,
            'selected_date': selected_date,
            'selected_time': selected_time,
            'selected_schedule': schedule,
            'slots': slots,
            'split_option': split_option,
            'form': form,
            'earliest_booking_date': earliest_booking_date,
            'working_weekdays': working_weekdays,
            'working_weekday_labels': working_weekday_labels(working_weekdays),
        },
    )


@doctor_required
def doctor_patient_cards(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    query = request.GET.get('q', '').strip()
    cards = doctor.patient_cards.select_related('patient__profile')
    if query:
        normalized_query = normalize_phone_number(query)
        for term in query.split():
            term_filter = (
                Q(patient_first_name__icontains=term)
                | Q(patient_last_name__icontains=term)
                | Q(patient_phone__icontains=term)
            )
            if normalized_query:
                term_filter |= Q(patient_phone__icontains=normalized_query)
            if term.isdigit() and len(term) <= 3:
                term_filter |= Q(patient__profile__age=int(term))
            cards = cards.filter(term_filter)
    card_rows = []
    for card in cards:
        appointments = appointments_for_patient_card(doctor, card).exclude(
            status__in=[Appointment.STATUS_CANCELED, Appointment.STATUS_REJECTED]
        )
        last_visit = appointments.filter(status=Appointment.STATUS_COMPLETED).order_by('-date', '-time').first()
        next_visit = appointments.filter(status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_APPROVED]).order_by('date', 'time').first()
        card_rows.append(
            {
                'card': card,
                'appointments_count': appointments.count(),
                'last_visit': last_visit,
                'next_visit': next_visit,
            }
        )
    return render(
        request,
        'clinic/doctor_patient_cards.html',
        {
            'doctor': doctor,
            'card_rows': card_rows,
            'query': query,
        },
    )


@doctor_required
def doctor_patient_card_detail(request, card_id):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    card = get_object_or_404(
        DoctorPatientCard.objects.select_related('patient__profile'),
        pk=card_id,
        doctor=doctor,
    )
    action = request.POST.get('action') if request.method == 'POST' else None
    form = DoctorPatientCardForm(request.POST if action == 'update_card' else None, instance=card)
    entry_form = PatientRecordEntryForm(
        request.POST if action == 'add_entry' else None,
        request.FILES if action == 'add_entry' else None,
    )

    if action == 'update_card' and form.is_valid():
        form.save()
        write_audit_log(request, 'Оновлено картку пацієнта', card)
        messages.success(request, 'Картку пацієнта оновлено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'add_entry' and entry_form.is_valid():
        appointment = None
        if request.POST.get('appointment_id'):
            appointment = get_object_or_404(
                appointments_for_patient_card(doctor, card),
                pk=request.POST.get('appointment_id'),
            )
        entry = entry_form.save(commit=False)
        entry.card = card
        entry.doctor = doctor
        entry.appointment = appointment
        entry.save()
        for photo in entry_form.cleaned_data['photos']:
            PatientRecordImage.objects.create(entry=entry, image=photo)
        for video in entry_form.cleaned_data['videos']:
            PatientRecordVideo.objects.create(entry=entry, video=video)
        write_audit_log(request, 'Додано запис до картки', entry)
        messages.success(request, 'Новий запис додано до картки пацієнта.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'delete_entry':
        entry = get_object_or_404(card.record_entries, pk=request.POST.get('entry_id'), doctor=doctor)
        write_audit_log(request, 'Видалено запис із картки', entry)
        entry.delete()
        messages.success(request, 'Запис із картки видалено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'delete_image':
        image = get_object_or_404(
            PatientRecordImage,
            pk=request.POST.get('image_id'),
            entry__card=card,
            entry__doctor=doctor,
        )
        image.delete()
        write_audit_log(request, 'Видалено фото з картки', card)
        messages.success(request, 'Фотографію видалено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'delete_video':
        video = get_object_or_404(
            PatientRecordVideo,
            pk=request.POST.get('video_id'),
            entry__card=card,
            entry__doctor=doctor,
        )
        video.delete()
        write_audit_log(request, 'Видалено відео з картки', card)
        messages.success(request, 'Відео видалено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    appointments = appointments_for_patient_card(doctor, card).select_related(
        'service',
    ).order_by('-date', '-time')
    completed_visits = appointments.filter(status=Appointment.STATUS_COMPLETED).count()
    last_visit = appointments.filter(status=Appointment.STATUS_COMPLETED).first()
    first_visit = appointments.last()
    next_visit = appointments.filter(status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_APPROVED]).order_by('date', 'time').first()
    selected_appointment = None
    if request.GET.get('appointment'):
        selected_appointment = appointments.filter(pk=request.GET.get('appointment')).first()

    return render(
        request,
        'clinic/doctor_patient_card_detail.html',
        {
            'doctor': doctor,
            'card': card,
            'form': form,
            'entry_form': entry_form,
            'record_entries': card.record_entries.select_related('appointment__service').prefetch_related(
                'images',
                'videos',
            ),
            'selected_appointment': selected_appointment,
            'appointments': appointments,
            'completed_visits': completed_visits,
            'last_visit': last_visit,
            'first_visit': first_visit,
            'next_visit': next_visit,
        },
    )


@doctor_required
def doctor_schedule(request):
    doctor = request.user.doctor_profile
    weekday_values = {value for value, _ in WorkSchedule.WEEKDAY_CHOICES}
    schedules = list(doctor.schedules.select_related('workplace'))
    configured_weekdays = {schedule.weekday for schedule in schedules}
    missing_weekdays = weekday_values - configured_weekdays

    edit_weekday_raw = request.GET.get('edit')
    try:
        edit_weekday = int(edit_weekday_raw) if edit_weekday_raw is not None else None
    except (TypeError, ValueError):
        edit_weekday = None
    editing_schedule = next(
        (schedule for schedule in schedules if schedule.weekday == edit_weekday),
        None,
    )
    if edit_weekday_raw is not None and editing_schedule is None:
        messages.error(request, 'День графіка не знайдено.')
        return redirect('doctor_schedule')

    if request.method == 'POST':
        form = WorkScheduleForm(
            request.POST,
            instance=editing_schedule,
            doctor=doctor,
            allowed_weekdays=missing_weekdays if editing_schedule is None else None,
            locked_weekday=editing_schedule.weekday if editing_schedule else None,
        )
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.doctor = doctor
            schedule.save()
            write_audit_log(request, 'Збережено графік лікаря', schedule)
            messages.success(request, 'Графік збережено.')
            return redirect('doctor_schedule')
    else:
        form = WorkScheduleForm(
            instance=editing_schedule,
            doctor=doctor,
            allowed_weekdays=missing_weekdays if editing_schedule is None else None,
            locked_weekday=editing_schedule.weekday if editing_schedule else None,
        )

    return render(
        request,
        'clinic/doctor_schedule.html',
        {
            'doctor': doctor,
            'schedules': schedules,
            'workplaces': doctor.workplaces.all(),
            'form': form,
            'editing_schedule': editing_schedule,
            'all_days_configured': not missing_weekdays,
        },
    )


@doctor_required
def doctor_workplaces(request):
    doctor = request.user.doctor_profile
    edit_id = request.GET.get('edit')
    instance = doctor.workplaces.filter(pk=edit_id).first() if edit_id else None

    if request.method == 'POST' and request.POST.get('action') == 'delete':
        workplace = get_object_or_404(doctor.workplaces, pk=request.POST.get('workplace_id'))
        if workplace.schedules.exists():
            messages.error(
                request,
                'Це місце використовується у графіку. Спочатку оберіть інше місце для відповідних днів.',
            )
        else:
            write_audit_log(request, 'Видалено місце прийому', workplace)
            workplace.delete()
            messages.success(request, 'Місце прийому видалено.')
        return redirect('doctor_workplaces')

    form = DoctorWorkplaceForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        workplace = form.save(commit=False)
        workplace.doctor = doctor
        workplace.save()
        doctor.schedules.filter(workplace=workplace).update(
            city=workplace.city,
            address=workplace.address,
        )
        write_audit_log(request, 'Збережено місце прийому', workplace)
        messages.success(request, 'Місце прийому збережено.')
        return redirect('doctor_workplaces')

    return render(
        request,
        'clinic/doctor_workplaces.html',
        {
            'doctor': doctor,
            'workplaces': doctor.workplaces.annotate(schedule_count=Count('schedules')),
            'form': form,
            'editing': instance,
        },
    )


@doctor_required
def doctor_services(request):
    doctor = request.user.doctor_profile
    edit_id = request.GET.get('edit')
    instance = doctor.services.filter(pk=edit_id).first() if edit_id else None

    if request.method == 'POST' and request.POST.get('action') in {'move_up', 'move_down'}:
        service = get_object_or_404(doctor.services, pk=request.POST.get('service_id'))
        services = list(doctor.services.order_by('sort_order', 'id'))
        current_index = services.index(service)
        offset = -1 if request.POST['action'] == 'move_up' else 1
        target_index = current_index + offset
        if 0 <= target_index < len(services):
            services[current_index], services[target_index] = services[target_index], services[current_index]
            for position, item in enumerate(services):
                item.sort_order = position
            MedicalService.objects.bulk_update(services, ['sort_order'])
            write_audit_log(request, 'Змінено порядок послуг', service)
            messages.success(request, 'Порядок послуг оновлено.')
        return redirect('doctor_services')

    if request.method == 'POST' and request.POST.get('action') == 'toggle_patient_visibility':
        service = get_object_or_404(doctor.services, pk=request.POST.get('service_id'))
        service.is_patient_selectable = not service.is_patient_selectable
        service.save(update_fields=['is_patient_selectable'])
        if service.is_patient_selectable:
            message = f'Послугу «{service.name}» показано пацієнтам під час запису.'
            audit_action = 'Послугу відкрито для онлайн-запису'
        else:
            message = f'Послугу «{service.name}» приховано від пацієнтів під час запису.'
            audit_action = 'Послугу приховано від онлайн-запису'
        write_audit_log(request, audit_action, service)
        messages.success(request, message)
        return redirect('doctor_services')

    if request.method == 'POST' and request.POST.get('action') == 'delete':
        service = get_object_or_404(doctor.services, pk=request.POST.get('service_id'))
        write_audit_log(request, 'Видалено послугу', service)
        service.delete()
        messages.success(request, 'Послугу видалено.')
        return redirect('doctor_services')

    if request.method == 'POST' and request.POST.get('action') == 'delete_image':
        image = get_object_or_404(
            MedicalServiceImage,
            pk=request.POST.get('image_id'),
            service__doctor=doctor,
        )
        service_id = image.service_id
        write_audit_log(request, 'Видалено фото послуги', image.service)
        image.delete()
        messages.success(request, 'Фотографію послуги видалено.')
        return redirect(f"{reverse('doctor_services')}?edit={service_id}")

    if request.method == 'POST' and request.POST.get('action') == 'delete_video':
        video = get_object_or_404(
            MedicalServiceVideo,
            pk=request.POST.get('video_id'),
            service__doctor=doctor,
        )
        service_id = video.service_id
        service = video.service
        write_audit_log(request, 'Видалено відео послуги', service)
        video.delete()
        messages.success(request, 'Відео послуги видалено.')
        return redirect(f"{reverse('doctor_services')}?edit={service_id}")

    form = ServiceForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        service = form.save(commit=False)
        service.doctor = doctor
        if service.pk is None:
            last_order = doctor.services.aggregate(max_order=Max('sort_order'))['max_order']
            service.sort_order = 0 if last_order is None else last_order + 1
        service.save()
        for photo in form.cleaned_data['photos']:
            MedicalServiceImage.objects.create(service=service, image=photo)
        for video in form.cleaned_data['videos']:
            MedicalServiceVideo.objects.create(service=service, video=video)
        write_audit_log(request, 'Збережено послугу', service)
        messages.success(request, 'Послугу збережено.')
        return redirect('doctor_services')

    return render(
        request,
        'clinic/doctor_services.html',
        {
            'doctor': doctor,
            'services': doctor.services.prefetch_related('images', 'videos'),
            'form': form,
            'editing': instance,
        },
    )


@doctor_required
def doctor_edit_profile(request):
    doctor = request.user.doctor_profile
    form = DoctorProfileForm(request.POST or None, request.FILES or None, doctor=doctor)
    if request.method == 'POST' and form.is_valid():
        form.save()
        write_audit_log(request, 'Оновлено профіль лікаря', doctor)
        messages.success(request, 'Профіль лікаря оновлено.')
        return redirect('doctor_dashboard')
    return render(request, 'clinic/doctor_edit_profile.html', {'form': form, 'doctor': doctor})


@doctor_required
def doctor_change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        write_audit_log(request, 'Змінено пароль лікаря', request.user.doctor_profile)
        messages.success(request, 'Пароль змінено.')
        return redirect('doctor_dashboard')
    return render(request, 'clinic/change_password.html', {'form': form})


@doctor_required
def doctor_news(request):
    doctor = request.user.doctor_profile
    edit_id = request.GET.get('edit')
    instance = doctor.news_posts.filter(pk=edit_id).first() if edit_id else None

    if request.method == 'POST' and request.POST.get('action') == 'delete':
        post = get_object_or_404(doctor.news_posts, pk=request.POST.get('post_id'))
        write_audit_log(request, 'Видалено новину лікаря', post)
        post.delete()
        messages.success(request, 'Новину видалено.')
        return redirect('doctor_news')

    form = NewsPostForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        post = form.save(commit=False)
        post.doctor = doctor
        post.save()
        write_audit_log(request, 'Збережено новину лікаря', post)
        messages.success(request, 'Новину збережено.')
        return redirect('doctor_news')

    return render(
        request,
        'clinic/doctor_news.html',
        {'doctor': doctor, 'posts': doctor.news_posts.all(), 'form': form, 'editing': instance},
    )


@admin_required
def admin_panel(request):
    refresh_completed_appointments()
    stats = {
        'patients': Profile.objects.filter(role=Profile.ROLE_PATIENT, user__is_active=True).count(),
        'doctors': Doctor.objects.filter(user__is_active=True).count(),
        'appointments': Appointment.objects.count(),
        'pending': Appointment.objects.filter(status=Appointment.STATUS_PENDING).count(),
    }
    users = User.objects.select_related('profile').order_by('last_name', 'first_name', 'username')
    appointments = (
        Appointment.objects.select_related('doctor__user', 'service')
        .order_by('-created_at')[:8]
    )
    return render(
        request,
        'clinic/admin_panel.html',
        {
            'stats': stats,
            'users': users,
            'doctors': (
                Doctor.objects.select_related('user')
                .annotate(total=Count('appointments'))
                .order_by('user__last_name', 'user__first_name')
            ),
            'appointments': appointments,
            'audit_events': AuditLog.objects.select_related('actor')[:12],
        },
    )


@admin_required
def admin_telegram_broadcast(request):
    connections = (
        TelegramConnection.objects.filter(is_active=True, user__is_active=True)
        .select_related('user__profile')
        .order_by('user__last_name', 'user__first_name', 'user__username')
    )
    form = AdminTelegramBroadcastForm(
        request.POST or None,
        connections=connections,
    )

    if request.method == 'POST' and form.is_valid():
        audience = form.cleaned_data['audience']
        recipient = form.cleaned_data['recipient']
        selected_connections = connections
        recipient_label = 'усім підключеним користувачам'
        if audience == AdminTelegramBroadcastForm.AUDIENCE_SINGLE:
            selected_connections = connections.filter(pk=recipient.pk)
            recipient_user = recipient.user
            recipient_label = recipient_user.get_full_name().strip() or recipient_user.username

        selected_connections = list(selected_connections)
        if not selected_connections:
            messages.warning(request, 'Немає активних користувачів із підключеним Telegram.')
        else:
            try:
                sent_count, failures = send_admin_broadcast(
                    selected_connections,
                    form.cleaned_data['message'],
                )
            except TelegramError as error:
                form.add_error(None, str(error))
            else:
                write_audit_log(
                    request,
                    'Надіслано Telegram-повідомлення',
                    request.user,
                    (
                        f'Адресат: {recipient_label}. Успішно: {sent_count}. '
                        f'Помилок: {len(failures)}. '
                        f'Текст: {form.cleaned_data["message"][:180]}'
                    ),
                )
                if sent_count:
                    messages.success(
                        request,
                        f'Telegram-повідомлення надіслано: {sent_count}.',
                    )
                if failures:
                    messages.warning(
                        request,
                        f'Не вдалося доставити повідомлення: {len(failures)}.',
                    )
                return redirect('admin_telegram_broadcast')

    return render(
        request,
        'clinic/admin_telegram_broadcast.html',
        {
            'form': form,
            'connected_count': connections.count(),
        },
    )


@admin_required
def admin_content(request):
    branding, _ = ClinicSettings.objects.get_or_create(pk=1)
    hero_id = request.GET.get('hero')
    news_id = request.GET.get('news')
    gallery_id = request.GET.get('gallery')
    hero_instance = HomeHeroSlide.objects.filter(pk=hero_id).first() if hero_id else None
    news_instance = NewsPost.objects.filter(pk=news_id).first() if news_id else None
    gallery_instance = GalleryImage.objects.filter(pk=gallery_id).first() if gallery_id else None

    settings_form = ClinicSettingsForm(instance=branding, prefix='settings')
    hero_form = HomeHeroSlideForm(instance=hero_instance, prefix='hero')
    news_form = NewsPostForm(instance=news_instance, prefix='news')
    gallery_form = GalleryImageForm(instance=gallery_instance, prefix='gallery')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_settings':
            settings_form = ClinicSettingsForm(request.POST, request.FILES, instance=branding, prefix='settings')
            if settings_form.is_valid():
                saved_branding = settings_form.save()
                write_audit_log(request, 'Оновлено оформлення сайту', saved_branding)
                messages.success(request, 'Оформлення клініки збережено.')
                return redirect(f"{reverse('admin_content')}#branding")
        elif action == 'save_hero':
            hero_instance = HomeHeroSlide.objects.filter(pk=request.POST.get('hero_id')).first()
            hero_form = HomeHeroSlideForm(
                request.POST,
                request.FILES,
                instance=hero_instance,
                prefix='hero',
            )
            if hero_form.is_valid():
                slide = hero_form.save(commit=False)
                if not slide.pk:
                    slide.sort_order = (
                        HomeHeroSlide.objects.aggregate(last_order=Max('sort_order'))['last_order'] or 0
                    ) + 1
                slide.save()
                write_audit_log(request, 'Збережено фото верхнього слайдера', slide)
                messages.success(request, 'Фотографію верхнього слайдера збережено.')
                return redirect(f"{reverse('admin_content')}#hero-slides")
        elif action == 'toggle_hero':
            slide = get_object_or_404(HomeHeroSlide, pk=request.POST.get('hero_id'))
            slide.is_active = not slide.is_active
            slide.save(update_fields=['is_active'])
            write_audit_log(request, 'Змінено видимість фото слайдера', slide)
            state = 'показується' if slide.is_active else 'прихована'
            messages.success(request, f'Фотографія тепер {state} на головній сторінці.')
            return redirect(f"{reverse('admin_content')}#hero-slides")
        elif action == 'move_hero':
            slide = get_object_or_404(HomeHeroSlide, pk=request.POST.get('hero_id'))
            direction = request.POST.get('direction')
            with transaction.atomic():
                ordered_slides = list(HomeHeroSlide.objects.select_for_update().order_by('sort_order', 'id'))
                for position, ordered_slide in enumerate(ordered_slides, start=1):
                    if ordered_slide.sort_order != position:
                        ordered_slide.sort_order = position
                        ordered_slide.save(update_fields=['sort_order'])
                current_index = next(
                    (index for index, ordered_slide in enumerate(ordered_slides) if ordered_slide.pk == slide.pk),
                    None,
                )
                offset = -1 if direction == 'up' else 1
                target_index = current_index + offset if current_index is not None else -1
                if current_index is not None and 0 <= target_index < len(ordered_slides):
                    neighbour = ordered_slides[target_index]
                    slide.sort_order, neighbour.sort_order = neighbour.sort_order, slide.sort_order
                    slide.save(update_fields=['sort_order'])
                    neighbour.save(update_fields=['sort_order'])
                    write_audit_log(request, 'Змінено порядок фото слайдера', slide)
                    messages.success(request, 'Порядок фотографій змінено.')
            return redirect(f"{reverse('admin_content')}#hero-slides")
        elif action == 'delete_hero':
            slide = get_object_or_404(HomeHeroSlide, pk=request.POST.get('hero_id'))
            write_audit_log(request, 'Видалено фото слайдера', slide)
            slide.delete()
            messages.success(request, 'Фотографію верхнього слайдера видалено.')
            return redirect(f"{reverse('admin_content')}#hero-slides")
        elif action == 'save_news':
            news_instance = NewsPost.objects.filter(pk=request.POST.get('news_id')).first()
            news_form = NewsPostForm(request.POST, request.FILES, instance=news_instance, prefix='news')
            if news_form.is_valid():
                post = news_form.save()
                write_audit_log(request, 'Збережено новину клініки', post)
                messages.success(request, 'Новину збережено.')
                return redirect(f"{reverse('admin_content')}#news")
        elif action == 'delete_news':
            post = get_object_or_404(NewsPost, pk=request.POST.get('news_id'))
            write_audit_log(request, 'Видалено новину', post)
            post.delete()
            messages.success(request, 'Новину видалено.')
            return redirect(f"{reverse('admin_content')}#news")
        elif action == 'save_gallery':
            gallery_instance = GalleryImage.objects.filter(pk=request.POST.get('gallery_id')).first()
            gallery_form = GalleryImageForm(
                request.POST,
                request.FILES,
                instance=gallery_instance,
                prefix='gallery',
            )
            if gallery_form.is_valid():
                gallery_item = gallery_form.save()
                write_audit_log(request, 'Збережено фото галереї', gallery_item)
                messages.success(request, 'Фотографію збережено.')
                return redirect(f"{reverse('admin_content')}#gallery")
        elif action == 'delete_gallery':
            gallery_item = get_object_or_404(GalleryImage, pk=request.POST.get('gallery_id'))
            write_audit_log(request, 'Видалено фото галереї', gallery_item)
            gallery_item.delete()
            messages.success(request, 'Фотографію видалено.')
            return redirect(f"{reverse('admin_content')}#gallery")

    hero_slides = HomeHeroSlide.objects.all()
    posts = NewsPost.objects.select_related('doctor__user').all()
    gallery_images = GalleryImage.objects.all()
    return render(
        request,
        'clinic/admin_content.html',
        {
            'settings_form': settings_form,
            'hero_form': hero_form,
            'news_form': news_form,
            'gallery_form': gallery_form,
            'hero_editing': hero_instance,
            'news_editing': news_instance,
            'gallery_editing': gallery_instance,
            'hero_slides': hero_slides,
            'posts': posts,
            'gallery_images': gallery_images,
            'content_stats': {
                'hero': hero_slides.filter(is_active=True).count(),
                'clinic_news': posts.filter(doctor__isnull=True, is_published=True).count(),
                'doctor_news': posts.filter(doctor__isnull=False, is_published=True).count(),
                'gallery': gallery_images.filter(is_published=True).count(),
            },
        },
    )


@admin_required
def admin_add_doctor(request):
    form = AdminDoctorCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        write_audit_log(request, 'Додано лікаря', user.doctor_profile)
        messages.success(request, f'Лікаря додано. Логін для входу: {user.username}')
        return redirect('admin_panel')
    return render(request, 'clinic/admin_add_doctor.html', {'form': form})


@admin_required
def admin_edit_user(request, user_id):
    edited_user = get_object_or_404(User, pk=user_id)
    form = AdminUserEditForm(request.POST or None, user=edited_user)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
                write_audit_log(request, 'Оновлено користувача', edited_user)
        except IntegrityError:
            form.add_error('phone', 'Цей номер телефону вже прив’язаний до іншого пацієнта.')
        else:
            messages.success(request, 'Дані користувача оновлено.')
            return redirect('admin_panel')
    return render(
        request,
        'clinic/admin_edit_user.html',
        {
            'form': form,
            'edited_user': edited_user,
        },
    )


@admin_required
def admin_toggle_user(request, user_id):
    edited_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        if edited_user == request.user:
            messages.error(request, 'Не можна архівувати самого себе.')
        else:
            edited_user.is_active = not edited_user.is_active
            edited_user.save(update_fields=['is_active'])
            action = 'Відновлено акаунт' if edited_user.is_active else 'Архівовано акаунт'
            write_audit_log(request, action, edited_user)
            message = 'Акаунт відновлено.' if edited_user.is_active else 'Акаунт перенесено до архіву.'
            messages.success(request, message)
    return redirect('admin_panel')


@admin_required
def admin_delete_user(request, user_id):
    edited_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        if edited_user == request.user:
            messages.error(request, 'Не можна видалити власний акаунт.')
        elif not hasattr(edited_user, 'profile') or edited_user.profile.role != Profile.ROLE_PATIENT:
            messages.error(request, 'Назавжди видаляти можна лише профілі пацієнтів.')
        elif edited_user.is_active:
            messages.error(request, 'Спочатку перенесіть профіль пацієнта до архіву.')
        else:
            profile_photo = edited_user.profile.photo
            write_audit_log(request, 'Назавжди видалено профіль пацієнта', edited_user)
            edited_user.delete()
            if profile_photo and profile_photo.name:
                profile_photo.storage.delete(profile_photo.name)
            messages.success(
                request,
                'Профіль пацієнта видалено назавжди. Історію прийомів і медичні записи збережено.',
            )
    return redirect('admin_panel')


@admin_required
def admin_cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(Appointment, pk=appointment_id)
    if request.method == 'POST' and appointment.can_cancel:
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        ensure_patient_card_from_appointment(appointment)
        write_audit_log(request, 'Скасовано запис адміністратором', appointment)
        notify_patient_status(
            appointment,
            'admin_canceled',
            'Адміністратор скасував прийом',
        )
        messages.success(request, 'Запис скасовано адміністратором.')
    elif request.method == 'POST':
        messages.error(request, 'Цей запис уже не можна скасувати.')
    return redirect('admin_panel')
