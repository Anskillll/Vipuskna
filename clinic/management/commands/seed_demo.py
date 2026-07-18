from datetime import time, timedelta
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from clinic.models import (
    Appointment,
    Doctor,
    DoctorPatientCard,
    GalleryImage,
    MedicalService,
    NewsPost,
    PatientRecordEntry,
    Profile,
    WorkSchedule,
)


DEMO_PASSWORD = 'Demo12345!'

DOCTORS = [
    ('Олександр', 'Мельник', 'Стоматолог-терапевт'),
    ('Марія', 'Коваль', 'Ортодонт'),
    ('Андрій', 'Бондар', 'Стоматолог-хірург'),
    ('Ірина', 'Шевченко', 'Дитячий стоматолог'),
    ('Олена', 'Ткаченко', 'Пародонтолог'),
    ('Максим', 'Кравченко', 'Імплантолог'),
    ('Наталія', 'Мороз', 'Стоматолог-гігієніст'),
    ('Сергій', 'Левченко', 'Ендодонтист'),
    ('Катерина', 'Олійник', 'Стоматолог-ортопед'),
    ('Віктор', 'Савчук', 'Стоматолог загальної практики'),
]

PATIENTS = [
    ('Анна', 'Бойко'), ('Дмитро', 'Ковальчук'), ('Софія', 'Марченко'),
    ('Богдан', 'Романюк'), ('Вікторія', 'Петренко'), ('Артем', 'Лисенко'),
    ('Поліна', 'Гриценко'), ('Михайло', 'Ткачук'), ('Дарина', 'Сидоренко'),
    ('Роман', 'Клименко'), ('Аліна', 'Мазур'), ('Іван', 'Шевчук'),
    ('Єва', 'Бондаренко'), ('Максим', 'Кравець'), ('Кіра', 'Олійник'),
    ('Назар', 'Мельничук'), ('Вероніка', 'Савченко'), ('Олег', 'Павленко'),
    ('Марина', 'Руденко'), ('Тимофій', 'Козак'), ('Юлія', 'Данилюк'),
    ('Олексій', 'Морозенко'), ('Злата', 'Левченко'), ('Андрій', 'Терещенко'),
    ('Катерина', 'Іваненко'),
]

