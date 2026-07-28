from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from .storage import private_media_storage
from .validators import validate_image_upload, validate_logo_upload, validate_video_upload


phone_validator = RegexValidator(
    regex=r'^\+?\d[\d\s().-]{7,18}$',
    message='Введіть телефон у форматі +380XXXXXXXXX.',
)


class Profile(models.Model):
    ROLE_PATIENT = 'patient'
    ROLE_DOCTOR = 'doctor'

    ROLE_CHOICES = [
        (ROLE_PATIENT, 'Пацієнт'),
        (ROLE_DOCTOR, 'Лікар'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name='Користувач',
    )
    role = models.CharField('Роль', max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField('Телефон', max_length=25, validators=[phone_validator], blank=True, default='')
    photo = models.ImageField(
        'Фото профілю',
        upload_to='patient_photos/',
        validators=[validate_image_upload],
        blank=True,
    )
    age = models.PositiveSmallIntegerField(
        'Вік',
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
    )

    class Meta:
        verbose_name = 'Профіль'
        verbose_name_plural = 'Профілі'
        constraints = [
            models.UniqueConstraint(
                fields=['phone'],
                condition=models.Q(role='patient') & ~models.Q(phone=''),
                name='unique_patient_profile_phone',
            ),
        ]

    def __str__(self):
        return f'{self.user.get_full_name()} ({self.get_role_display()})'


class Doctor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_profile',
        verbose_name='Користувач',
    )
    specialization = models.CharField('Спеціальність', max_length=120)
    phone = models.CharField('Телефон', max_length=25, validators=[phone_validator])
    photo = models.ImageField(
        'Фото лікаря',
        upload_to='doctor_photos/',
        validators=[validate_image_upload],
        blank=True,
    )
    photo_url = models.URLField('Посилання на фото', blank=True)
    description = models.TextField('Інформація про лікаря', blank=True)

    class Meta:
        verbose_name = 'Лікар'
        verbose_name_plural = 'Лікарі'
        ordering = ['user__last_name', 'user__first_name']

    def __str__(self):
        return f'{self.full_name} - {self.specialization}'

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def photo_src(self):
        if self.photo:
            return self.photo.url
        return self.photo_url

    @property
    def cities(self):
        cities = (
            self.schedules.filter(is_working=True)
            .exclude(city='')
            .values_list('city', flat=True)
        )
        return list(dict.fromkeys(cities))


class DoctorWorkplace(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='workplaces',
        verbose_name='Лікар',
    )
    name = models.CharField('Назва клініки або кабінету', max_length=160)
    city = models.CharField('Місто', max_length=100)
    address = models.CharField('Адреса', max_length=200)

    class Meta:
        verbose_name = 'Місце прийому'
        verbose_name_plural = 'Місця прийому'
        ordering = ['name', 'city', 'address']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'name'],
                name='unique_doctor_workplace_name',
            ),
        ]

    def __str__(self):
        return self.selection_label

    @property
    def has_generic_name(self):
        normalized = ' '.join(self.name.casefold().split())
        if normalized == 'основне місце прийому':
            return True
        if not normalized.startswith('місце прийому'):
            return False
        suffix = normalized.removeprefix('місце прийому').strip()
        return not suffix or suffix.isdigit()

    @property
    def display_name(self):
        if self.has_generic_name:
            return f'Кабінет у м. {self.city}'
        return self.name

    @property
    def selection_label(self):
        location = ', '.join(part for part in (self.city, self.address) if part)
        if self.has_generic_name:
            return location
        return f'{self.name} — {location}' if location else self.name


