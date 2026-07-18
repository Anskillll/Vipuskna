from datetime import datetime, timedelta
from functools import wraps
from math import ceil
from urllib.parse import urlencode

from allauth.socialaccount.models import SocialAccount
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    AdminDoctorCreateForm,
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
    NewsPostForm,
    PatientRecordEntryForm,
    PatientProfileForm,
    ServiceForm,
    UsernameLoginForm,
    WorkScheduleForm,
    normalize_phone_number,
)
from .models import (
    Appointment,
    AppointmentImage,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    DoctorWorkplace,
    GalleryImage,
    MedicalService,
    MedicalServiceImage,
    NewsPost,
    PatientRecordEntry,
    PatientRecordImage,
    Profile,
    WorkSchedule,
)


BLOCKING_APPOINTMENT_STATUSES = [
    Appointment.STATUS_PENDING,
    Appointment.STATUS_APPROVED,
    Appointment.STATUS_COMPLETED,
    Appointment.STATUS_RESCHEDULE_PROPOSED,
]

UKRAINIAN_WEEKDAYS = (
    'Понеділок',
    'Вівторок',
    'Середа',
    'Четвер',
    "П'ятниця",
    'Субота',
    'Неділя',
)


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
        if request.user.is_authenticated and user_role(request.user) == Profile.ROLE_PATIENT:
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Увійдіть як пацієнт.')
        return redirect('home')

    return wrapper


def doctor_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and user_role(request.user) == Profile.ROLE_DOCTOR:
            if not request.user.is_active:
                messages.error(request, 'Ваш акаунт заблоковано.')
                return redirect('home')
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Увійдіть як лікар.')
        return redirect('doctor_login')

    return wrapper


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return view_func(request, *args, **kwargs)
        messages.error(request, 'Увійдіть як адміністратор.')
        return redirect('admin_login')

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


def slots_for_doctor(doctor, selected_date):
    schedule = schedule_for_date(doctor, selected_date)
    if not schedule:
        return None, []

    now = timezone.localtime()
    slots = [
        {
            'time': slot,
            'busy': appointment_conflicts(doctor, selected_date, slot, duration_slots=1) or (
                selected_date == now.date() and slot <= now.time().replace(second=0, microsecond=0)
            ),
        }
        for slot in schedule.get_slots()
    ]
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


def ensure_patient_card_from_appointment(appointment):
    normalized_phone = normalize_phone_number(appointment.patient_phone)
    if appointment.patient_phone != normalized_phone:
        appointment.patient_phone = normalized_phone
        appointment.save(update_fields=['patient_phone'])
    patient = appointment.patient or find_patient_by_contacts(appointment.patient_email, appointment.patient_phone)
    card = find_doctor_card_by_phone(appointment.doctor, normalized_phone)
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
        for appointment in Appointment.objects.filter(patient__isnull=True).only('id', 'patient_phone')
        if normalize_phone_number(appointment.patient_phone) == phone
    ]
    cards = [
        card
        for card in DoctorPatientCard.objects.filter(patient__isnull=True).only('id', 'patient_phone')
        if normalize_phone_number(card.patient_phone) == phone
    ]
    return appointments, cards


def sync_patient_cards_for_doctor(doctor):
    appointments = doctor.appointments.exclude(
        status__in=[Appointment.STATUS_CANCELED, Appointment.STATUS_REJECTED]
    )
    for appointment in appointments:
        ensure_patient_card_from_appointment(appointment)


