from datetime import datetime, timedelta

from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import (
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    DoctorWorkplace,
    GalleryImage,
    HomeHeroSlide,
    MedicalService,
    NewsPost,
    PatientRecordEntry,
    Profile,
    WorkSchedule,
)
from .validators import (
    MAX_IMAGE_COUNT,
    MAX_VIDEO_COUNT,
    validate_image_upload,
    validate_video_upload,
)


User = get_user_model()

phone_validator = RegexValidator(
    regex=r'^\+?\d[\d\s().-]{7,18}$',
    message='Введіть телефон у форматі +380XXXXXXXXX.',
)


def normalize_phone_number(value):
    digits = ''.join(character for character in (value or '') if character.isdigit())
    if len(digits) == 10 and digits.startswith('0'):
        digits = f'38{digits}'
    return f'+{digits}' if digits else ''


def patient_phone_is_used(phone, exclude_user=None):
    normalized = normalize_phone_number(phone)
    if not normalized:
        return False
    profiles = Profile.objects.filter(role=Profile.ROLE_PATIENT).exclude(phone='')
    if exclude_user:
        profiles = profiles.exclude(user=exclude_user)
    return any(normalize_phone_number(item.phone) == normalized for item in profiles.only('phone'))


class FormStyleMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')
            if isinstance(field.widget, forms.ClearableFileInput):
                field.widget.template_name = 'clinic/widgets/clearable_file_input.html'


class RegisterForm(FormStyleMixin, forms.Form):
    first_name = forms.CharField(label="Ім'я", max_length=80)
    last_name = forms.CharField(label='Прізвище', max_length=80)
    email = forms.EmailField(label='Електронна пошта')
    phone = forms.CharField(label='Телефон', validators=[phone_validator])
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError('Користувач із такою електронною поштою вже зареєстрований.')
        return email

    def clean_phone(self):
        phone = normalize_phone_number(self.cleaned_data['phone'])
        if patient_phone_is_used(phone):
            raise forms.ValidationError('Цей номер телефону вже прив’язаний до іншого пацієнта.')
        return phone

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data['email'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password'],
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
        )
        Profile.objects.create(
            user=user,
            role=Profile.ROLE_PATIENT,
            phone=self.cleaned_data['phone'],
        )
        return user


class UsernameLoginForm(FormStyleMixin, forms.Form):
    username = forms.CharField(label='Логін', max_length=150)
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)

    def clean_username(self):
        return self.cleaned_data['username'].strip()

    def get_user(self, request):
        return authenticate(
            request,
            username=self.cleaned_data['username'],
            password=self.cleaned_data['password'],
        )


class EmailForm(FormStyleMixin, forms.Form):
    email = forms.EmailField(label='Електронна пошта')


class ClaimPatientForm(FormStyleMixin, forms.Form):
    phone = forms.CharField(
        label='Номер телефону',
        validators=[phone_validator],
        help_text='Введіть свій номер або той самий номер, який ви повідомили лікарю.',
    )

    def clean_phone(self):
        return normalize_phone_number(self.cleaned_data['phone'])