class MedicalService(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='services',
        verbose_name='Лікар',
    )
    name = models.CharField('Назва послуги', max_length=160)
    approximate_price = models.PositiveIntegerField(
        'Орієнтовна вартість',
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    description = models.TextField('Опис послуги', blank=True)
    sort_order = models.PositiveIntegerField('Порядок відображення', default=0)
    is_patient_selectable = models.BooleanField('Доступна для онлайн-запису', default=True)

    class Meta:
        verbose_name = 'Медична послуга'
        verbose_name_plural = 'Медичні послуги'
        ordering = ['sort_order', 'id']

    def __str__(self):
        if self.approximate_price is None:
            return self.name
        return f'{self.name} - орієнтовно {self.approximate_price} грн'


class MedicalServiceImage(models.Model):
    service = models.ForeignKey(
        MedicalService,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='Послуга',
    )
    image = models.ImageField(
        'Фотографія',
        upload_to='service_photos/',
        validators=[validate_image_upload],
    )
    created_at = models.DateTimeField('Додано', auto_now_add=True)

    class Meta:
        verbose_name = 'Фотографія послуги'
        verbose_name_plural = 'Фотографії послуг'
        ordering = ['id']

    def __str__(self):
        return f'Фото: {self.service.name}'


class MedicalServiceVideo(models.Model):
    service = models.ForeignKey(
        MedicalService,
        on_delete=models.CASCADE,
        related_name='videos',
        verbose_name='Послуга',
    )
    video = models.FileField(
        'Відео',
        upload_to='service_videos/',
        validators=[validate_video_upload],
    )
    created_at = models.DateTimeField('Додано', auto_now_add=True)

    class Meta:
        verbose_name = 'Відео послуги'
        verbose_name_plural = 'Відео послуг'
        ordering = ['id']

    def __str__(self):
        return f'Відео: {self.service.name}'


class WorkSchedule(models.Model):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6

    WEEKDAY_CHOICES = [
        (MONDAY, 'Понеділок'),
        (TUESDAY, 'Вівторок'),
        (WEDNESDAY, 'Середа'),
        (THURSDAY, 'Четвер'),
        (FRIDAY, 'Пʼятниця'),
        (SATURDAY, 'Субота'),
        (SUNDAY, 'Неділя'),
    ]

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='schedules',
        verbose_name='Лікар',
    )
    workplace = models.ForeignKey(
        DoctorWorkplace,
        on_delete=models.SET_NULL,
        related_name='schedules',
        null=True,
        blank=True,
        verbose_name='Місце прийому',
    )
    weekday = models.PositiveSmallIntegerField('День тижня', choices=WEEKDAY_CHOICES)
    city = models.CharField('Місто', max_length=100, blank=True)
    address = models.CharField('Адреса', max_length=200, blank=True)
    start_time = models.TimeField('Початок прийому', default=time(9, 0))
    end_time = models.TimeField('Кінець прийому', default=time(17, 0))
    slot_minutes = models.PositiveSmallIntegerField('Тривалість слота, хвилин', default=60)
    break_start_time = models.TimeField('Початок обідньої перерви', null=True, blank=True)
    break_duration_minutes = models.PositiveSmallIntegerField('Тривалість обіду, хвилин', null=True, blank=True)
    is_working = models.BooleanField('Робочий день', default=True)

    class Meta:
        verbose_name = 'Графік лікаря'
        verbose_name_plural = 'Графіки лікарів'
        ordering = ['weekday', 'start_time']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'weekday'],
                name='unique_doctor_weekday_schedule',
            ),
        ]

    def __str__(self):
        return f'{self.doctor.full_name}: {self.get_weekday_display()}'

    def get_slots(self):
        if not self.is_working:
            return []

        start = datetime.combine(timezone.localdate(), self.start_time)
        end = datetime.combine(timezone.localdate(), self.end_time)
        step = timedelta(minutes=self.slot_minutes)
        break_start = datetime.combine(timezone.localdate(), self.break_start_time) if self.break_start_time else None
        break_end = break_start + timedelta(minutes=self.break_duration_minutes) if break_start and self.break_duration_minutes else None
        slots = []

        while start < end:
            slot_end = start + step
            if not break_end or not (start < break_end and slot_end > break_start):
                slots.append(start.time())
            start += step

        return slots


