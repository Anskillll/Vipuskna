from datetime import datetime

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.utils import timezone

from .models import (
    Appointment,
    ClinicSettings,
    Doctor,
    PatientRecordEntry,
    Profile,
    TelegramConnection,
)


def clinic_branding(request):
    branding, _ = ClinicSettings.objects.get_or_create(pk=1)
    return {'clinic_branding': branding}


def doctor_visit_status(request):
    context = {
        'doctor_visit_show': False,
        'doctor_visit_appointment': None,
        'doctor_visit_card': None,
        'doctor_visit_entries': PatientRecordEntry.objects.none(),
    }
    if not request.user.is_authenticated or request.user.is_staff:
        return context

    try:
        doctor = request.user.doctor_profile
    except Doctor.DoesNotExist:
        return context

    now = timezone.localtime()
    active_appointment = None
    appointments = doctor.appointments.filter(
        date=now.date(),
        status=Appointment.STATUS_APPROVED,
    ).select_related('service', 'patient').order_by('time')
    for appointment in appointments:
        start = timezone.make_aware(datetime.combine(appointment.date, appointment.time))
        end = timezone.make_aware(datetime.combine(appointment.date, appointment.end_time))
        if start <= now < end:
            active_appointment = appointment
            break

    card = None

    if active_appointment:
        if active_appointment.patient_id:
            card = doctor.patient_cards.filter(patient_id=active_appointment.patient_id).first()
        if card is None:
            card = doctor.patient_cards.filter(
                patient_phone=active_appointment.patient_phone,
            ).first()

    entries = PatientRecordEntry.objects.none()
    if card:
        entries = card.record_entries.select_related(
            'appointment__service',
        ).prefetch_related('images')

    context.update(
        {
            'doctor_visit_show': bool(active_appointment),
            'doctor_visit_appointment': active_appointment,
            'doctor_visit_card': card,
            'doctor_visit_entries': entries,
        }
    )
    return context


def telegram_status(request):
    context = {
        'telegram_enabled': bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME),
        'telegram_connected': False,
        'telegram_show_connect': False,
    }
    if not context['telegram_enabled'] or not request.user.is_authenticated or request.user.is_staff:
        return context

    try:
        role = request.user.profile.role
    except Profile.DoesNotExist:
        role = Profile.ROLE_DOCTOR if hasattr(request.user, 'doctor_profile') else None

    eligible = role == Profile.ROLE_DOCTOR
    if role == Profile.ROLE_PATIENT:
        eligible = SocialAccount.objects.filter(user=request.user, provider='google').exists()

    connected = TelegramConnection.objects.filter(user=request.user, is_active=True).exists()
    context['telegram_connected'] = connected
    context['telegram_show_connect'] = eligible and not connected
    return context