class PatientProfileForm(FormStyleMixin, forms.Form):
    first_name = forms.CharField(label="Ім'я", max_length=80)
    last_name = forms.CharField(label='Прізвище', max_length=80)
    age = forms.IntegerField(label='Вік', min_value=1, max_value=120)
    phone = forms.CharField(label='Телефон', validators=[phone_validator])
    photo = forms.ImageField(
        label='Нове фото профілю',
        required=False,
        validators=[validate_image_upload],
        help_text='Залиште поле порожнім, якщо не хочете змінювати поточне фото.',
        widget=forms.FileInput(attrs={'accept': 'image/*'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        initial = kwargs.pop('initial', {})
        if user:
            initial.update(
                {
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'age': user.profile.age,
                    'phone': user.profile.phone,
                }
            )
        super().__init__(*args, initial=initial, **kwargs)

    def clean_phone(self):
        phone = normalize_phone_number(self.cleaned_data['phone'])
        if patient_phone_is_used(phone, exclude_user=self.user):
            raise forms.ValidationError('Цей номер телефону вже прив’язаний до іншого пацієнта.')
        return phone

    def save(self):
        self.user.first_name = self.cleaned_data['first_name']
        self.user.last_name = self.cleaned_data['last_name']
        self.user.save(update_fields=['first_name', 'last_name'])
        self.user.profile.phone = self.cleaned_data['phone']
        self.user.profile.age = self.cleaned_data['age']
        if self.cleaned_data.get('photo'):
            self.user.profile.photo = self.cleaned_data['photo']
        self.user.profile.save()
        return self.user


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    widget = MultipleImageInput(attrs={'multiple': True, 'accept': 'image/*'})
    default_validators = [validate_image_upload]

    def clean(self, data, initial=None):
        files = list(data) if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > MAX_IMAGE_COUNT:
            raise forms.ValidationError(f'Можна додати не більше {MAX_IMAGE_COUNT} фотографій за один раз.')
        single_clean = super().clean
        return [single_clean(item, initial) for item in files]


class MultipleVideoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleVideoField(forms.FileField):
    widget = MultipleVideoInput(
        attrs={
            'multiple': True,
            'accept': 'video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov',
        }
    )
    default_validators = [validate_video_upload]

    def clean(self, data, initial=None):
        files = list(data) if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > MAX_VIDEO_COUNT:
            raise forms.ValidationError(f'Можна додати не більше {MAX_VIDEO_COUNT} відео за один раз.')
        single_clean = super().clean
        return [single_clean(item, initial) for item in files]


class ServiceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, service):
        return service.name


class BookingReasonForm(FormStyleMixin, forms.Form):
    service = ServiceChoiceField(
        label='Послуга',
        queryset=MedicalService.objects.none(),
        empty_label=None,
    )
    reason = forms.CharField(
        label='Причина звернення',
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    photos = MultipleImageField(
        label='Фотографії до заявки',
        required=False,
        help_text='До 6 фотографій, не більше 8 МБ кожна.',
    )
    videos = MultipleVideoField(
        label='Відео до заявки',
        required=False,
        help_text='До 2 відео у форматі MP4, WEBM або MOV, не більше 50 МБ кожне.',
    )

    def __init__(self, *args, doctor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if doctor:
            queryset = doctor.services.filter(is_patient_selectable=True)
            self.fields['service'].queryset = queryset
            if not self.is_bound:
                consultation = queryset.filter(name__icontains='консульта').first()
                if consultation:
                    self.fields['service'].initial = consultation.pk


class PatientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        name = user.get_full_name() or user.username
        phone = getattr(user.profile, 'phone', '')
        return f'{name} ({phone})' if phone else name


class DoctorPatientBookingForm(FormStyleMixin, forms.Form):
    patient = PatientChoiceField(
        label='Зареєстрований пацієнт',
        queryset=User.objects.none(),
        required=False,
        empty_label='Ввести дані нового пацієнта вручну',
    )
    service = ServiceChoiceField(
        label='Послуга',
        queryset=MedicalService.objects.none(),
        empty_label=None,
    )
    duration_minutes = forms.IntegerField(
        label='Тривалість прийому, хвилин',
        min_value=1,
        initial=60,
    )
    first_name = forms.CharField(label="Ім'я", max_length=80, required=False)
    last_name = forms.CharField(label='Прізвище', max_length=80, required=False)
    phone = forms.CharField(label='Телефон', validators=[phone_validator], required=False)
    reason = forms.CharField(label='Причина звернення', widget=forms.Textarea(attrs={'rows': 4}))

    def __init__(self, *args, doctor=None, slot_minutes=60, **kwargs):
        self.slot_minutes = slot_minutes
        super().__init__(*args, **kwargs)
        self.fields['duration_minutes'].initial = slot_minutes
        self.fields['duration_minutes'].min_value = slot_minutes
        self.fields['duration_minutes'].widget.attrs.update({'min': slot_minutes, 'step': slot_minutes})
        self.fields['duration_minutes'].help_text = f'Один слот цього дня — {slot_minutes} хв. Можна вказати {slot_minutes}, {slot_minutes * 2}, {slot_minutes * 3} тощо.'
        self.fields['patient'].queryset = User.objects.filter(
            profile__role=Profile.ROLE_PATIENT,
        ).select_related('profile').order_by('last_name', 'first_name', 'username')
        if doctor:
            queryset = doctor.services.all()
            self.fields['service'].queryset = queryset
            if not self.is_bound:
                consultation = queryset.filter(name__icontains='консульта').first()
                if consultation:
                    self.fields['service'].initial = consultation.pk

    def clean_duration_minutes(self):
        value = self.cleaned_data['duration_minutes']
        if value % self.slot_minutes:
            raise forms.ValidationError(f'Тривалість має бути кратною тривалості слота: {self.slot_minutes} хв.')
        return value

    def clean(self):
        cleaned_data = super().clean()
        patient = cleaned_data.get('patient')

        if patient:
            phone = patient.profile.phone or cleaned_data.get('phone', '')
            cleaned_data['first_name'] = patient.first_name or patient.username
            cleaned_data['last_name'] = patient.last_name
            cleaned_data['phone'] = normalize_phone_number(phone)
            return cleaned_data

        if cleaned_data.get('phone'):
            phone = normalize_phone_number(cleaned_data['phone'])
            cleaned_data['phone'] = phone
            if patient_phone_is_used(phone):
                self.add_error(
                    'phone',
                    'Цей номер належить зареєстрованому пацієнту. Знайдіть і виберіть його вище.',
                )

        for field_name in ('first_name', 'last_name', 'phone'):
            if not cleaned_data.get(field_name):
                self.add_error(field_name, 'Заповніть це поле або оберіть зареєстрованого пацієнта.')
        return cleaned_data


class AppointmentDecisionForm(FormStyleMixin, forms.Form):
    duration_minutes = forms.IntegerField(
        label='Тривалість прийому, хвилин',
        min_value=1,
        initial=60,
    )

    def __init__(self, *args, slot_minutes=60, **kwargs):
        self.slot_minutes = slot_minutes
        super().__init__(*args, **kwargs)
        self.fields['duration_minutes'].initial = slot_minutes
        self.fields['duration_minutes'].min_value = slot_minutes
        self.fields['duration_minutes'].widget.attrs.update({'min': slot_minutes, 'step': slot_minutes})
        self.fields['duration_minutes'].help_text = f'Один слот у графіку цього дня — {slot_minutes} хв. Тривалість має бути кратною цьому часу.'

    def clean_duration_minutes(self):
        value = self.cleaned_data['duration_minutes']
        if value % self.slot_minutes:
            raise forms.ValidationError(f'Тривалість має бути кратною тривалості слота: {self.slot_minutes} хв.')
        return value


class AppointmentRescheduleForm(FormStyleMixin, forms.Form):
    date = forms.DateField(
        label='Нова дата',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    time = forms.TimeField(
        label='Новий час',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    duration_minutes = forms.IntegerField(
        label='Тривалість прийому, хвилин',
        min_value=1,
    )

    def __init__(self, *args, appointment=None, **kwargs):
        self.appointment = appointment
        self.schedule = None
        initial = kwargs.pop('initial', {})
        if appointment:
            initial = {
                'date': appointment.date,
                'time': appointment.time,
                'duration_minutes': appointment.duration_minutes,
                **initial,
            }
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)
        self.fields['date'].widget.attrs['min'] = timezone.localdate().isoformat()
        if appointment:
            slot_minutes = appointment.slot_minutes
            self.fields['duration_minutes'].widget.attrs.update(
                {'min': slot_minutes, 'step': slot_minutes}
            )
            self.fields['duration_minutes'].help_text = (
                f'Для поточного дня один слот триває {slot_minutes} хв. '
                'Після зміни дати система перевірить слот нового дня.'
            )

    def clean(self):
        cleaned_data = super().clean()
        selected_date = cleaned_data.get('date')
        selected_time = cleaned_data.get('time')
        duration_minutes = cleaned_data.get('duration_minutes')
        if not selected_date or not selected_time or not duration_minutes or not self.appointment:
            return cleaned_data

        now = timezone.localtime()
        if selected_date < now.date() or (
            selected_date == now.date()
            and selected_time <= now.time().replace(second=0, microsecond=0)
        ):
            self.add_error('date', 'Не можна запропонувати час у минулому.')
            return cleaned_data

        self.schedule = WorkSchedule.objects.filter(
            doctor=self.appointment.doctor,
            weekday=selected_date.weekday(),
            is_working=True,
        ).first()
        if not self.schedule:
            self.add_error('date', 'На цей день лікар не має робочого графіка.')
            return cleaned_data

        if selected_time not in self.schedule.get_slots():
            self.add_error('time', 'Оберіть час, який відповідає початку слота в графіку цього дня.')
        if duration_minutes % self.schedule.slot_minutes:
            self.add_error(
                'duration_minutes',
                f'Тривалість має бути кратною тривалості слота: {self.schedule.slot_minutes} хв.',
            )
        return cleaned_data


class DoctorPatientCardForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = DoctorPatientCard
        fields = [
            'patient_first_name',
            'patient_last_name',
            'patient_phone',
            'notes',
        ]
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 8}),
        }
        labels = {
            'patient_first_name': "Ім'я",
            'patient_last_name': 'Прізвище',
            'patient_phone': 'Телефон',
            'notes': 'Нотатки лікаря',
        }


class PatientRecordEntryForm(FormStyleMixin, forms.ModelForm):
    photos = MultipleImageField(
        label='Фотографії та знімки',
        required=False,
        help_text='До 6 фотографій, не більше 8 МБ кожна.',
    )
    videos = MultipleVideoField(
        label='Відео',
        required=False,
        help_text='До 2 відео у форматі MP4, WEBM або MOV, не більше 50 МБ кожне.',
    )

    class Meta:
        model = PatientRecordEntry
        fields = ['kind', 'title', 'details', 'recommendations']
        widgets = {
            'details': forms.Textarea(attrs={'rows': 6}),
            'recommendations': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {
            'kind': 'Тип запису',
            'title': 'Короткий заголовок',
            'details': 'Детальна інформація',
            'recommendations': 'Рекомендації пацієнту',
        }
        help_texts = {
            'kind': 'Записи типу «Лікування» та «Рекомендації» будуть видимі пацієнту в його кабінеті.',
        }


class DoctorProfileForm(FormStyleMixin, forms.Form):
    first_name = forms.CharField(label="Ім'я", max_length=80)
    last_name = forms.CharField(label='Прізвище', max_length=80)
    phone = forms.CharField(label='Телефон', validators=[phone_validator])
    specialization = forms.CharField(label='Спеціальність', max_length=120)
    photo = forms.ImageField(
        label='Фото з пристрою',
        required=False,
        validators=[validate_image_upload],
    )
    photo_url = forms.URLField(label='Посилання на фото', required=False)
    description = forms.CharField(
        label='Коротко про себе',
        required=False,
        max_length=300,
        help_text='Короткий текст, який пацієнти побачать у вкладці «Лікарі». До 300 символів.',
        widget=forms.Textarea(
            attrs={
                'rows': 3,
                'maxlength': 300,
                'placeholder': 'Наприклад: досвід роботи, підхід до пацієнтів та основні напрямки.',
            }
        ),
    )

    def __init__(self, *args, doctor=None, **kwargs):
        self.doctor = doctor
        initial = kwargs.pop('initial', {})
        if doctor:
            initial.update(
                {
                    'first_name': doctor.user.first_name,
                    'last_name': doctor.user.last_name,
                    'phone': doctor.phone,
                    'specialization': doctor.specialization,
                    'photo_url': doctor.photo_url,
                    'description': doctor.description,
                }
            )
        super().__init__(*args, initial=initial, **kwargs)

    def clean_phone(self):
        return normalize_phone_number(self.cleaned_data['phone'])

    def save(self):
        user = self.doctor.user
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save(update_fields=['first_name', 'last_name'])

        self.doctor.phone = self.cleaned_data['phone']
        self.doctor.specialization = self.cleaned_data['specialization']
        if self.cleaned_data.get('photo'):
            self.doctor.photo = self.cleaned_data['photo']
        self.doctor.photo_url = self.cleaned_data['photo_url']
        self.doctor.description = self.cleaned_data['description']
        self.doctor.save()

        user.profile.phone = self.cleaned_data['phone']
        user.profile.save()
        return self.doctor


class WorkplaceChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, workplace):
        return workplace.selection_label


class WorkScheduleForm(FormStyleMixin, forms.ModelForm):
    workplace = WorkplaceChoiceField(
        label='Місце прийому',
        queryset=DoctorWorkplace.objects.none(),
        empty_label=None,
    )

    class Meta:
        model = WorkSchedule
        fields = [
            'weekday',
            'workplace',
            'start_time',
            'end_time',
            'slot_minutes',
            'break_start_time',
            'break_duration_minutes',
            'is_working',
        ]
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
            'break_start_time': forms.TimeInput(attrs={'type': 'time'}),
        }
        labels = {
            'weekday': 'День тижня',
            'workplace': 'Місце прийому',
            'start_time': 'Початок прийому',
            'end_time': 'Кінець прийому',
            'slot_minutes': 'Тривалість одного слота, хвилин',
            'break_start_time': 'Початок обідньої перерви',
            'break_duration_minutes': 'Тривалість обіду, хвилин',
            'is_working': 'Робочий день',
        }

    def __init__(self, *args, doctor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['weekday'].choices = [
            (value, label)
            for value, label in self.fields['weekday'].choices
            if value != ''
        ]
        self.fields['workplace'].queryset = (
            doctor.workplaces.all()
            if doctor
            else DoctorWorkplace.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        start_time = cleaned.get('start_time')
        end_time = cleaned.get('end_time')
        is_working = cleaned.get('is_working')
        workplace = cleaned.get('workplace')
        break_start = cleaned.get('break_start_time')
        break_duration = cleaned.get('break_duration_minutes')

        if is_working and not workplace:
            self.add_error('workplace', 'Оберіть місце прийому або спочатку додайте нове.')
        if start_time and end_time and start_time >= end_time:
            self.add_error('end_time', 'Кінець прийому має бути пізніше за початок.')
        if bool(break_start) != bool(break_duration):
            self.add_error('break_start_time', 'Укажіть і початок, і тривалість обіду або залиште обидва поля порожніми.')
        if break_start and break_duration and start_time and end_time:
            break_end = (datetime.combine(timezone.localdate(), break_start) + timedelta(minutes=break_duration)).time()
            if break_start < start_time or break_end > end_time:
                self.add_error('break_start_time', 'Обідня перерва має повністю входити в робочий час.')
        return cleaned

    def save(self, commit=True):
        schedule = super().save(commit=False)
        if schedule.workplace:
            schedule.city = schedule.workplace.city
            schedule.address = schedule.workplace.address
        if commit:
            schedule.save()
        return schedule


class DoctorWorkplaceForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = DoctorWorkplace
        fields = ['name', 'city', 'address']
        labels = {
            'name': 'Назва клініки або кабінету',
            'city': 'Місто',
            'address': 'Адреса',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.has_generic_name and not self.is_bound:
            self.initial['name'] = self.instance.display_name


class ServiceForm(FormStyleMixin, forms.ModelForm):
    photos = MultipleImageField(
        label='Додати фотографії',
        required=False,
        help_text='До 6 фотографій, не більше 8 МБ кожна.',
    )
    videos = MultipleVideoField(
        label='Додати відео',
        required=False,
        help_text='До 2 відео у форматі MP4, WEBM або MOV, не більше 50 МБ кожне.',
    )

    class Meta:
        model = MedicalService
        fields = [
            'name',
            'approximate_price',
            'description',
            'is_patient_selectable',
            'photos',
            'videos',
        ]
        labels = {
            'name': 'Назва послуги',
            'approximate_price': 'Орієнтовна вартість, грн',
            'description': 'Детальна інформація про послугу',
            'is_patient_selectable': 'Дозволити пацієнтам обирати цю послугу під час запису',
        }
        help_texts = {
            'approximate_price': 'Необов’язково. Вкажіть приблизну суму, якщо її можна оцінити заздалегідь.',
            'description': 'Опишіть процедуру, її особливості, підготовку та іншу важливу інформацію.',
            'is_patient_selectable': 'Увімкніть для основних процедур, які пацієнт може самостійно вибрати онлайн.',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6}),
        }

    def clean_photos(self):
        photos = self.cleaned_data.get('photos', [])
        existing_count = self.instance.images.count() if self.instance.pk else 0
        if existing_count + len(photos) > MAX_IMAGE_COUNT:
            raise forms.ValidationError(
                f'Для однієї послуги можна зберегти не більше {MAX_IMAGE_COUNT} фотографій.'
            )
        return photos

    def clean_videos(self):
        videos = self.cleaned_data.get('videos', [])
        existing_count = self.instance.videos.count() if self.instance.pk else 0
        if existing_count + len(videos) > MAX_VIDEO_COUNT:
            raise forms.ValidationError(
                f'Для однієї послуги можна зберегти не більше {MAX_VIDEO_COUNT} відео.'
            )
        return videos


class AdminDoctorCreateForm(FormStyleMixin, forms.Form):
    username = forms.CharField(label='Логін', max_length=150)
    first_name = forms.CharField(label="Ім'я", max_length=80)
    last_name = forms.CharField(label='Прізвище', max_length=80)
    email = forms.EmailField(label='Електронна пошта', required=False)
    phone = forms.CharField(label='Телефон', validators=[phone_validator])
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)
    specialization = forms.CharField(label='Спеціальність', max_length=120)
    photo = forms.ImageField(
        label='Фото з пристрою',
        required=False,
        validators=[validate_image_upload],
    )
    description = forms.CharField(
        label='Опис',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    photo_url = forms.URLField(label='Посилання на фото', required=False)

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Такий логін уже використовується.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').lower().strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Ця електронна пошта вже використовується.')
        return email

    def save(self):
        user = User.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password'],
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
        )
        Profile.objects.create(
            user=user,
            role=Profile.ROLE_DOCTOR,
            phone=self.cleaned_data['phone'],
        )
        Doctor.objects.create(
            user=user,
            specialization=self.cleaned_data['specialization'],
            phone=self.cleaned_data['phone'],
            photo=self.cleaned_data.get('photo'),
            description=self.cleaned_data['description'],
            photo_url=self.cleaned_data['photo_url'],
        )
        return user