class Appointment(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_COMPLETED = 'completed'
    STATUS_CANCELED = 'canceled'
    STATUS_REJECTED = 'rejected'
    STATUS_RESCHEDULE_PROPOSED = 'reschedule_proposed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Нова заявка'),
        (STATUS_APPROVED, 'Підтверджено'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_CANCELED, 'Скасовано'),
        (STATUS_REJECTED, 'Відхилено'),
        (STATUS_RESCHEDULE_PROPOSED, 'Очікує рішення пацієнта'),
    ]

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='appointments',
        verbose_name='Лікар',
    )
    service = models.ForeignKey(
        MedicalService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
        verbose_name='Послуга',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
        verbose_name='Зареєстрований пацієнт',
    )
    patient_first_name = models.CharField("Ім'я пацієнта", max_length=80)
    patient_last_name = models.CharField('Прізвище пацієнта', max_length=80)
    booked_for_other = models.BooleanField(
        'Запис створено для іншої людини',
        default=False,
    )
    patient_phone = models.CharField('Телефон пацієнта', max_length=25, validators=[phone_validator])
    patient_email = models.EmailField('Електронна пошта пацієнта', blank=True)
    date = models.DateField('Дата прийому')
    time = models.TimeField('Час прийому')
    city = models.CharField('Місто', max_length=100)
    address = models.CharField('Адреса', max_length=200)
    reason = models.TextField('Причина звернення')
    duration_slots = models.PositiveSmallIntegerField('Кількість слотів', default=1)
    duration_minutes_exact = models.PositiveSmallIntegerField('Тривалість прийому, хвилин', null=True, blank=True)
    status = models.CharField(
        'Статус',
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    approved_at = models.DateTimeField('Підтверджено', null=True, blank=True)
    previous_date = models.DateField('Попередня дата', null=True, blank=True)
    previous_time = models.TimeField('Попередній час', null=True, blank=True)
    reschedule_requested_at = models.DateTimeField('Запропоновано перенесення', null=True, blank=True)

    class Meta:
        verbose_name = 'Запис на прийом'
        verbose_name_plural = 'Записи на прийом'
        ordering = ['date', 'time']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'date', 'time'],
                condition=~models.Q(status__in=['canceled', 'rejected']),
                name='unique_active_appointment_slot',
            ),
        ]

    def __str__(self):
        return f'{self.patient_name} -> {self.doctor.full_name} {self.date} {self.time}'

    @property
    def patient_name(self):
        return f'{self.patient_first_name} {self.patient_last_name}'.strip()

    @property
    def patient_age(self):
        if (
            not self.booked_for_other
            and self.patient
            and hasattr(self.patient, 'profile')
        ):
            return self.patient.profile.age
        return None

    @property
    def booking_owner_name(self):
        if not self.patient:
            return ''
        return self.patient.get_full_name() or self.patient.username

    @property
    def is_future(self):
        visit = timezone.make_aware(datetime.combine(self.date, self.time))
        return visit > timezone.localtime()

    @property
    def restore_unavailable_reason(self):
        if self.status != self.STATUS_CANCELED:
            return ''
        if not self.is_future:
            return 'Минулий запис уже не можна відновити'

        schedule = self.doctor.schedules.filter(weekday=self.date.weekday()).first()
        if not schedule:
            return 'На цей день у лікаря більше немає робочого графіка'

        target_start = datetime.combine(self.date, self.time)
        target_end = target_start + timedelta(minutes=self.duration_minutes)
        day_start = datetime.combine(self.date, schedule.start_time)
        day_end = datetime.combine(self.date, schedule.end_time)
        if target_start < day_start or target_end > day_end:
            return 'Цей час більше не входить до робочого графіка лікаря'

        if schedule.break_start_time and schedule.break_duration_minutes:
            break_start = datetime.combine(self.date, schedule.break_start_time)
            break_end = break_start + timedelta(minutes=schedule.break_duration_minutes)
            if target_start < break_end and target_end > break_start:
                return 'На цей час у графіку лікаря запланована перерва'

        appointments = (
            Appointment.objects.filter(
                doctor=self.doctor,
                date=self.date,
            )
            .exclude(pk=self.pk)
            .exclude(status=self.STATUS_CANCELED)
            .exclude(status=self.STATUS_REJECTED)
        )
        for appointment in appointments:
            item_start = datetime.combine(appointment.date, appointment.time)
            item_end = item_start + timedelta(minutes=appointment.duration_minutes)
            if target_start < item_end and target_end > item_start:
                return 'Цей час уже зайнятий іншим записом'

        if self.patient:
            patient_appointments = (
                Appointment.objects.filter(
                    patient=self.patient,
                    date=self.date,
                )
                .exclude(pk=self.pk)
                .exclude(status=self.STATUS_CANCELED)
                .exclude(status=self.STATUS_REJECTED)
            )
            for appointment in patient_appointments:
                item_start = datetime.combine(appointment.date, appointment.time)
                item_end = item_start + timedelta(minutes=appointment.duration_minutes)
                if target_start < item_end and target_end > item_start:
                    return 'На цей час у вас уже є інший запис'
        return ''

    @property
    def can_restore(self):
        return (
            self.status == self.STATUS_CANCELED
            and not self.restore_unavailable_reason
        )

    @property
    def can_cancel(self):
        return self.status in {
            self.STATUS_PENDING,
            self.STATUS_APPROVED,
            self.STATUS_RESCHEDULE_PROPOSED,
        } and self.is_future

    @property
    def end_time(self):
        return (
            datetime.combine(self.date, self.time)
            + timedelta(minutes=self.duration_minutes)
        ).time()

    @property
    def slot_minutes(self):
        schedule = self.doctor.schedules.filter(weekday=self.date.weekday()).first()
        return schedule.slot_minutes if schedule else 60

    @property
    def duration_minutes(self):
        return self.duration_minutes_exact or self.duration_slots * self.slot_minutes