def home(request):
    return render(
        request,
        'clinic/home.html',
        {
            'clinic_news': NewsPost.objects.filter(doctor__isnull=True, is_published=True)[:12],
            'doctor_news': NewsPost.objects.filter(doctor__isnull=False, is_published=True).select_related('doctor__user')[:12],
            'gallery_images': GalleryImage.objects.filter(is_published=True)[:18],
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
            if role == 'admin' and not user.is_staff:
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
        appointments, cards = unclaimed_records_for_phone(phone)
        if not appointments and not cards:
            form.add_error('phone', 'Записів із таким номером не знайдено. Перевірте номер або зверніться до лікаря.')
        else:
            request.session['patient_claim_phone'] = phone
            complete_url = reverse('claim_patient_complete')
            if request.user.is_authenticated and SocialAccount.objects.filter(
                user=request.user,
                provider='google',
            ).exists():
                return redirect(complete_url)
            google_url = reverse('google_login')
            return redirect(f'{google_url}?{urlencode({"next": complete_url})}')

    return render(request, 'clinic/claim_patient.html', {'form': form})


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
    if not appointments and not cards:
        request.session.pop('patient_claim_phone', None)
        messages.error(request, 'Неприв’язаних записів із цим номером більше немає.')
        return redirect('patient_dashboard')

    with transaction.atomic():
        profile.phone = phone
        profile.save(update_fields=['phone'])
        Appointment.objects.filter(pk__in=[item.pk for item in appointments]).update(patient=request.user)
        DoctorPatientCard.objects.filter(pk__in=[item.pk for item in cards]).update(patient=request.user)

    request.session.pop('patient_claim_phone', None)
    messages.success(request, 'Записи лікаря прив’язано до вашого кабінету.')
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
        .prefetch_related('images')
        .distinct()
    )
    return render(
        request,
        'clinic/patient_dashboard.html',
        {
            'pending': appointments.filter(status=Appointment.STATUS_PENDING),
            'reschedule_requests': appointments.filter(status=Appointment.STATUS_RESCHEDULE_PROPOSED),
            'open_requests_count': appointments.filter(
                status__in=[
                    Appointment.STATUS_PENDING,
                    Appointment.STATUS_RESCHEDULE_PROPOSED,
                ]
            ).count(),
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
        form.save()
        messages.success(request, 'Профіль оновлено.')
        return redirect('patient_dashboard')
    return render(request, 'clinic/patient_edit_profile.html', {'form': form})


@patient_required
def patient_change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
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
    if request.method == 'POST':
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        messages.success(request, 'Запис скасовано.')
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
        if not appointment.can_restore or appointment_conflicts(
            appointment.doctor,
            appointment.date,
            appointment.time,
            duration_slots=appointment.duration_slots,
            duration_minutes=appointment.duration_minutes,
            exclude_id=appointment.id,
        ):
            messages.error(request, 'Цей запис уже не можна відновити.')
        else:
            appointment.status = Appointment.STATUS_PENDING
            appointment.save(update_fields=['status'])
            messages.success(request, 'Заявку відновлено і знову відправлено лікарю.')
    return redirect('patient_dashboard')


@patient_required
def patient_appointment_detail(request, appointment_id):
    appointment = get_object_or_404(
        Appointment.objects.select_related('doctor__user', 'service').prefetch_related('images'),
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
    ):
        messages.error(request, 'Цей час уже недоступний. Зверніться до лікаря для нового перенесення.')
        return redirect('patient_appointment_detail', appointment_id=appointment.id)

    appointment.status = Appointment.STATUS_APPROVED
    appointment.approved_at = timezone.now()
    appointment.save(update_fields=['status', 'approved_at'])
    ensure_patient_card_from_appointment(appointment)
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
        .prefetch_related('images', 'doctor__schedules'),
        pk=service_id,
    )
    return render(
        request,
        'clinic/service_detail.html',
        {
            'doctor': service.doctor,
            'service': service,
        },
    )


@patient_required
def booking(request):
    refresh_completed_appointments()
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
    selected_date = parse_date(request.GET.get('date')) if request.GET.get('date') else timezone.localdate()
    if selected_date < timezone.localdate():
        messages.error(request, 'Не можна вибрати минулу дату.')
        selected_date = timezone.localdate()
    selected_time = parse_time(request.GET.get('time'))

    if request.method == 'POST':
        selected_doctor = get_object_or_404(doctors, pk=request.POST.get('doctor'))
        selected_date = parse_date(request.POST.get('date'))
        selected_time = parse_time(request.POST.get('time'))
        reason_form = BookingReasonForm(request.POST, request.FILES, doctor=selected_doctor)
        schedule, slots = slots_for_doctor(selected_doctor, selected_date)
        available_times = [slot['time'] for slot in slots if not slot['busy']]

        if is_past_appointment(selected_date, selected_time):
            messages.error(request, 'Не можна записатися на минулу дату або час.')
        elif not selected_time or selected_time not in available_times:
            messages.error(request, 'Цей час уже недоступний.')
        elif reason_form.is_valid() and schedule:
            duration_slots = 1
            if appointment_conflicts(selected_doctor, selected_date, selected_time, duration_slots=1):
                messages.error(request, 'Цей час уже недоступний.')
            else:
                try:
                    appointment = Appointment.objects.create(
                        doctor=selected_doctor,
                        service=reason_form.cleaned_data['service'],
                        patient=request.user,
                        patient_first_name=request.user.first_name,
                        patient_last_name=request.user.last_name,
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
                    ensure_patient_card_from_appointment(appointment)
                    messages.success(request, 'Заявку відправлено лікарю на підтвердження.')
                    return redirect('patient_dashboard')
                except IntegrityError:
                    messages.error(request, 'Цей час уже недоступний.')
    else:
        reason_form = BookingReasonForm(doctor=selected_doctor)

    schedule, slots = slots_for_doctor(selected_doctor, selected_date) if selected_doctor else (None, [])
    has_bookable_services = bool(
        selected_doctor
        and selected_doctor.services.filter(is_patient_selectable=True).exists()
    )

    return render(
        request,
        'clinic/booking.html',
        {
            'doctors': doctors,
            'selected_doctor': selected_doctor,
            'selected_date': selected_date,
            'selected_time': selected_time,
            'selected_schedule': schedule,
            'slots': slots,
            'reason_form': reason_form,
            'has_bookable_services': has_bookable_services,
        },
    )


@doctor_required
def doctor_dashboard(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    current_appointment = active_appointment_for_doctor(doctor)
    current_card = None
    if current_appointment:
        current_card, _ = ensure_patient_card_from_appointment(current_appointment)
    return render(
        request,
        'clinic/doctor_dashboard.html',
        {
            'doctor': doctor,
            'schedules': doctor.schedules.select_related('workplace'),
            'current_appointment': current_appointment,
            'current_card': current_card,
        },
    )


@doctor_required
def doctor_appointments(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    sync_patient_cards_for_doctor(doctor)
    appointments = doctor.appointments.select_related('service', 'patient__profile').order_by('date', 'time')
    cards_by_phone = {
        card.patient_phone: card
        for card in doctor.patient_cards.select_related('patient__profile')
    }
    for appointment in appointments:
        appointment.patient_card = cards_by_phone.get(appointment.patient_phone)
        appointment.weekday_name = UKRAINIAN_WEEKDAYS[appointment.date.weekday()]
    return render(
        request,
        'clinic/doctor_appointments.html',
        {
            'doctor': doctor,
            'appointments': appointments,
        },
    )


@doctor_required
def doctor_appointment_detail(request, appointment_id):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    appointment = get_object_or_404(
        doctor.appointments.select_related('service', 'patient__profile').prefetch_related('images'),
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
            messages.success(request, 'Заявку відхилено.')
            return redirect('doctor_appointments')

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
            else:
                appointment.duration_slots = duration_slots
                appointment.duration_minutes_exact = duration_minutes
                appointment.status = Appointment.STATUS_APPROVED
                appointment.approved_at = timezone.now()
                appointment.save(update_fields=['duration_slots', 'duration_minutes_exact', 'status', 'approved_at'])
                ensure_patient_card_from_appointment(appointment)
                messages.success(request, 'Заявку підтверджено.')
                return redirect('doctor_appointments')
        else:
            error = form.errors.get('duration_minutes')
            messages.error(request, error[0] if error else 'Перевірте тривалість прийому.')
    return redirect('doctor_appointments')


@doctor_required
def doctor_cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment,
        pk=appointment_id,
        doctor=request.user.doctor_profile,
    )
    if request.method == 'POST':
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        ensure_patient_card_from_appointment(appointment)
        messages.success(request, 'Запис пацієнта скасовано.')
    return redirect('doctor_appointment_detail', appointment_id=appointment.id)


@doctor_required
def doctor_book_patient(request):
    refresh_completed_appointments()
    doctor = request.user.doctor_profile
    selected_date = parse_date(request.GET.get('date')) if request.GET.get('date') else timezone.localdate()
    if selected_date < timezone.localdate():
        messages.error(request, 'Не можна вибрати минулу дату.')
        selected_date = timezone.localdate()
    selected_time = parse_time(request.GET.get('time'))
    schedule, slots = slots_for_doctor(doctor, selected_date)
    slot_minutes = schedule.slot_minutes if schedule else 60
    form = DoctorPatientBookingForm(request.POST or None, doctor=doctor, slot_minutes=slot_minutes)

    if request.method == 'POST':
        selected_date = parse_date(request.POST.get('date'))
        selected_time = parse_time(request.POST.get('time'))
        schedule, slots = slots_for_doctor(doctor, selected_date)
        slot_minutes = schedule.slot_minutes if schedule else 60
        form = DoctorPatientBookingForm(request.POST, doctor=doctor, slot_minutes=slot_minutes)
        available_times = [slot['time'] for slot in slots if not slot['busy']]

        if is_past_appointment(selected_date, selected_time):
            messages.error(request, 'Не можна записати пацієнта на минулу дату або час.')
        elif not selected_time or selected_time not in available_times:
            messages.error(request, 'Цей час уже недоступний.')
        elif form.is_valid() and schedule:
            duration_minutes = form.cleaned_data['duration_minutes']
            duration_slots = ceil(duration_minutes / slot_minutes)
            selected_patient = form.cleaned_data.get('patient')
            patient = selected_patient or find_patient_by_contacts(
                '',
                form.cleaned_data['phone'],
            )
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
                if not selected_patient:
                    match_message = ' Номер уже належить зареєстрованому пацієнту, запис додано до його кабінету.'
            elif existing_card:
                patient_first_name = existing_card.patient_first_name
                patient_last_name = existing_card.patient_last_name
                match_message = ' Номер уже був у картці пацієнта, використано наявну картку.'
            if appointment_conflicts(
                doctor,
                selected_date,
                selected_time,
                duration_slots=duration_slots,
                duration_minutes=duration_minutes,
            ):
                messages.error(request, 'Для такої тривалості недостатньо вільного часу.')
            else:
                try:
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
                    messages.success(request, f'Пацієнта записано.{match_message}')
                    return redirect('doctor_appointments')
                except IntegrityError:
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
            'form': form,
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
            cards = cards.filter(term_filter)
    card_rows = []
    for card in cards:
        appointments = doctor.appointments.filter(patient_phone=card.patient_phone).exclude(
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
        messages.success(request, 'Картку пацієнта оновлено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'add_entry' and entry_form.is_valid():
        appointment = None
        if request.POST.get('appointment_id'):
            appointment = get_object_or_404(
                doctor.appointments,
                pk=request.POST.get('appointment_id'),
                patient_phone=card.patient_phone,
            )
        entry = entry_form.save(commit=False)
        entry.card = card
        entry.doctor = doctor
        entry.appointment = appointment
        entry.save()
        for photo in entry_form.cleaned_data['photos']:
            PatientRecordImage.objects.create(entry=entry, image=photo)
        messages.success(request, 'Новий запис додано до картки пацієнта.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    if action == 'delete_entry':
        entry = get_object_or_404(card.record_entries, pk=request.POST.get('entry_id'), doctor=doctor)
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
        messages.success(request, 'Фотографію видалено.')
        return redirect('doctor_patient_card_detail', card_id=card.id)

    appointments = doctor.appointments.filter(patient_phone=card.patient_phone).select_related('service').order_by('-date', '-time')
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
            'record_entries': card.record_entries.select_related('appointment__service').prefetch_related('images'),
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
    instance = None
    if request.method == 'POST':
        weekday = request.POST.get('weekday')
        instance = WorkSchedule.objects.filter(doctor=doctor, weekday=weekday).first()
        form = WorkScheduleForm(request.POST, instance=instance, doctor=doctor)
        if form.is_valid():
            schedule = form.save(commit=False)
            schedule.doctor = doctor
            schedule.save()
            messages.success(request, 'Графік збережено.')
            return redirect('doctor_schedule')
    else:
        edit_weekday = request.GET.get('weekday')
        if edit_weekday is not None:
            instance = WorkSchedule.objects.filter(doctor=doctor, weekday=edit_weekday).first()
        form = WorkScheduleForm(instance=instance, doctor=doctor)

    schedules = doctor.schedules.select_related('workplace')
    return render(
        request,
        'clinic/doctor_schedule.html',
        {
            'doctor': doctor,
            'schedules': schedules,
            'workplaces': doctor.workplaces.all(),
            'form': form,
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
            messages.success(request, 'Порядок послуг оновлено.')
        return redirect('doctor_services')

    if request.method == 'POST' and request.POST.get('action') == 'delete':
        service = get_object_or_404(doctor.services, pk=request.POST.get('service_id'))
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
        image.delete()
        messages.success(request, 'Фотографію послуги видалено.')
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
        messages.success(request, 'Послугу збережено.')
        return redirect('doctor_services')

    return render(
        request,
        'clinic/doctor_services.html',
        {
            'doctor': doctor,
            'services': doctor.services.prefetch_related('images'),
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
        messages.success(request, 'Профіль лікаря оновлено.')
        return redirect('doctor_dashboard')
    return render(request, 'clinic/doctor_edit_profile.html', {'form': form, 'doctor': doctor})


@doctor_required
def doctor_change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
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
        post.delete()
        messages.success(request, 'Новину видалено.')
        return redirect('doctor_news')

    form = NewsPostForm(request.POST or None, request.FILES or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        post = form.save(commit=False)
        post.doctor = doctor
        post.save()
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
        'patients': Profile.objects.filter(role=Profile.ROLE_PATIENT).count(),
        'doctors': Doctor.objects.count(),
        'appointments': Appointment.objects.count(),
        'pending': Appointment.objects.filter(status=Appointment.STATUS_PENDING).count(),
    }
    users = User.objects.select_related('profile').order_by('last_name', 'first_name')[:50]
    appointments = Appointment.objects.select_related('doctor__user').order_by('-date', '-time')[:50]
    return render(
        request,
        'clinic/admin_panel.html',
        {
            'stats': stats,
            'users': users,
            'doctors': Doctor.objects.select_related('user').annotate(total=Count('appointments')),
            'appointments': appointments,
        },
    )


@admin_required
def admin_content(request):
    branding, _ = ClinicSettings.objects.get_or_create(pk=1)
    news_id = request.GET.get('news')
    gallery_id = request.GET.get('gallery')
    news_instance = NewsPost.objects.filter(pk=news_id).first() if news_id else None
    gallery_instance = GalleryImage.objects.filter(pk=gallery_id).first() if gallery_id else None

    settings_form = ClinicSettingsForm(instance=branding, prefix='settings')
    news_form = NewsPostForm(instance=news_instance, prefix='news')
    gallery_form = GalleryImageForm(instance=gallery_instance, prefix='gallery')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_settings':
            settings_form = ClinicSettingsForm(request.POST, request.FILES, instance=branding, prefix='settings')
            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, 'Оформлення головної сторінки збережено.')
                return redirect('admin_content')
        elif action == 'save_news':
            post = NewsPost.objects.filter(pk=request.POST.get('news_id')).first()
            news_form = NewsPostForm(request.POST, request.FILES, instance=post, prefix='news')
            if news_form.is_valid():
                news_form.save()
                messages.success(request, 'Новину збережено.')
                return redirect('admin_content')
        elif action == 'delete_news':
            get_object_or_404(NewsPost, pk=request.POST.get('news_id')).delete()
            messages.success(request, 'Новину видалено.')
            return redirect('admin_content')
        elif action == 'save_gallery':
            image = GalleryImage.objects.filter(pk=request.POST.get('gallery_id')).first()
            gallery_form = GalleryImageForm(request.POST, request.FILES, instance=image, prefix='gallery')
            if gallery_form.is_valid():
                gallery_form.save()
                messages.success(request, 'Фотографію збережено.')
                return redirect('admin_content')
        elif action == 'delete_gallery':
            get_object_or_404(GalleryImage, pk=request.POST.get('gallery_id')).delete()
            messages.success(request, 'Фотографію видалено.')
            return redirect('admin_content')

    return render(
        request,
        'clinic/admin_content.html',
        {
            'settings_form': settings_form,
            'news_form': news_form,
            'gallery_form': gallery_form,
            'news_editing': news_instance,
            'gallery_editing': gallery_instance,
            'posts': NewsPost.objects.select_related('doctor__user').all(),
            'gallery_images': GalleryImage.objects.all(),
        },
    )


@admin_required
def admin_add_doctor(request):
    form = AdminDoctorCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        messages.success(request, f'Лікаря додано. Логін для входу: {user.username}')
        return redirect('admin_panel')
    return render(request, 'clinic/admin_add_doctor.html', {'form': form})


@admin_required
def admin_edit_user(request, user_id):
    edited_user = get_object_or_404(User, pk=user_id)
    form = AdminUserEditForm(request.POST or None, user=edited_user)
    if request.method == 'POST' and form.is_valid():
        form.save()
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
            messages.error(request, 'Не можна заблокувати самого себе.')
        else:
            edited_user.is_active = not edited_user.is_active
            edited_user.save(update_fields=['is_active'])
            messages.success(request, 'Статус акаунта змінено.')
    return redirect('admin_panel')


@admin_required
def admin_delete_user(request, user_id):
    edited_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        if edited_user == request.user:
            messages.error(request, 'Не можна видалити самого себе.')
        else:
            edited_user.delete()
            messages.success(request, 'Акаунт видалено.')
    return redirect('admin_panel')


@admin_required
def admin_cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(Appointment, pk=appointment_id)
    if request.method == 'POST':
        appointment.status = Appointment.STATUS_CANCELED
        appointment.save(update_fields=['status'])
        ensure_patient_card_from_appointment(appointment)
        messages.success(request, 'Запис скасовано адміністратором.')
    return redirect('admin_panel')