SERVICE_CATALOG = {
    'consultation': (
        'Первинна консультація',
        500,
        'Знайомство з пацієнтом, збір скарг та огляд ротової порожнини. '
        'Лікар пояснює стан зубів і ясен та пропонує зрозумілий план наступних дій.',
    ),
    'diagnostics': (
        'Комплексна діагностика',
        900,
        'Розширений огляд із оцінкою прикусу, стану зубів, ясен і попередніх реставрацій. '
        'За потреби лікар рекомендує додаткові знімки.',
    ),
    'cleaning': (
        'Професійна гігієна',
        1600,
        'Делікатне видалення м’якого нальоту й зубного каменю, полірування поверхні зубів '
        'та персональні рекомендації з домашнього догляду.',
    ),
    'caries': (
        'Лікування карієсу',
        1900,
        'Очищення уражених тканин і відновлення форми зуба сучасним пломбувальним матеріалом. '
        'Остаточна вартість залежить від глибини ураження.',
    ),
    'restoration': (
        'Художня реставрація зуба',
        2800,
        'Відновлення природної форми, кольору та функції зуба фотополімерним матеріалом. '
        'Перед процедурою лікар узгоджує очікуваний результат.',
    ),
    'root_canal': (
        'Лікування кореневих каналів',
        4200,
        'Очищення, обробка та герметичне пломбування кореневих каналів. '
        'Кількість відвідувань визначається після діагностики.',
    ),
    'braces': (
        'Встановлення брекет-системи',
        26000,
        'Підбір і встановлення ортодонтичної системи після повної діагностики. '
        'Тип брекетів та етапи лікування обговорюються індивідуально.',
    ),
    'aligners': (
        'Лікування елайнерами',
        None,
        'Планування корекції прикусу прозорими капами. Кількість елайнерів і тривалість '
        'лікування залежать від складності клінічного випадку.',
    ),
    'activation': (
        'Активація ортодонтичного апарата',
        700,
        'Плановий контроль перебігу лікування, корекція дуги або апарата та перевірка гігієни.',
    ),
    'retainer': (
        'Виготовлення ретейнера',
        2200,
        'Виготовлення індивідуальної конструкції для утримання результату після '
        'ортодонтичного лікування.',
    ),
    'extraction': (
        'Видалення зуба',
        1800,
        'Акуратне видалення зуба під місцевим знеболенням. Перед процедурою лікар оцінює '
        'знімок і пояснює правила догляду після втручання.',
    ),
    'wisdom': (
        'Складне видалення зуба мудрості',
        4500,
        'Хірургічне видалення зуба мудрості після огляду та аналізу знімка. '
        'Точний обсяг процедури визначається індивідуально.',
    ),
    'implant': (
        'Встановлення імпланта',
        None,
        'Планування та хірургічне встановлення дентального імпланта. Вартість залежить '
        'від системи імпланта й потреби у додатковій підготовці.',
    ),
    'gum_treatment': (
        'Лікування ясен',
        2400,
        'Оцінка стану ясен, професійне очищення проблемних ділянок та складання '
        'індивідуального плану пародонтологічного лікування.',
    ),
    'fluoride': (
        'Фторування зубів',
        800,
        'Нанесення захисного препарату для зміцнення емалі та зниження чутливості зубів.',
    ),
    'children_check': (
        'Дитячий профілактичний огляд',
        450,
        'Спокійне знайомство дитини з лікарем, огляд зубів і прикусу та поради батькам '
        'щодо щоденної гігієни.',
    ),
    'fissure': (
        'Герметизація фісур',
        850,
        'Захист жувальної поверхні зуба спеціальним матеріалом для профілактики карієсу.',
    ),
    'crown': (
        'Виготовлення коронки',
        None,
        'Підготовка зуба, цифровий або класичний відбиток і виготовлення індивідуальної '
        'коронки. Матеріал обирається разом із пацієнтом.',
    ),
    'veneer': (
        'Встановлення вініра',
        None,
        'Естетичне відновлення передньої поверхні зуба. Форма, відтінок і матеріал '
        'узгоджуються після огляду та фотопротоколу.',
    ),
    'denture': (
        'Знімне протезування',
        None,
        'Підбір і виготовлення знімної конструкції з урахуванням прикусу, стану ясен '
        'та побажань пацієнта.',
    ),
}

DOCTOR_SERVICE_KEYS = [
    ('consultation', 'diagnostics', 'cleaning', 'caries', 'restoration', 'root_canal'),
    ('consultation', 'diagnostics', 'braces', 'aligners', 'activation', 'retainer'),
    ('consultation', 'diagnostics', 'extraction', 'wisdom', 'implant', 'gum_treatment'),
    ('children_check', 'consultation', 'cleaning', 'caries', 'fissure', 'fluoride'),
    ('consultation', 'diagnostics', 'cleaning', 'gum_treatment', 'fluoride', 'root_canal'),
    ('consultation', 'diagnostics', 'implant', 'extraction', 'crown', 'gum_treatment'),
    ('consultation', 'cleaning', 'fluoride', 'diagnostics', 'gum_treatment', 'children_check'),
    ('consultation', 'diagnostics', 'root_canal', 'caries', 'restoration', 'crown'),
    ('consultation', 'diagnostics', 'crown', 'veneer', 'denture', 'implant'),
    ('consultation', 'cleaning', 'caries', 'restoration', 'extraction', 'root_canal'),
]

CITIES = [
    ('Нікополь', 'вул. Шевченка, 18'),
    ('Дніпро', 'просп. Дмитра Яворницького, 54'),
    ('Запоріжжя', 'вул. Перемоги, 32'),
]

PATIENT_REASONS = [
    'Плановий огляд і консультація.',
    'Періодично турбує чутливість зуба.',
    'Потрібна професійна гігієна.',
    'Хочу перевірити стан пломби.',
    'Консультація щодо подальшого лікування.',
    'Дискомфорт під час жування.',
]

CLINIC_NEWS = [
    (
        'Новий кабінет для сімейних прийомів',
        'Ми підготували зручний кабінет для дорослих і маленьких пацієнтів. '
        'У ньому можна пройти огляд і спокійно обговорити план лікування.',
    ),
    (
        'Тиждень профілактичних оглядів',
        'Нагадуємо, що регулярний огляд допомагає помітити проблему на ранньому етапі. '
        'Оберіть лікаря та залиште заявку у своєму кабінеті.',
    ),
    (
        'Оновили обладнання для діагностики',
        'У клініці з’явилося нове обладнання, яке допомагає лікарям точніше планувати '
        'лікування та зрозуміло показувати результат пацієнту.',
    ),
]