class AppointmentImage(models.Model):
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='Заявка',
    )
    image = models.ImageField(
        'Фотографія',
        upload_to='appointment_images/%Y/%m/',
        storage=private_media_storage,
        validators=[validate_image_upload],
    )
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Фото до заявки'
        verbose_name_plural = 'Фото до заявок'

    def __str__(self):
        return f'Фото до заявки {self.appointment_id}'


class AppointmentVideo(models.Model):
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name='videos',
        verbose_name='Заявка',
    )
    video = models.FileField(
        'Відео',
        upload_to='appointment_videos/%Y/%m/',
        storage=private_media_storage,
        validators=[validate_video_upload],
    )
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Відео до заявки'
        verbose_name_plural = 'Відео до заявок'

    def __str__(self):
        return f'Відео до заявки {self.appointment_id}'


class DoctorPatientCard(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='patient_cards',
        verbose_name='Лікар',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='doctor_cards',
        verbose_name='Зареєстрований пацієнт',
    )
    patient_first_name = models.CharField("Ім'я пацієнта", max_length=80)
    patient_last_name = models.CharField('Прізвище пацієнта', max_length=80, blank=True)
    patient_phone = models.CharField('Телефон пацієнта', max_length=25, validators=[phone_validator])
    patient_email = models.EmailField('Електронна пошта пацієнта', blank=True)
    notes = models.TextField('Нотатки лікаря', blank=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Картка пацієнта'
        verbose_name_plural = 'Картки пацієнтів'
        ordering = ['patient_last_name', 'patient_first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'patient'],
                condition=models.Q(patient__isnull=False),
                name='unique_doctor_registered_patient_card',
            ),
            models.UniqueConstraint(
                fields=[
                    'doctor',
                    'patient_phone',
                    'patient_first_name',
                    'patient_last_name',
                ],
                condition=models.Q(patient__isnull=True),
                name='unique_doctor_unregistered_patient_card',
            ),
        ]

    def __str__(self):
        return f'{self.full_name} - {self.doctor.full_name}'

    @property
    def full_name(self):
        return f'{self.patient_first_name} {self.patient_last_name}'.strip()

    @property
    def patient_age(self):
        if self.patient and hasattr(self.patient, 'profile'):
            return self.patient.profile.age
        return None