class AdminUserEditForm(FormStyleMixin, forms.Form):
    username = forms.CharField(label='Логін', max_length=150)
    first_name = forms.CharField(label="Ім'я", max_length=80)
    last_name = forms.CharField(label='Прізвище', max_length=80)
    email = forms.EmailField(label='Електронна пошта', required=False)
    phone = forms.CharField(label='Телефон', validators=[phone_validator], required=False)
    is_active = forms.BooleanField(label='Активний акаунт', required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        initial = kwargs.pop('initial', {})
        if user:
            initial.update(
                {
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'email': user.email,
                    'phone': getattr(getattr(user, 'profile', None), 'phone', ''),
                    'is_active': user.is_active,
                }
            )
        super().__init__(*args, initial=initial, **kwargs)

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        qs = User.objects.filter(username__iexact=username).exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError('Такий логін уже використовується.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').lower().strip()
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk)
        if email and qs.exists():
            raise forms.ValidationError('Ця електронна пошта вже використовується.')
        return email

    def clean_phone(self):
        phone = normalize_phone_number(self.cleaned_data.get('phone', ''))
        profile = getattr(self.user, 'profile', None)
        if profile and profile.role == Profile.ROLE_PATIENT:
            if patient_phone_is_used(phone, exclude_user=self.user):
                raise forms.ValidationError('Цей номер телефону вже прив’язаний до іншого пацієнта.')
        return phone

    def save(self):
        self.user.username = self.cleaned_data['username']
        self.user.first_name = self.cleaned_data['first_name']
        self.user.last_name = self.cleaned_data['last_name']
        self.user.email = self.cleaned_data['email']
        self.user.is_active = self.cleaned_data['is_active']
        self.user.save()

        if hasattr(self.user, 'profile'):
            self.user.profile.phone = self.cleaned_data['phone']
            self.user.profile.save()
        return self.user


class ClinicSettingsForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = ClinicSettings
        fields = ['clinic_name', 'logo', 'home_background', 'home_effect', 'particle_image']
        labels = {
            'clinic_name': 'Назва клініки',
            'logo': 'Логотип клініки',
            'home_background': 'Фон усього сайту',
            'home_effect': 'Анімований ефект поверх фону',
            'particle_image': 'Зображення для власного пресета',
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('home_effect') == ClinicSettings.EFFECT_CUSTOM and not cleaned_data.get('particle_image'):
            self.add_error('particle_image', 'Завантажте зображення для власного пресета.')
        return cleaned_data


class HomeHeroSlideForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = HomeHeroSlide
        fields = ['title', 'image', 'is_active']
        labels = {
            'title': 'Коротка назва фотографії',
            'image': 'Фотографія для верхнього слайдера',
            'is_active': 'Показувати фотографію на головній сторінці',
        }
        help_texts = {
            'title': 'Назву бачить лише адміністратор. Вона допомагає розрізняти фотографії.',
        }


class NewsPostForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = NewsPost
        fields = ['title', 'text', 'image', 'is_published']
        widgets = {'text': forms.Textarea(attrs={'rows': 7})}
        labels = {
            'title': 'Заголовок',
            'text': 'Текст новини',
            'image': 'Зображення',
            'is_published': 'Показувати на головній сторінці',
        }


class GalleryImageForm(FormStyleMixin, forms.ModelForm):
    class Meta:
        model = GalleryImage
        fields = ['title', 'image', 'is_published']
        labels = {
            'title': 'Підпис до фото',
            'image': 'Фотографія',
            'is_published': 'Показувати в галереї',
        }
