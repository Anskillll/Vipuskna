from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


phone_validator = RegexValidator(
    regex=r'^\+?\d[\d\s().-]{7,18}$',
    message='Введите телефон в формате +380XXXXXXXXX.',
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
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=25, validators=[phone_validator], blank=True, default='')
    photo = models.ImageField(upload_to='patient_photos/', blank=True)
    age = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
    )

    class Meta:
        verbose_name = 'Профіль'
        verbose_name_plural = 'Профілі'

    def __str__(self):
        return f'{self.user.get_full_name()} ({self.get_role_display()})'


class Doctor(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_profile',
    )
    specialization = models.CharField(max_length=120)
    phone = models.CharField(max_length=25, validators=[phone_validator])
    photo = models.ImageField(upload_to='doctor_photos/', blank=True)
    photo_url = models.URLField(blank=True)
    description = models.TextField(blank=True)

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
    )
    name = models.CharField(max_length=160)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=200)

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
        return f'{self.name} — {self.city}, {self.address}'


class MedicalService(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='services',
    )
    name = models.CharField(max_length=160)
    approximate_price = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_patient_selectable = models.BooleanField(default=True)

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
    )
    image = models.ImageField(upload_to='service_photos/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фотографія послуги'
        verbose_name_plural = 'Фотографії послуг'
        ordering = ['id']

    def __str__(self):
        return f'Фото: {self.service.name}'


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
    )
    workplace = models.ForeignKey(
        DoctorWorkplace,
        on_delete=models.SET_NULL,
        related_name='schedules',
        null=True,
        blank=True,
    )
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAY_CHOICES)
    city = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=200, blank=True)
    start_time = models.TimeField(default=time(9, 0))
    end_time = models.TimeField(default=time(17, 0))
    slot_minutes = models.PositiveSmallIntegerField(default=60)
    break_start_time = models.TimeField(null=True, blank=True)
    break_duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    is_working = models.BooleanField(default=True)

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

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Нова заявка'),
        (STATUS_APPROVED, 'Підтверджено'),
        (STATUS_COMPLETED, 'Завершено'),
        (STATUS_CANCELED, 'Скасовано'),
        (STATUS_REJECTED, 'Відхилено'),
    ]

    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='appointments',
    )
    service = models.ForeignKey(
        MedicalService,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    patient_first_name = models.CharField(max_length=80)
    patient_last_name = models.CharField(max_length=80)
    patient_phone = models.CharField(max_length=25, validators=[phone_validator])
    patient_email = models.EmailField(blank=True)
    date = models.DateField()
    time = models.TimeField()
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=200)
    reason = models.TextField()
    duration_slots = models.PositiveSmallIntegerField(default=1)
    duration_minutes_exact = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

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
        if self.patient:
            return self.patient.get_full_name() or self.patient.username
        return f'{self.patient_first_name} {self.patient_last_name}'.strip()

    @property
    def patient_age(self):
        if self.patient and hasattr(self.patient, 'profile'):
            return self.patient.profile.age
        return None

    @property
    def is_future(self):
        visit = timezone.make_aware(datetime.combine(self.date, self.time))
        return visit > timezone.localtime()

    @property
    def can_restore(self):
        if self.status not in [self.STATUS_CANCELED, self.STATUS_REJECTED] or not self.is_future:
            return False
        schedule = self.doctor.schedules.filter(weekday=self.date.weekday()).first()
        slot_minutes = schedule.slot_minutes if schedule else 60
        target_start = datetime.combine(self.date, self.time)
        target_end = target_start + timedelta(minutes=self.duration_minutes)

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
                return False
        return True

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
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='appointment_images/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фото до заявки'
        verbose_name_plural = 'Фото до заявок'

    def __str__(self):
        return f'Фото до заявки {self.appointment_id}'


class DoctorPatientCard(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='patient_cards',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='doctor_cards',
    )
    patient_first_name = models.CharField(max_length=80)
    patient_last_name = models.CharField(max_length=80, blank=True)
    patient_phone = models.CharField(max_length=25, validators=[phone_validator])
    patient_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Картка пацієнта'
        verbose_name_plural = 'Картки пацієнтів'
        ordering = ['patient_last_name', 'patient_first_name']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'patient_phone'],
                name='unique_doctor_patient_phone_card',
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

    card = models.ForeignKey(DoctorPatientCard, on_delete=models.CASCADE, related_name='record_entries')
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name='patient_record_entries')
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        related_name='record_entries',
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=24, choices=KIND_CHOICES, default=KIND_NOTE)
    title = models.CharField(max_length=180)
    details = models.TextField()
    recommendations = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Запис у картці пацієнта'
        verbose_name_plural = 'Записи у картках пацієнтів'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.card.full_name}: {self.title}'


class PatientRecordImage(models.Model):
    entry = models.ForeignKey(PatientRecordEntry, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='patient_records/%Y/%m/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фото медичного запису'
        verbose_name_plural = 'Фото медичних записів'

    def __str__(self):
        return f'Фото до запису {self.entry_id}'


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

    clinic_name = models.CharField(max_length=120, default='MedClinic')
    logo = models.ImageField(upload_to='clinic/branding/', blank=True)
    home_background = models.ImageField(upload_to='clinic/branding/', blank=True)
    home_effect = models.CharField(max_length=20, choices=EFFECT_CHOICES, default=EFFECT_NONE)
    particle_image = models.ImageField(upload_to='clinic/branding/particles/', blank=True)

    class Meta:
        verbose_name = 'Оформлення клініки'
        verbose_name_plural = 'Оформлення клініки'

    def __str__(self):
        return self.clinic_name


class NewsPost(models.Model):
    doctor = models.ForeignKey(
        Doctor,
        on_delete=models.CASCADE,
        related_name='news_posts',
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=180)
    text = models.TextField()
    image = models.ImageField(upload_to='clinic/news/', blank=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    title = models.CharField(max_length=160, blank=True)
    image = models.ImageField(upload_to='clinic/gallery/')
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Фото галереї'
        verbose_name_plural = 'Галерея'
        ordering = ['-created_at']

    def __str__(self):
        return self.title or f'Фото {self.pk}'