class PatientRecordEntry(models.Model):
    KIND_NOTE = 'note'
    KIND_EXAMINATION = 'examination'
    KIND_TREATMENT = 'treatment'
    KIND_RECOMMENDATION = 'recommendation'
    KIND_CHOICES = [
        (KIND_NOTE, 'Нотатка'),
        (KIND_EXAMINATION, 'Огляд'),
        (KIND_TREATMENT, 'Лікування'),
        (KIND_RECOMMENDATION, 'Рекомендації'),
    ]

    card = models.ForeignKey(
        DoctorPatientCard,
        on_delete=models.CASCADE,
        related_name='record_entries',
        verbose_name='Картка пацієнта',
    )
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='patient_record_entries',
        verbose_name='Лікар',
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        related_name='record_entries',
        null=True,
        blank=True,
        verbose_name='Прийом',
    )
    kind = models.CharField('Тип запису', max_length=24, choices=KIND_CHOICES, default=KIND_NOTE)
    title = models.CharField('Заголовок', max_length=180)
    details = models.TextField('Детальна інформація')
    recommendations = models.TextField('Рекомендації пацієнту', blank=True)
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Запис у картці пацієнта'
        verbose_name_plural = 'Записи у картках пацієнтів'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.card.full_name}: {self.title}'


class PatientRecordImage(models.Model):
    entry = models.ForeignKey(
        PatientRecordEntry,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='Медичний запис',
    )
    image = models.ImageField(
        'Фотографія',
        upload_to='patient_records/%Y/%m/',
        storage=private_media_storage,
        validators=[validate_image_upload],
    )
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Фото медичного запису'
        verbose_name_plural = 'Фото медичних записів'

    def __str__(self):
        return f'Фото до запису {self.entry_id}'


class PatientRecordVideo(models.Model):
    entry = models.ForeignKey(
        PatientRecordEntry,
        on_delete=models.CASCADE,
        related_name='videos',
        verbose_name='Медичний запис',
    )
    video = models.FileField(
        'Відео',
        upload_to='patient_record_videos/%Y/%m/',
        storage=private_media_storage,
        validators=[validate_video_upload],
    )
    uploaded_at = models.DateTimeField('Завантажено', auto_now_add=True)

    class Meta:
        verbose_name = 'Відео медичного запису'
        verbose_name_plural = 'Відео медичних записів'

    def __str__(self):
        return f'Відео до запису {self.entry_id}'


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clinic_audit_events',
        verbose_name='Хто виконав дію',
    )
    action = models.CharField('Дія', max_length=80)
    target_type = models.CharField('Тип об’єкта', max_length=80)
    target_id = models.CharField('Ідентифікатор об’єкта', max_length=80, blank=True)
    target_label = models.CharField('Назва об’єкта', max_length=240, blank=True)
    details = models.TextField('Деталі', blank=True)
    created_at = models.DateTimeField('Час', auto_now_add=True)

    class Meta:
        verbose_name = 'Подія журналу'
        verbose_name_plural = 'Журнал дій'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%d.%m.%Y %H:%M} — {self.action}'


class TelegramConnection(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_connection',
        verbose_name='Користувач',
    )
    chat_id = models.BigIntegerField('Telegram chat ID', unique=True)
    username = models.CharField('Telegram username', max_length=64, blank=True)
    first_name = models.CharField("Ім'я у Telegram", max_length=120, blank=True)
    is_active = models.BooleanField('Отримувати сповіщення', default=True)
    linked_at = models.DateTimeField('Підключено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Підключення Telegram'
        verbose_name_plural = 'Підключення Telegram'

    def __str__(self):
        return f'{self.user} — {self.chat_id}'


class TelegramLinkToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_link_tokens',
        verbose_name='Користувач',
    )
    token = models.CharField('Одноразовий код', max_length=64, unique=True)
    expires_at = models.DateTimeField('Діє до')
    used_at = models.DateTimeField('Використано', null=True, blank=True)
    created_at = models.DateTimeField('Створено', auto_now_add=True)

    class Meta:
        verbose_name = 'Код підключення Telegram'
        verbose_name_plural = 'Коди підключення Telegram'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} — {self.expires_at:%d.%m.%Y %H:%M}'


