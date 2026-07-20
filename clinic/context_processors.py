from datetime import datetime

from django.utils import timezone

from .models import Appointment, ClinicSettings, Doctor, PatientRecordEntry


DOCTOR_VISIT_PREVIEW_SESSION_KEY = 'doctor_visit_preview_card_id'


def clinic_branding(request):
    branding, _ = ClinicSettings.objects.get_or_create(pk=1)
    return {'clinic_branding': branding}


def doctor_visit_status(request):
    context = {
        'doctor_visit_show': False,
        'doctor_visit_is_preview': False,
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

    preview_requested = DOCTOR_VISIT_PREVIEW_SESSION_KEY in request.session
    is_preview = active_appointment is None and preview_requested
    card = None

    if active_appointment:
        request.session.pop(DOCTOR_VISIT_PREVIEW_SESSION_KEY, None)
        if active_appointment.patient_id:
            card = doctor.patient_cards.filter(patient_id=active_appointment.patient_id).first()
        if card is None:
            card = doctor.patient_cards.filter(
                patient_phone=active_appointment.patient_phone,
            ).first()
    elif is_preview:
        preview_card_id = request.session.get(DOCTOR_VISIT_PREVIEW_SESSION_KEY)
        if preview_card_id:
            card = doctor.patient_cards.filter(pk=preview_card_id).first()
        if card is None and preview_card_id:
            card = doctor.patient_cards.order_by('-updated_at').first()

    entries = PatientRecordEntry.objects.none()
    if card:
        entries = card.record_entries.select_related(
            'appointment__service',
        ).prefetch_related('images')

    context.update(
        {
            'doctor_visit_show': bool(active_appointment or is_preview),
            'doctor_visit_is_preview': is_preview,
            'doctor_visit_appointment': active_appointment,
            'doctor_visit_card': card,
            'doctor_visit_entries': entries,
        }
    )
    return context