def make_demo_image(label, color, size=(512, 512), portrait=True):
    image = Image.new('RGB', size, color)
    draw = ImageDraw.Draw(image)
    width, height = size
    accent = tuple(min(channel + 45, 255) for channel in color)

    if portrait:
        draw.ellipse(
            (width * 0.31, height * 0.14, width * 0.69, height * 0.52),
            fill=(245, 248, 255),
        )
        draw.rounded_rectangle(
            (width * 0.18, height * 0.48, width * 0.82, height * 0.96),
            radius=int(width * 0.18),
            fill=accent,
        )
    else:
        draw.ellipse(
            (width * 0.08, height * 0.12, width * 0.46, height * 0.78),
            outline=(255, 255, 255),
            width=max(6, width // 90),
        )
        draw.ellipse(
            (width * 0.54, height * 0.22, width * 0.92, height * 0.88),
            outline=(255, 255, 255),
            width=max(6, width // 90),
        )

    font = ImageFont.load_default(size=max(20, width // 12))
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    draw.rounded_rectangle(
        (
            width / 2 - text_width / 2 - 18,
            height * 0.79,
            width / 2 + text_width / 2 + 18,
            height * 0.92,
        ),
        radius=12,
        fill=(255, 255, 255),
    )
    draw.text(
        (width / 2 - text_width / 2, height * 0.81),
        label,
        fill=(20, 43, 80),
        font=font,
    )

    output = BytesIO()
    image.save(output, format='PNG')
    return ContentFile(output.getvalue())


def save_demo_image(instance, field_name, filename, label, color, size=(512, 512), portrait=True):
    field = getattr(instance, field_name)
    upload_folder = field.field.upload_to.rstrip('/')
    storage_name = f'{upload_folder}/{filename}'
    if default_storage.exists(storage_name):
        default_storage.delete(storage_name)
    field.save(
        filename,
        make_demo_image(label, color, size=size, portrait=portrait),
        save=True,
    )


class Command(BaseCommand):
    help = 'Створює демонстраційних лікарів, пацієнтів, послуги та прийоми.'

    def add_arguments(self, parser):
        parser.add_argument('--doctors', type=int, default=10)
        parser.add_argument('--patients', type=int, default=25)
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Видалити раніше створені demo_ акаунти перед заповненням.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        doctor_count = max(1, min(options['doctors'], len(DOCTORS)))
        patient_count = max(1, min(options['patients'], len(PATIENTS)))

        if options['reset']:
            User.objects.filter(username__startswith='demo_').delete()
        else:
            doctor_usernames = [f'demo_doctor_{index:02d}' for index in range(1, doctor_count + 1)]
            patient_usernames = [f'demo_patient_{index:02d}' for index in range(1, patient_count + 1)]
            User.objects.filter(username__startswith='demo_doctor_').exclude(
                username__in=doctor_usernames
            ).delete()
            User.objects.filter(username__startswith='demo_patient_').exclude(
                username__in=patient_usernames
            ).delete()

        self._fill_existing_service_descriptions()
        doctors = self._create_doctors(doctor_count)
        patients = self._create_patients(patient_count)
        appointments = self._create_appointments(doctors, patients)
        self._create_public_content()

        self.stdout.write(self.style.SUCCESS(
            f'Готово: {len(doctors)} лікарів, {len(patients)} пацієнтів, '
            f'{appointments} прийомів і заявок.'
        ))
        self.stdout.write(f'Пароль усіх demo_ акаунтів: {DEMO_PASSWORD}')

    def _fill_existing_service_descriptions(self):
        services = MedicalService.objects.exclude(
            doctor__user__username__startswith='demo_doctor_'
        ).filter(description='')
        for service in services:
            service.description = (
                f'Послуга «{service.name}» проводиться після огляду та консультації лікаря. '
                'Тривалість, етапи й очікуваний результат залежать від клінічної ситуації. '
                'Перед початком лікар відповість на запитання та узгодить індивідуальний план.'
            )
        MedicalService.objects.bulk_update(services, ['description'])

    def _create_doctors(self, doctor_count):
        doctors = []
        colors = [
            (51, 102, 204), (106, 76, 197), (20, 140, 126), (220, 90, 108),
            (45, 126, 160), (130, 92, 170), (24, 145, 105), (211, 112, 55),
            (72, 103, 171), (181, 75, 118),
        ]

        for index, (first_name, last_name, specialization) in enumerate(DOCTORS[:doctor_count], 1):
            username = f'demo_doctor_{index:02d}'
            user, _ = User.objects.get_or_create(username=username)
            user.first_name = first_name
            user.last_name = last_name
            user.email = ''
            user.is_active = True
            user.set_password(DEMO_PASSWORD)
            user.save()

            phone = f'+380671{index:05d}'
            Profile.objects.update_or_create(
                user=user,
                defaults={'role': Profile.ROLE_DOCTOR, 'phone': phone},
            )
            doctor, _ = Doctor.objects.update_or_create(
                user=user,
                defaults={
                    'specialization': specialization,
                    'phone': phone,
                    'description': (
                        f'{specialization} із уважним підходом до пацієнтів. '
                        'Пояснює кожен етап лікування простою мовою та допомагає '
                        'обрати комфортний план процедур.'
                    ),
                },
            )
            save_demo_image(
                doctor,
                'photo',
                f'demo_doctor_{index:02d}.png',
                f'D{index:02d}',
                colors[index - 1],
            )

            city, address = CITIES[(index - 1) % len(CITIES)]
            slot_minutes = 20 if index % 2 else 30
            for weekday in range(5):
                WorkSchedule.objects.update_or_create(
                    doctor=doctor,
                    weekday=weekday,
                    defaults={
                        'city': city,
                        'address': address,
                        'start_time': time(9, 0),
                        'end_time': time(17, 0),
                        'slot_minutes': slot_minutes,
                        'break_start_time': time(13, 0),
                        'break_duration_minutes': 40 if slot_minutes == 20 else 60,
                        'is_working': True,
                    },
                )
            doctor.schedules.exclude(weekday__lt=5).delete()

            service_names = []
            for order, key in enumerate(DOCTOR_SERVICE_KEYS[index - 1]):
                name, approximate_price, description = SERVICE_CATALOG[key]
                service_names.append(name)
                MedicalService.objects.update_or_create(
                    doctor=doctor,
                    name=name,
                    defaults={
                        'approximate_price': approximate_price,
                        'description': description,
                        'sort_order': order,
                        'is_patient_selectable': order < 4,
                    },
                )
            doctor.services.exclude(name__in=service_names).delete()

            news, _ = NewsPost.objects.update_or_create(
                doctor=doctor,
                title=f'Порада від лікаря {first_name} {last_name}',
                defaults={
                    'text': (
                        'Не відкладайте профілактичний огляд до появи болю. '
                        'Регулярна перевірка та правильний домашній догляд допомагають '
                        'зберігати здоров’я зубів і уникати складного лікування.'
                    ),
                    'is_published': True,
                },
            )
            save_demo_image(
                news,
                'image',
                f'demo_doctor_news_{index:02d}.png',
                f'TIP {index:02d}',
                colors[index - 1],
                size=(1200, 700),
                portrait=False,
            )
            doctors.append(doctor)
        return doctors

    def _create_patients(self, patient_count):
        patients = []
        colors = [
            (83, 112, 166), (47, 141, 132), (165, 90, 124), (135, 105, 181),
            (194, 116, 67), (72, 135, 175),
        ]
        for index, (first_name, last_name) in enumerate(PATIENTS[:patient_count], 1):
            username = f'demo_patient_{index:02d}'
            user, _ = User.objects.get_or_create(username=username)
            user.first_name = first_name
            user.last_name = last_name
            user.email = ''
            user.is_active = True
            user.set_password(DEMO_PASSWORD)
            user.save()

            profile, _ = Profile.objects.update_or_create(
                user=user,
                defaults={
                    'role': Profile.ROLE_PATIENT,
                    'phone': f'+380931{index:05d}',
                    'age': 9 + (index * 3) % 58,
                },
            )
            save_demo_image(
                profile,
                'photo',
                f'demo_patient_{index:02d}.png',
                f'P{index:02d}',
                colors[(index - 1) % len(colors)],
            )
            patients.append(user)
        return patients

    def _create_appointments(self, doctors, patients):
        Appointment.objects.filter(patient__username__startswith='demo_patient_').delete()
        DoctorPatientCard.objects.filter(patient__username__startswith='demo_patient_').delete()

        today = timezone.localdate()
        created_count = 0
        used_slots = set()

        for patient_index, patient in enumerate(patients):
            appointment_count = 1 + patient_index % 3
            for item_index in range(appointment_count):
                doctor = doctors[(patient_index * 2 + item_index) % len(doctors)]
                day_offset = (patient_index - len(patients) // 2) * 2 + item_index * 7
                visit_date = today + timedelta(days=day_offset)
                while visit_date.weekday() > 4:
                    visit_date += timedelta(days=1)

                schedule = doctor.schedules.get(weekday=visit_date.weekday())
                slots = schedule.get_slots()
                slot_index = (patient_index + item_index * 3) % len(slots)
                visit_time = slots[slot_index]
                while (doctor.id, visit_date, visit_time) in used_slots:
                    slot_index += 1
                    if slot_index >= len(slots):
                        visit_date += timedelta(days=1)
                        while visit_date.weekday() > 4:
                            visit_date += timedelta(days=1)
                        schedule = doctor.schedules.get(weekday=visit_date.weekday())
                        slots = schedule.get_slots()
                        slot_index = 0
                    visit_time = slots[slot_index]
                used_slots.add((doctor.id, visit_date, visit_time))

                if visit_date < today:
                    status = (
                        Appointment.STATUS_COMPLETED
                        if (patient_index + item_index) % 4
                        else Appointment.STATUS_CANCELED
                    )
                else:
                    status = [
                        Appointment.STATUS_PENDING,
                        Appointment.STATUS_APPROVED,
                        Appointment.STATUS_CANCELED,
                        Appointment.STATUS_REJECTED,
                    ][(patient_index + item_index) % 4]

                service = doctor.services.all()[
                    (patient_index + item_index) % doctor.services.count()
                ]
                appointment = Appointment.objects.create(
                    doctor=doctor,
                    service=service,
                    patient=patient,
                    patient_first_name=patient.first_name,
                    patient_last_name=patient.last_name,
                    patient_phone=patient.profile.phone,
                    patient_email='',
                    date=visit_date,
                    time=visit_time,
                    city=schedule.city,
                    address=schedule.address,
                    reason=PATIENT_REASONS[(patient_index + item_index) % len(PATIENT_REASONS)],
                    duration_slots=1,
                    status=status,
                    approved_at=(
                        timezone.now()
                        if status in [Appointment.STATUS_APPROVED, Appointment.STATUS_COMPLETED]
                        else None
                    ),
                )
                card, _ = DoctorPatientCard.objects.update_or_create(
                    doctor=doctor,
                    patient_phone=patient.profile.phone,
                    defaults={
                        'patient': patient,
                        'patient_first_name': patient.first_name,
                        'patient_last_name': patient.last_name,
                        'patient_email': '',
                        'notes': 'Демонстраційна картка пацієнта. Дані створені для перевірки сайту.',
                    },
                )
                if status == Appointment.STATUS_COMPLETED:
                    PatientRecordEntry.objects.create(
                        card=card,
                        doctor=doctor,
                        appointment=appointment,
                        kind=PatientRecordEntry.KIND_EXAMINATION,
                        title='Плановий огляд',
                        details=(
                            'Проведено огляд, стан ротової порожнини задовільний. '
                            'Запис створено як демонстраційний приклад.'
                        ),
                        recommendations='Підтримувати щоденну гігієну та пройти повторний огляд через 6 місяців.',
                    )
                created_count += 1
        return created_count

    def _create_public_content(self):
        colors = [(50, 104, 190), (23, 141, 128), (142, 90, 175)]
        for index, (title, text) in enumerate(CLINIC_NEWS, 1):
            post, _ = NewsPost.objects.update_or_create(
                doctor=None,
                title=title,
                defaults={'text': text, 'is_published': True},
            )
            save_demo_image(
                post,
                'image',
                f'demo_clinic_news_{index:02d}.png',
                f'NEWS {index}',
                colors[index - 1],
                size=(1200, 700),
                portrait=False,
            )

        for index in range(1, 7):
            gallery, _ = GalleryImage.objects.update_or_create(
                title=f'Демонстраційне фото клініки {index}',
                defaults={'is_published': True},
            )
            save_demo_image(
                gallery,
                'image',
                f'demo_gallery_{index:02d}.png',
                f'CLINIC {index}',
                colors[(index - 1) % len(colors)],
                size=(1200, 800),
                portrait=False,
            )