class TelegramNotification(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Очікує'),
        (STATUS_SENT, 'Надіслано'),
        (STATUS_FAILED, 'Помилка'),
    ]

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name='telegram_notifications',
        verbose_name='Запис на прийом',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='telegram_notifications',
        verbose_name='Одержувач',
    )
    event_key = models.CharField('Ключ події', max_length=180, unique=True)
    kind = models.CharField('Тип сповіщення', max_length=40)
    status = models.CharField(
        'Статус',
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    error = models.TextField('Помилка', blank=True)
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    sent_at = models.DateTimeField('Надіслано', null=True, blank=True)

    class Meta:
        verbose_name = 'Сповіщення Telegram'
        verbose_name_plural = 'Сповіщення Telegram'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.kind}: {self.recipient}'


class ClinicSettings(models.Model):
    EFFECT_NONE = 'none'
    EFFECT_TEETH = 'teeth'
    EFFECT_CROSSES = 'crosses'
    EFFECT_CUSTOM = 'custom'
    EFFECT_DENTAL_FIELD = 'dental_field'
    EFFECT_CHOICES = [
        (EFFECT_NONE, 'Без анімації'),
        (EFFECT_TEETH, 'Літаючі зуби'),
        (EFFECT_CROSSES, 'Медичні хрестики'),
        (EFFECT_CUSTOM, 'Власне зображення'),
        (EFFECT_DENTAL_FIELD, 'Стоматологічне поле'),
    ]

    clinic_name = models.CharField('Назва клініки', max_length=120, default='MedClinic')
    logo = models.FileField(
        'Логотип клініки',
        upload_to='clinic/branding/',
        validators=[validate_logo_upload],
        blank=True,
    )
    home_background = models.ImageField(
        'Фон сайту',
        upload_to='clinic/branding/',
        validators=[validate_image_upload],
        blank=True,
    )
    home_effect = models.CharField('Анімований ефект', max_length=20, choices=EFFECT_CHOICES, default=EFFECT_NONE)
    particle_image = models.ImageField(
        'Зображення частинок',
        upload_to='clinic/branding/particles/',
        validators=[validate_image_upload],
        blank=True,
    )

    class Meta:
        verbose_name = 'Оформлення клініки'
        verbose_name_plural = 'Оформлення клініки'

    def __str__(self):
        return self.clinic_name


class HomeHeroSlide(models.Model):
    title = models.CharField('Підпис для адміністратора', max_length=160, blank=True)
    image = models.ImageField(
        'Фотографія',
        upload_to='clinic/hero/',
        validators=[validate_image_upload],
    )
    is_active = models.BooleanField('Показувати у верхньому слайдері', default=True)
    sort_order = models.PositiveIntegerField('Порядок', default=0)
    created_at = models.DateTimeField('Створено', auto_now_add=True)

    class Meta:
        verbose_name = 'Фото верхнього слайдера'
        verbose_name_plural = 'Фото верхнього слайдера'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.title or f'Фото слайдера {self.pk}'


class NewsPost(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='news_posts',
        null=True,
        blank=True,
        verbose_name='Лікар',
    )
    title = models.CharField('Заголовок', max_length=180)
    text = models.TextField('Текст новини')
    image = models.ImageField(
        'Зображення',
        upload_to='clinic/news/',
        validators=[validate_image_upload],
        blank=True,
    )
    is_published = models.BooleanField('Опубліковано', default=True)
    created_at = models.DateTimeField('Створено', auto_now_add=True)
    updated_at = models.DateTimeField('Оновлено', auto_now=True)

    class Meta:
        verbose_name = 'Новина'
        verbose_name_plural = 'Новини'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_doctor_news(self):
        return self.doctor_id is not None


class GalleryImage(models.Model):
    title = models.CharField('Підпис до фото', max_length=160, blank=True)
    image = models.ImageField(
        'Фотографія',
        upload_to='clinic/gallery/',
        validators=[validate_image_upload],
    )
    is_published = models.BooleanField('Опубліковано', default=True)
    created_at = models.DateTimeField('Створено', auto_now_add=True)

    class Meta:
        verbose_name = 'Фото галереї'
        verbose_name_plural = 'Галерея'
        ordering = ['-created_at']

    def __str__(self):
        return self.title or f'Фото {self.pk}'
