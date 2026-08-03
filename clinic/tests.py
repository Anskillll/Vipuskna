from datetime import datetime, time, timedelta
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlparse

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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
    NewsPost,
    PatientRecordEntry,
    PatientRecordImage,
    PatientRecordVideo,
    Profile,
    TelegramConnection,
    TelegramLinkToken,
    TelegramNotification,
    WorkSchedule,
)
from .forms import (
    BookingReasonForm,
    ClinicSettingsForm,
    DoctorPatientBookingForm,
    DoctorProfileForm,
    DoctorWorkplaceForm,
    MultipleImageField,
    MultipleVideoField,
    PatientProfileForm,
    PatientRecordEntryForm,
    ServiceForm,
    UsernameLoginForm,
    WorkScheduleForm,
)
from .validators import (
    MAX_APPOINTMENT_DURATION_MINUTES,
    MAX_IMAGE_BYTES,
    MAX_LOGO_BYTES,
    MAX_REASON_LENGTH,
    MAX_SERVICE_PRICE_UAH,
    MAX_VIDEO_BYTES,
    validate_image_upload,
    validate_logo_upload,
    validate_ukrainian_phone,
    validate_video_upload,
)
from .views import (
    active_appointment_for_doctor,
    appointment_conflicts,
    next_working_date,
    patient_appointment_conflicts,
)
from .telegram import (
    create_link_url,
    notify_doctor_new_request,
    process_update,
    send_tomorrow_reminders,
)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class ClinicModelTests(TestCase):
    def setUp(self):
        self.patient = User.objects.create_user(
            username='patient@test.local',
            email='patient@test.local',
            password='pass12345',
            first_name='Тест',
            last_name='Пацієнт',
        )
        Profile.objects.create(
            user=self.patient,
            role=Profile.ROLE_PATIENT,
            phone='+380501111111',
            age=25,
        )
        SocialAccount.objects.create(
            user=self.patient,
            provider='google',
            uid='test-patient-google-uid',
        )
        doctor_user = User.objects.create_user(
            username='doctor@test.local',
            email='doctor@test.local',
            password='pass12345',
            first_name='Тест',
            last_name='Лікар',
        )
        Profile.objects.create(
            user=doctor_user,
            role=Profile.ROLE_DOCTOR,
            phone='+380502222222',
        )
        self.doctor = Doctor.objects.create(
            user=doctor_user,
            specialization='Терапевт',
            phone='+380502222222',
        )
        self.service = MedicalService.objects.create(
            doctor=self.doctor,
            name='Консультація',
            approximate_price=500,
        )
        self.workplace = DoctorWorkplace.objects.create(
            doctor=self.doctor,
            name='Тестова клініка',
            city='Дніпро',
            address='вул. Тестова, 1',
        )
        self.schedule = WorkSchedule.objects.create(
            doctor=self.doctor,
            workplace=self.workplace,
            weekday=timezone.localdate().weekday(),
            city='Дніпро',
            address='вул. Тестова, 1',
            start_time=time(9, 0),
            end_time=time(11, 0),
            slot_minutes=60,
        )

    def test_site_uses_semantic_color_accents(self):
        css_path = Path(settings.BASE_DIR) / 'static' / 'clinic' / 'site.css'
        css = css_path.read_text(encoding='utf-8')

        self.assertIn('--clinic-teal:', css)
        self.assertIn('--clinic-blue:', css)
        self.assertIn('--clinic-amber:', css)
        self.assertIn('--clinic-green:', css)
        self.assertIn('--clinic-red:', css)
        self.assertIn('.status.pending', css)
        self.assertIn('.status.approved', css)

    def test_home_page_shows_clinic_addresses_and_clickable_phone(self):
        response = self.client.get(reverse('home'))

        self.assertContains(response, 'clinic/site.css?v=20260802-6')
        self.assertContains(response, 'clinic/mobile.css?v=20260802-3')
        self.assertContains(response, 'Нікополь, вул. Шевченка, 200')
        self.assertContains(response, 'Дніпро, вул. Гусенка, 17')
        self.assertContains(response, 'https://www.google.com/maps/search/?api=1&amp;query=')
        self.assertContains(response, 'href="tel:+380509168426"')
        self.assertContains(response, '+38 (050) 916-84-26')

    def test_appointment_locations_open_in_google_maps(self):
        workplace_query = parse_qs(urlparse(self.workplace.google_maps_url).query)
        self.assertEqual(
            workplace_query['query'],
            ['Дніпро, вул. Тестова, 1'],
        )
        self.assertEqual(self.schedule.google_maps_url, self.workplace.google_maps_url)

        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=1),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка карти',
            status=Appointment.STATUS_APPROVED,
        )
        appointment_query = parse_qs(urlparse(appointment.google_maps_url).query)
        self.assertEqual(appointment_query['query'], ['Дніпро, вул. Тестова, 1'])

        self.client.login(username='patient@test.local', password='pass12345')
        dashboard = self.client.get(reverse('patient_dashboard'))
        detail = self.client.get(
            reverse('patient_appointment_detail', args=[appointment.pk])
        )
        escaped_url = appointment.google_maps_url.replace('&', '&amp;')
        self.assertIn(escaped_url, dashboard.content.decode())
        self.assertIn(escaped_url, detail.content.decode())

    def test_static_files_are_readable_by_web_server(self):
        from django.contrib.staticfiles.storage import staticfiles_storage

        self.assertEqual(staticfiles_storage.file_permissions_mode, 0o644)
        self.assertEqual(staticfiles_storage.directory_permissions_mode, 0o755)

    def test_home_gallery_uses_only_admin_uploaded_images(self):
        empty_response = self.client.get(reverse('home'))
        empty_content = empty_response.content.decode()
        empty_gallery = empty_content.split(
            '<section class="home-section home-gallery-section"',
            1,
        )[1].split('</section>', 1)[0]

        self.assertIn('Фотографій у галереї поки немає.', empty_gallery)
        self.assertNotIn('hero-treatment-room.webp', empty_gallery)
        self.assertNotIn('hero-reception.webp', empty_gallery)
        self.assertNotIn('hero-consultation.webp', empty_gallery)

        GalleryImage.objects.create(
            title='Власне фото',
            image='clinic/gallery/admin-uploaded.jpg',
            is_published=True,
        )
        response = self.client.get(reverse('home'))
        content = response.content.decode()
        gallery = content.split(
            '<section class="home-section home-gallery-section"',
            1,
        )[1].split('</section>', 1)[0]

        self.assertEqual(gallery.count('clinic/gallery/admin-uploaded.jpg'), 1)
        self.assertIn('data-home-gallery-slider', gallery)
        self.assertIn('data-home-gallery-slide', gallery)
        self.assertNotIn('hero-treatment-room.webp', gallery)

        GalleryImage.objects.create(
            title='Друге власне фото',
            image='clinic/gallery/admin-uploaded-second.jpg',
            is_published=True,
        )
        response = self.client.get(reverse('home'))
        gallery = response.content.decode().split(
            '<section class="home-section home-gallery-section"',
            1,
        )[1].split('</section>', 1)[0]

        self.assertIn('data-home-gallery-prev', gallery)
        self.assertIn('data-home-gallery-next', gallery)
        self.assertIn('data-home-gallery-dots', gallery)

        home_script = (Path(settings.BASE_DIR) / 'static' / 'clinic' / 'home.js').read_text(encoding='utf-8')
        self.assertIn('5000', home_script)

    def test_top_navigation_highlights_only_current_section(self):
        self.client.login(username='patient@test.local', password='pass12345')
        patient_sections = (
            ('doctors', 'doctors'),
            ('patient_dashboard', 'patient_dashboard'),
            ('booking', 'booking'),
        )
        for page_name, active_link_name in patient_sections:
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(page_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.content.count(b'class="nav-active" aria-current="page"'),
                    1,
                )
                self.assertContains(
                    response,
                    (
                        'class="nav-active" aria-current="page" '
                        f'href="{reverse(active_link_name)}"'
                    ),
                )

        self.client.logout()
        self.client.login(username='doctor@test.local', password='pass12345')
        doctor_response = self.client.get(reverse('doctor_appointments'))
        self.assertEqual(doctor_response.status_code, 200)
        self.assertEqual(
            doctor_response.content.count(b'class="nav-active" aria-current="page"'),
            1,
        )
        self.assertContains(
            doctor_response,
            (
                'class="nav-active" aria-current="page" '
                f'href="{reverse("doctor_dashboard")}"'
            ),
        )

        self.client.logout()
        User.objects.create_superuser(
            username='navigation-admin@test.local',
            password='pass12345',
            email='navigation-admin@test.local',
        )
        self.client.login(
            username='navigation-admin@test.local',
            password='pass12345',
        )
        admin_response = self.client.get(reverse('admin_content'))
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(
            admin_response.content.count(b'class="nav-active" aria-current="page"'),
            1,
        )
        self.assertContains(
            admin_response,
            (
                'class="nav-active" aria-current="page" '
                f'href="{reverse("admin_panel")}"'
            ),
        )

    def test_patient_dashboards_do_not_show_summary_statistics(self):
        self.client.login(username='patient@test.local', password='pass12345')
        patient_response = self.client.get(reverse('patient_dashboard'))

        self.assertEqual(patient_response.status_code, 200)
        self.assertNotContains(patient_response, 'patient-dashboard-stats')
        self.assertNotContains(patient_response, 'patient-dashboard-stat')

        self.client.logout()
        session = self.client.session
        session['patient_claim_phone'] = '+380501234577'
        session.save()
        pending_response = self.client.get(reverse('pending_patient_dashboard'))

        self.assertEqual(pending_response.status_code, 200)
        self.assertNotContains(pending_response, 'patient-dashboard-stats')
        self.assertNotContains(pending_response, 'patient-dashboard-stat')

    def create_other_doctor(self, slot_minutes=20):
        doctor_user = User.objects.create_user(
            username='other-doctor@test.local',
            password='pass12345',
            first_name='Інший',
            last_name='Лікар',
        )
        Profile.objects.create(
            user=doctor_user,
            role=Profile.ROLE_DOCTOR,
            phone='+380503333333',
        )
        doctor = Doctor.objects.create(
            user=doctor_user,
            specialization='Ортодонт',
            phone='+380503333333',
        )
        workplace = DoctorWorkplace.objects.create(
            doctor=doctor,
            name='Інша клініка',
            city='Київ',
            address='вул. Інша, 2',
        )
        WorkSchedule.objects.create(
            doctor=doctor,
            workplace=workplace,
            weekday=self.schedule.weekday,
            city='Київ',
            address='вул. Інша, 2',
            start_time=time(9, 0),
            end_time=time(11, 0),
            slot_minutes=slot_minutes,
        )
        service = MedicalService.objects.create(
            doctor=doctor,
            name='Огляд ортодонта',
        )
        return doctor, service

    def test_schedule_creates_hour_slots(self):
        slots = self.schedule.get_slots()

        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].strftime('%H:%M'), '09:00')
        self.assertEqual(slots[1].strftime('%H:%M'), '10:00')

    def test_success_messages_are_marked_for_automatic_dismissal(self):
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('logout'), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ви вийшли з акаунта.')
        self.assertContains(response, 'data-auto-dismiss')
        self.assertContains(response, 'clinic/flash_messages.js')

    def test_action_forms_use_toasts_without_browser_confirmation_dialogs(self):
        template_dir = Path(__file__).resolve().parent.parent / 'templates' / 'clinic'
        templates = list(template_dir.glob('*.html'))
        rendered_source = '\n'.join(path.read_text(encoding='utf-8') for path in templates)

        self.assertNotIn('confirm(', rendered_source)
        self.assertNotIn('onsubmit=', rendered_source)

    def test_back_links_are_only_on_secondary_pages(self):
        for page_name in ('home', 'doctors'):
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(page_name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'class="back-link"')
                self.assertNotContains(response, 'data-history-back')

        self.client.login(username='patient@test.local', password='pass12345')
        for page_name in ('patient_dashboard', 'booking'):
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(page_name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'class="back-link"')
                self.assertNotContains(response, 'data-history-back')

        edit_response = self.client.get(reverse('patient_edit_profile'))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(
            edit_response,
            (
                f'class="back-link" href="{reverse("patient_dashboard")}">'
                '← Повернутися до мого кабінету'
            ),
        )

        detail_response = self.client.get(
            reverse('doctor_detail', args=[self.doctor.id])
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(
            detail_response,
            (
                f'class="back-link" href="{reverse("doctors")}">'
                '← Повернутися до списку лікарів'
            ),
        )
        self.assertNotContains(detail_response, 'data-history-back')

        base_template = (
            Path(settings.BASE_DIR) / 'templates' / 'clinic' / 'base.html'
        ).read_text(encoding='utf-8')
        self.assertNotIn('navigation_history.js', base_template)
        self.assertNotIn('data-history-back', base_template)

    def test_site_and_admin_use_ukrainian_language(self):
        self.assertEqual(settings.LANGUAGE_CODE, 'uk')
        self.assertEqual(settings.LANGUAGES, [('uk', 'Українська')])
        self.assertEqual(admin.site.site_header, 'Адміністрування MedClinic')
        self.assertEqual(Doctor._meta.get_field('specialization').verbose_name, 'Спеціальність')
        self.assertEqual(Appointment._meta.get_field('patient_first_name').verbose_name, "Ім'я пацієнта")

        form = UsernameLoginForm(data={})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['username'][0], "Це поле обов'язкове.")

        self.patient.is_staff = True
        self.patient.is_superuser = True
        self.patient.save(update_fields=['is_staff', 'is_superuser'])
        self.client.login(username='patient@test.local', password='pass12345')
        response = self.client.get(reverse('admin:index'))

        self.assertContains(response, 'Адміністрування MedClinic')
        self.assertContains(response, 'Керування клінікою')
        self.assertNotContains(response, 'Администрирование')

    def test_doctor_cities_do_not_repeat(self):
        WorkSchedule.objects.create(
            doctor=self.doctor,
            weekday=(self.schedule.weekday + 1) % 7,
            city=self.schedule.city,
            address='Інша адреса',
            start_time=time(12, 0),
            end_time=time(16, 0),
            slot_minutes=60,
        )

        self.assertEqual(self.doctor.cities, ['Дніпро'])

    def test_seed_demo_creates_complete_repeatable_dataset(self):
        output = StringIO()

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            call_command(
                'seed_demo',
                doctors=2,
                patients=3,
                reset=True,
                stdout=output,
            )
            call_command(
                'seed_demo',
                doctors=2,
                patients=3,
                reset=False,
                stdout=output,
            )

            demo_doctors = Doctor.objects.filter(user__username__startswith='demo_doctor_')
            demo_patients = User.objects.filter(username__startswith='demo_patient_')

            self.service.refresh_from_db()
            self.assertEqual(demo_doctors.count(), 2)
            self.assertEqual(demo_patients.count(), 3)
            self.assertEqual(
                Appointment.objects.filter(patient__username__startswith='demo_patient_').count(),
                6,
            )
            self.assertTrue(self.service.description)
            for doctor in demo_doctors:
                self.assertEqual(doctor.schedules.count(), 5)
                self.assertEqual(doctor.workplaces.count(), 1)
                self.assertFalse(doctor.schedules.filter(workplace=None).exists())
                self.assertGreaterEqual(doctor.services.count(), 5)
                self.assertTrue(doctor.photo)
                self.assertTrue(doctor.services.exclude(description='').exists())
            for patient in demo_patients:
                self.assertTrue(patient.profile.photo)

    def test_clean_fake_data_keeps_selected_people_and_shared_data(self):
        admin_user = User.objects.create_superuser(
            username='admin@test.local',
            password='pass12345',
        )
        extra_patient = User.objects.create_user(
            username='extra-patient@test.local',
            first_name='Зайвий',
            last_name='Пацієнт',
        )
        Profile.objects.create(
            user=extra_patient,
            role=Profile.ROLE_PATIENT,
            phone='+380504444444',
        )
        other_doctor, _ = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        kept_appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=visit_date,
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Контроль',
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=extra_patient,
            patient_first_name=extra_patient.first_name,
            patient_last_name=extra_patient.last_name,
            patient_phone=extra_patient.profile.phone,
            date=visit_date,
            time=time(10, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Демонстраційний запис',
        )
        kept_card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
        )
        DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=extra_patient,
            patient_first_name=extra_patient.first_name,
            patient_last_name=extra_patient.last_name,
            patient_phone=extra_patient.profile.phone,
        )
        NewsPost.objects.create(doctor=other_doctor, title='Демо', text='Демо')
        GalleryImage.objects.create(title='Демо', image='clinic/gallery/demo.jpg')
        hero = HomeHeroSlide.objects.create(
            title='Справжній слайд',
            image='clinic/hero/real.jpg',
        )

        call_command(
            'clean_fake_data',
            admin_username=admin_user.username,
            doctor_username=self.doctor.user.username,
            patient_username=self.patient.username,
            apply=True,
            stdout=StringIO(),
        )

        self.assertSetEqual(
            set(User.objects.values_list('id', flat=True)),
            {admin_user.id, self.doctor.user_id, self.patient.id},
        )
        self.assertSetEqual(
            set(Appointment.objects.values_list('id', flat=True)),
            {kept_appointment.id},
        )
        self.assertSetEqual(
            set(DoctorPatientCard.objects.values_list('id', flat=True)),
            {kept_card.id},
        )
        self.assertTrue(MedicalService.objects.filter(pk=self.service.pk).exists())
        self.assertTrue(HomeHeroSlide.objects.filter(pk=hero.pk).exists())
        self.assertFalse(NewsPost.objects.exists())
        self.assertFalse(GalleryImage.objects.exists())
        self.assertEqual(AuditLog.objects.count(), 1)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_telegram_deep_link_connects_account_only_once(self):
        link = create_link_url(self.patient)
        token = parse_qs(urlparse(link).query)['start'][0]
        client = Mock()
        update = {
            'message': {
                'text': f'/start {token}',
                'chat': {'id': 10001, 'type': 'private'},
                'from': {'id': 10001, 'username': 'patient_tg', 'first_name': 'Пацієнт'},
            },
        }

        self.assertTrue(process_update(update, client=client))
        connection = TelegramConnection.objects.get(user=self.patient)
        self.assertEqual(connection.chat_id, 10001)
        self.assertEqual(connection.username, 'patient_tg')
        self.assertTrue(connection.is_active)
        self.assertIsNotNone(TelegramLinkToken.objects.get(token=token).used_at)

        self.assertFalse(process_update(update, client=client))
        self.assertEqual(TelegramConnection.objects.filter(user=self.patient).count(), 1)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_telegram_start_reports_existing_connection(self):
        TelegramConnection.objects.create(
            user=self.patient,
            chat_id=10002,
            username='patient_tg',
        )
        client = Mock()
        update = {
            'message': {
                'text': '/start',
                'chat': {'id': 10002, 'type': 'private'},
                'from': {'id': 10002, 'username': 'patient_tg'},
            },
        }

        self.assertTrue(process_update(update, client=client))
        message = client.send_message.call_args.args[1]
        self.assertIn('Ви вже зареєстровані', message)
        self.assertIn('сповіщення', message)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_patient_sees_telegram_connect_banner_and_gets_deep_link(self):
        self.client.login(username=self.patient.username, password='pass12345')

        dashboard = self.client.get(reverse('patient_dashboard'))
        connect = self.client.get(reverse('telegram_connect'))

        self.assertContains(dashboard, 'Приєднайте Telegram-бота')
        self.assertEqual(connect.status_code, 302)
        self.assertTrue(connect.url.startswith('https://t.me/myclinic_ua_bot?start='))

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_connected_patient_sees_status_and_can_start_telegram_reconnect(self):
        old_connection = TelegramConnection.objects.create(
            user=self.patient,
            chat_id=10003,
            username='old_patient_tg',
        )
        self.client.login(username=self.patient.username, password='pass12345')

        dashboard = self.client.get(reverse('patient_dashboard'))
        reconnect_get = self.client.get(reverse('telegram_reconnect'))
        reconnect = self.client.post(reverse('telegram_reconnect'))

        self.assertContains(dashboard, 'Бот прив’язаний')
        self.assertContains(dashboard, 'Переприв’язати бота')
        self.assertContains(dashboard, reverse('telegram_reconnect'))
        self.assertEqual(reconnect_get.status_code, 405)
        self.assertEqual(reconnect.status_code, 302)
        self.assertTrue(reconnect.url.startswith('https://t.me/myclinic_ua_bot?start='))
        self.assertFalse(TelegramConnection.objects.filter(pk=old_connection.pk).exists())
        self.assertEqual(
            TelegramLinkToken.objects.filter(user=self.patient, used_at__isnull=True).count(),
            1,
        )

    @override_settings(
        TELEGRAM_BOT_TOKEN='',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_failed_telegram_reconnect_keeps_old_connection(self):
        old_connection = TelegramConnection.objects.create(
            user=self.patient,
            chat_id=10004,
        )
        self.client.login(username=self.patient.username, password='pass12345')

        response = self.client.post(reverse('telegram_reconnect'))

        self.assertRedirects(response, reverse('patient_dashboard'))
        self.assertTrue(TelegramConnection.objects.filter(pk=old_connection.pk).exists())

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
        SITE_BASE_URL='http://testserver',
    )
    def test_new_patient_request_notifies_connected_doctor_once(self):
        TelegramConnection.objects.create(user=self.doctor.user, chat_id=20002)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=2),
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Консультація',
            status=Appointment.STATUS_PENDING,
        )

        with patch('clinic.telegram.TelegramBotClient.send_message') as send_message:
            self.assertTrue(notify_doctor_new_request(appointment))
            self.assertFalse(notify_doctor_new_request(appointment))

        self.assertEqual(send_message.call_count, 1)
        self.assertEqual(send_message.call_args.args[0], 20002)
        reply_markup = send_message.call_args.kwargs['reply_markup']
        action_buttons = reply_markup['inline_keyboard'][0]
        self.assertEqual(
            [button['text'] for button in action_buttons],
            ['✅ Прийняти', '❌ Відхилити'],
        )
        self.assertEqual(
            action_buttons[0]['callback_data'],
            f'doctor_request:approve:{appointment.pk}',
        )
        self.assertEqual(
            action_buttons[1]['callback_data'],
            f'doctor_request:reject:{appointment.pk}',
        )
        self.assertEqual(
            reply_markup['inline_keyboard'][1][0]['text'],
            'Деталі на сайті',
        )
        notification = TelegramNotification.objects.get()
        self.assertEqual(notification.recipient, self.doctor.user)
        self.assertEqual(notification.status, TelegramNotification.STATUS_SENT)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
        SITE_BASE_URL='http://testserver',
    )
    def test_doctor_can_approve_request_from_telegram_button(self):
        TelegramConnection.objects.create(user=self.doctor.user, chat_id=21002)
        TelegramConnection.objects.create(user=self.patient, chat_id=31003)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Консультація',
            status=Appointment.STATUS_PENDING,
        )
        client = Mock()
        update = {
            'callback_query': {
                'id': 'approve-callback',
                'data': f'doctor_request:approve:{appointment.pk}',
                'from': {'id': 21002},
                'message': {
                    'message_id': 91,
                    'chat': {'id': 21002, 'type': 'private'},
                },
            },
        }

        with patch('clinic.telegram.TelegramBotClient.send_message') as send_message:
            self.assertTrue(process_update(update, client=client))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)
        self.assertEqual(appointment.duration_minutes_exact, self.schedule.slot_minutes)
        self.assertIsNotNone(appointment.approved_at)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.doctor.user,
                action='Підтверджено заявку лікарем у Telegram',
                target_id=str(appointment.pk),
            ).exists()
        )
        client.answer_callback_query.assert_called_once_with(
            'approve-callback',
            'Заявку підтверджено.',
            show_alert=False,
        )
        edit_markup = client.edit_message_reply_markup.call_args.args[2]
        self.assertEqual(edit_markup['inline_keyboard'][0][0]['text'], 'Деталі на сайті')
        self.assertNotIn('callback_data', edit_markup['inline_keyboard'][0][0])
        self.assertEqual(send_message.call_args.args[0], 31003)

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
        SITE_BASE_URL='http://testserver',
    )
    def test_doctor_can_reject_request_from_telegram_button(self):
        TelegramConnection.objects.create(user=self.doctor.user, chat_id=22002)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Консультація',
            status=Appointment.STATUS_PENDING,
        )
        client = Mock()
        update = {
            'callback_query': {
                'id': 'reject-callback',
                'data': f'doctor_request:reject:{appointment.pk}',
                'from': {'id': 22002},
                'message': {
                    'message_id': 92,
                    'chat': {'id': 22002, 'type': 'private'},
                },
            },
        }

        self.assertTrue(process_update(update, client=client))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_REJECTED)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.doctor.user,
                action='Відхилено заявку лікарем у Telegram',
                target_id=str(appointment.pk),
            ).exists()
        )
        client.answer_callback_query.assert_called_once_with(
            'reject-callback',
            'Заявку відхилено.',
            show_alert=False,
        )
        client.edit_message_reply_markup.assert_called_once()

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    def test_telegram_button_cannot_change_another_doctors_request(self):
        TelegramConnection.objects.create(user=self.patient, chat_id=33003)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Консультація',
            status=Appointment.STATUS_PENDING,
        )
        client = Mock()
        update = {
            'callback_query': {
                'id': 'foreign-callback',
                'data': f'doctor_request:approve:{appointment.pk}',
                'from': {'id': 33003},
                'message': {
                    'message_id': 93,
                    'chat': {'id': 33003, 'type': 'private'},
                },
            },
        }

        self.assertFalse(process_update(update, client=client))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_PENDING)
        client.answer_callback_query.assert_called_once_with(
            'foreign-callback',
            'Ця заявка не належить вашому профілю лікаря.',
            show_alert=True,
        )
        client.edit_message_reply_markup.assert_not_called()

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    @patch('clinic.telegram.TelegramConnection.objects.filter')
    def test_telegram_button_answers_when_database_is_temporarily_unavailable(
        self,
        connection_filter,
    ):
        connection_filter.side_effect = DatabaseError('database unavailable')
        client = Mock()
        update = {
            'callback_query': {
                'id': 'database-error-callback',
                'data': 'doctor_request:approve:123',
                'from': {'id': 44004},
                'message': {
                    'message_id': 94,
                    'chat': {'id': 44004, 'type': 'private'},
                },
            },
        }

        self.assertFalse(process_update(update, client=client))

        client.answer_callback_query.assert_called_once_with(
            'database-error-callback',
            'База даних тимчасово недоступна. Спробуйте ще раз.',
            show_alert=True,
        )
        client.edit_message_reply_markup.assert_not_called()

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
        SITE_BASE_URL='http://testserver',
    )
    def test_day_before_reminder_is_sent_only_to_patient_after_18(self):
        TelegramConnection.objects.create(user=self.patient, chat_id=30003)
        TelegramConnection.objects.create(user=self.doctor.user, chat_id=40004)
        local_today = timezone.localdate()
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=local_today + timedelta(days=1),
            time=time(9, 0),
            city=self.schedule.city,
            address=self.schedule.address,
            reason='Консультація',
            status=Appointment.STATUS_APPROVED,
            approved_at=timezone.now(),
        )
        before_18 = timezone.make_aware(datetime.combine(local_today, time(17, 59)))
        after_18 = timezone.make_aware(datetime.combine(local_today, time(18, 1)))

        with patch('clinic.telegram.TelegramBotClient.send_message') as send_message:
            self.assertEqual(send_tomorrow_reminders(now=before_18), 0)
            self.assertEqual(send_tomorrow_reminders(now=after_18), 1)
            self.assertEqual(send_tomorrow_reminders(now=after_18), 0)

        self.assertEqual(send_message.call_count, 1)
        self.assertEqual(send_message.call_args.args[0], 30003)
        notification = TelegramNotification.objects.get(appointment=appointment)
        self.assertEqual(notification.recipient, self.patient)
        self.assertEqual(notification.kind, 'day_before_reminder')

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='myclinic_ua_bot',
    )
    @patch('clinic.management.commands.run_telegram_bot.time.sleep')
    @patch('clinic.management.commands.run_telegram_bot.TelegramBotClient')
    def test_telegram_bot_recovers_from_temporary_database_error(
        self,
        client_class,
        sleep,
    ):
        client = client_class.return_value
        client.get_me.return_value = {'username': 'myclinic_ua_bot'}
        client.get_updates.side_effect = [
            DatabaseError('database restarted'),
            KeyboardInterrupt(),
        ]

        call_command('run_telegram_bot')

        sleep.assert_called_once_with(5)

    def test_doctor_manages_workplaces_and_uses_preset_in_schedule(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        response = self.client.post(
            reverse('doctor_workplaces'),
            data={
                'name': 'Сімейна стоматологія',
                'city': 'Київ',
                'address': 'вул. Хрещатик, 10',
            },
        )
        workplace = DoctorWorkplace.objects.get(
            doctor=self.doctor,
            name='Сімейна стоматологія',
        )
        weekday = (self.schedule.weekday + 1) % 7

        self.assertRedirects(response, reverse('doctor_workplaces'))

        workplaces_response = self.client.get(reverse('doctor_workplaces'))
        self.assertContains(workplaces_response, 'placeholder="Місто або адреса"')
        self.assertContains(
            workplaces_response,
            'data-filter-search="Київ вул. Хрещатик, 10"',
        )
        self.assertNotContains(
            workplaces_response,
            'data-filter-search="Сімейна стоматологія',
        )

        response = self.client.post(
            reverse('doctor_schedule'),
            data={
                'weekday': weekday,
                'workplace': workplace.id,
                'start_time': '10:00',
                'end_time': '16:00',
                'slot_minutes': 30,
                'is_working': 'on',
            },
        )
        schedule = WorkSchedule.objects.get(doctor=self.doctor, weekday=weekday)

        self.assertRedirects(response, reverse('doctor_schedule'))
        self.assertEqual(schedule.workplace, workplace)
        self.assertEqual(schedule.city, 'Київ')
        self.assertEqual(schedule.address, 'вул. Хрещатик, 10')

        response = self.client.post(
            f"{reverse('doctor_workplaces')}?edit={workplace.id}",
            data={
                'name': 'Сімейна стоматологія',
                'city': 'Київ',
                'address': 'вул. Хрещатик, 12',
            },
        )
        schedule.refresh_from_db()

        self.assertRedirects(response, reverse('doctor_workplaces'))
        self.assertEqual(schedule.address, 'вул. Хрещатик, 12')

        dashboard_response = self.client.get(reverse('doctor_dashboard'))
        schedule_response = self.client.get(reverse('doctor_schedule'))
        self.assertContains(dashboard_response, 'Редагувати місця прийому')
        self.assertContains(dashboard_response, workplace.name)
        self.assertContains(schedule_response, 'Додати місце')
        self.assertContains(schedule_response, workplace.name)
        self.assertContains(schedule_response, 'name="workplace"')
        self.assertNotContains(schedule_response, 'name="city"')
        self.assertNotContains(schedule_response, 'name="address"')
        self.assertContains(schedule_response, 'manager-create-disclosure schedule-editor-disclosure')
        self.assertContains(schedule_response, 'Додати день')
        self.assertContains(schedule_response, 'schedule-edit-link')
        self.assertContains(schedule_response, 'schedule_editor.js?v=20260802-1')
        self.assertContains(schedule_response, 'live_filter.js?v=20260802-2')

        delete_response = self.client.post(
            reverse('doctor_workplaces'),
            data={
                'action': 'delete',
                'workplace_id': workplace.id,
            },
        )
        self.assertRedirects(delete_response, reverse('doctor_workplaces'))
        self.assertTrue(DoctorWorkplace.objects.filter(pk=workplace.id).exists())

    def test_required_schedule_choices_have_no_empty_placeholders(self):
        generic_workplace = DoctorWorkplace.objects.create(
            doctor=self.doctor,
            name='Місце прийому 2',
            city='Нікополь',
            address='вул. Центральна, 5',
        )

        form = WorkScheduleForm(doctor=self.doctor)
        weekday_choices = list(form.fields['weekday'].choices)
        workplace_labels = [str(label) for _, label in form.fields['workplace'].choices]

        self.assertNotEqual(weekday_choices[0][0], '')
        self.assertIsNone(form.fields['workplace'].empty_label)
        self.assertTrue(form.fields['workplace'].required)
        self.assertNotIn('Оберіть місце прийому', form.as_p())
        self.assertNotIn(generic_workplace.name, workplace_labels)
        self.assertIn('Нікополь, вул. Центральна, 5', workplace_labels)

        edit_form = DoctorWorkplaceForm(instance=generic_workplace)
        self.assertEqual(edit_form.initial['name'], 'Кабінет у м. Нікополь')

    def test_schedule_add_button_disappears_after_all_weekdays_are_configured(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        for weekday, _label in WorkSchedule.WEEKDAY_CHOICES:
            WorkSchedule.objects.update_or_create(
                doctor=self.doctor,
                weekday=weekday,
                defaults={
                    'workplace': self.workplace,
                    'city': self.workplace.city,
                    'address': self.workplace.address,
                    'start_time': time(9, 0),
                    'end_time': time(17, 0),
                    'slot_minutes': 20,
                },
            )

        response = self.client.get(reverse('doctor_schedule'))

        self.assertNotContains(response, 'manager-create-trigger')
        self.assertNotContains(response, 'Додати день до графіка')
        self.assertEqual(response.content.decode().count('class="schedule-edit-link"'), 7)

    def test_schedule_pencil_prefills_selected_weekday(self):
        self.schedule.break_start_time = time(9, 0)
        self.schedule.break_duration_minutes = 60
        self.schedule.save(update_fields=['break_start_time', 'break_duration_minutes'])
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(
            reverse('doctor_schedule'),
            {'edit': self.schedule.weekday},
        )
        form = response.context['form']

        self.assertEqual(response.context['editing_schedule'], self.schedule)
        self.assertEqual(form.instance, self.schedule)
        self.assertEqual(form['weekday'].value(), self.schedule.weekday)
        self.assertEqual(form['workplace'].value(), self.workplace.pk)
        self.assertEqual(form['start_time'].value(), self.schedule.start_time)
        self.assertEqual(form['end_time'].value(), self.schedule.end_time)
        self.assertEqual(form['slot_minutes'].value(), self.schedule.slot_minutes)
        self.assertEqual(form['break_slots'].value(), ['09:00'])
        self.assertContains(response, 'Змініть лише потрібні значення')

    def test_schedule_edit_replaces_day_and_builds_lunch_from_selected_slots(self):
        self.schedule.break_start_time = time(9, 0)
        self.schedule.break_duration_minutes = 60
        self.schedule.save(update_fields=['break_start_time', 'break_duration_minutes'])
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            f"{reverse('doctor_schedule')}?edit={self.schedule.weekday}",
            data={
                'weekday': self.schedule.weekday,
                'workplace': self.workplace.id,
                'start_time': '10:00',
                'end_time': '16:00',
                'slot_minutes': 20,
                'break_slots': ['13:00', '13:20', '13:40'],
                'is_working': 'on',
            },
        )
        self.schedule.refresh_from_db()

        self.assertRedirects(response, reverse('doctor_schedule'))
        self.assertEqual(self.schedule.start_time, time(10, 0))
        self.assertEqual(self.schedule.end_time, time(16, 0))
        self.assertEqual(self.schedule.slot_minutes, 20)
        self.assertEqual(self.schedule.break_start_time, time(13, 0))
        self.assertEqual(self.schedule.break_duration_minutes, 60)

    def test_schedule_rejects_lunch_slots_with_a_gap(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            f"{reverse('doctor_schedule')}?edit={self.schedule.weekday}",
            data={
                'weekday': self.schedule.weekday,
                'workplace': self.workplace.id,
                'start_time': '09:00',
                'end_time': '17:00',
                'slot_minutes': 20,
                'break_slots': ['13:00', '13:40'],
                'is_working': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'break_slots',
            'Слоти обіду мають іти послідовно, без проміжків.',
        )

    def test_service_editor_expands_inside_selected_service(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('doctor_services'), {'edit': self.service.id})

        self.assertContains(response, 'service-manager-item editing')
        self.assertContains(response, 'Зберегти зміни')
        self.assertContains(response, self.service.name)
        self.assertNotContains(response, 'grid grid-2')

    def test_doctor_can_change_service_order(self):
        second_service = MedicalService.objects.create(
            doctor=self.doctor,
            name='Друга послуга',
            approximate_price=900,
            sort_order=1,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_services'),
            data={
                'action': 'move_down',
                'service_id': self.service.id,
            },
        )

        ordered_ids = list(self.doctor.services.values_list('id', flat=True))
        self.assertRedirects(response, reverse('doctor_services'))
        self.assertEqual(ordered_ids, [second_service.id, self.service.id])

        dashboard_response = self.client.get(reverse('doctor_dashboard'))
        content = dashboard_response.content.decode()
        self.assertLess(content.index('Друга послуга'), content.index(self.service.name))

    def test_doctor_can_toggle_service_visibility_without_editing(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        page_response = self.client.get(reverse('doctor_services'))
        self.assertContains(page_response, 'Додати нову послугу')
        self.assertContains(page_response, 'manager-create-disclosure')
        self.assertContains(page_response, 'Приховати від пацієнтів')
        self.assertContains(page_response, 'value="toggle_patient_visibility"')

        hide_response = self.client.post(
            reverse('doctor_services'),
            data={
                'action': 'toggle_patient_visibility',
                'service_id': self.service.id,
            },
        )
        self.service.refresh_from_db()

        self.assertRedirects(hide_response, reverse('doctor_services'))
        self.assertFalse(self.service.is_patient_selectable)

        hidden_page_response = self.client.get(reverse('doctor_services'))
        self.assertContains(hidden_page_response, 'Показувати пацієнтам')

        show_response = self.client.post(
            reverse('doctor_services'),
            data={
                'action': 'toggle_patient_visibility',
                'service_id': self.service.id,
            },
        )
        self.service.refresh_from_db()

        self.assertRedirects(show_response, reverse('doctor_services'))
        self.assertTrue(self.service.is_patient_selectable)

    def test_new_service_is_added_to_end(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_services'),
            data={
                'name': 'Остання послуга',
            },
        )

        created_service = MedicalService.objects.get(name='Остання послуга')
        self.assertRedirects(response, reverse('doctor_services'))
        self.assertGreater(created_service.sort_order, self.service.sort_order)
        self.assertIsNone(created_service.approximate_price)

    def test_patient_can_select_only_services_enabled_by_doctor(self):
        hidden_service = MedicalService.objects.create(
            doctor=self.doctor,
            name='Службова процедура',
            approximate_price=300,
            sort_order=1,
            is_patient_selectable=False,
        )

        patient_form = BookingReasonForm(doctor=self.doctor)
        doctor_form = DoctorPatientBookingForm(doctor=self.doctor)
        patient_service_ids = list(
            patient_form.fields['service'].queryset.values_list('id', flat=True)
        )
        doctor_service_ids = list(
            doctor_form.fields['service'].queryset.values_list('id', flat=True)
        )
        patient_service_labels = [
            label for _, label in patient_form.fields['service'].choices
        ]
        doctor_service_labels = [
            label for _, label in doctor_form.fields['service'].choices
        ]

        self.assertIn(self.service.id, patient_service_ids)
        self.assertNotIn(hidden_service.id, patient_service_ids)
        self.assertIn(hidden_service.id, doctor_service_ids)
        self.assertIsNone(patient_form.fields['service'].empty_label)
        self.assertIsNone(doctor_form.fields['service'].empty_label)
        self.assertEqual(patient_service_labels, [self.service.name])
        self.assertEqual(
            doctor_service_labels,
            [self.service.name, hidden_service.name],
        )
        self.assertNotIn('грн', ' '.join(doctor_service_labels))

        forged_form = BookingReasonForm(
            data={
                'service': hidden_service.id,
                'reason': 'Спроба підставити приховану послугу',
            },
            doctor=self.doctor,
        )
        self.assertFalse(forged_form.is_valid())
        self.assertIn('service', forged_form.errors)

    def test_service_form_shows_patient_booking_checkbox(self):
        form = ServiceForm(instance=self.service)

        self.assertIn('is_patient_selectable', form.fields)
        self.assertIn('description', form.fields)
        self.assertIn('photos', form.fields)
        self.assertFalse(form.fields['approximate_price'].required)
        self.assertEqual(
            form.fields['is_patient_selectable'].label,
            'Дозволити пацієнтам обирати цю послугу під час запису',
        )
        self.assertEqual(
            form.fields['approximate_price'].widget.attrs['max'],
            MAX_SERVICE_PRICE_UAH,
        )

    def test_service_price_cannot_exceed_one_million(self):
        valid_form = ServiceForm(
            data={
                'name': 'Послуга на верхній межі',
                'approximate_price': MAX_SERVICE_PRICE_UAH,
                'description': 'Коректний опис.',
                'is_patient_selectable': 'on',
            },
            instance=self.service,
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)

        invalid_form = ServiceForm(
            data={
                'name': 'Занадто дорога послуга',
                'approximate_price': MAX_SERVICE_PRICE_UAH + 1,
                'description': 'Коректний опис.',
                'is_patient_selectable': 'on',
            },
            instance=self.service,
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('approximate_price', invalid_form.errors)

        self.service.approximate_price = MAX_SERVICE_PRICE_UAH + 1
        with self.assertRaises(ValidationError):
            self.service.full_clean()

    def test_doctor_can_add_service_description_and_photo(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        image = SimpleUploadedFile(
            'service.gif',
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;',
            content_type='image/gif',
        )

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                f"{reverse('doctor_services')}?edit={self.service.id}",
                data={
                    'name': self.service.name,
                    'approximate_price': self.service.approximate_price,
                    'description': 'Розгорнутий опис процедури та підготовки до неї.',
                    'is_patient_selectable': 'on',
                    'photos': [image],
                },
            )

            self.service.refresh_from_db()
            service_image = MedicalServiceImage.objects.get(service=self.service)
            detail_response = self.client.get(
                reverse('service_detail', args=[self.doctor.id, self.service.id])
            )

            self.assertRedirects(response, reverse('doctor_services'))
            self.assertEqual(
                self.service.description,
                'Розгорнутий опис процедури та підготовки до неї.',
            )
            self.assertTrue(service_image.image.name.startswith('service_photos/'))
            self.assertContains(detail_response, self.service.description)
            self.assertContains(detail_response, service_image.image.url)
            self.assertContains(detail_response, 'Головне про процедуру')
            self.assertNotContains(detail_response, 'Лікар, який надає послугу')
            self.assertNotContains(detail_response, self.doctor.full_name)

    def test_doctor_cannot_delete_another_doctors_service_photo(self):
        other_user = User.objects.create_user(
            username='other-doctor@test.local',
            password='pass12345',
        )
        Profile.objects.create(user=other_user, role=Profile.ROLE_DOCTOR)
        other_doctor = Doctor.objects.create(
            user=other_user,
            specialization='Хірург',
            phone='+380503333333',
        )
        other_service = MedicalService.objects.create(
            doctor=other_doctor,
            name='Інша послуга',
            approximate_price=800,
        )
        other_image = MedicalServiceImage.objects.create(
            service=other_service,
            image='service_photos/other.jpg',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_services'),
            data={
                'action': 'delete_image',
                'image_id': other_image.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(MedicalServiceImage.objects.filter(pk=other_image.id).exists())

    def test_doctor_cards_are_compact_and_detail_page_is_complete(self):
        self.doctor.description = 'Повна інформація про досвід лікаря.'
        self.doctor.save(update_fields=['description'])

        list_response = self.client.get(reverse('doctors'))
        detail_response = self.client.get(reverse('doctor_detail', args=[self.doctor.id]))

        self.assertContains(list_response, 'Детальніше')
        self.assertContains(
            list_response,
            reverse('doctor_detail', args=[self.doctor.id]),
            count=1,
        )
        self.assertNotContains(list_response, 'Переглянути профіль')
        self.assertNotContains(list_response, self.doctor.phone)
        self.assertNotContains(list_response, self.service.name)

        self.assertContains(detail_response, self.doctor.full_name)
        self.assertContains(detail_response, self.doctor.phone)
        self.assertContains(detail_response, self.doctor.specialization)
        self.assertContains(detail_response, self.doctor.description)
        self.assertContains(detail_response, self.service.name)
        self.assertContains(detail_response, 'Графік роботи')

    def test_booking_doctor_summary_is_compact(self):
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('booking'), {'doctor': self.doctor.id})

        self.assertContains(response, 'data-booking-auto-submit')
        self.assertContains(response, 'filterForm.requestSubmit()')
        self.assertNotContains(response, 'Показати час')
        self.assertContains(response, self.doctor.full_name)
        self.assertContains(response, self.doctor.specialization)
        self.assertContains(response, 'Детальніше')
        self.assertNotContains(response, self.doctor.phone)
        self.assertNotContains(response, self.service.name)

    def test_booking_service_selector_has_details_link(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)

        response = self.client.get(
            reverse('booking'),
            {
                'doctor': self.doctor.id,
                'date': future_date.strftime('%Y-%m-%d'),
                'time': '09:00',
            },
        )

        expected_base = f"{reverse('doctor_detail', args=[self.doctor.id])}services/"
        self.assertContains(response, 'Детальніше про послугу')
        self.assertContains(response, f'data-service-url="{expected_base}"')
        self.assertContains(response, 'data-service-details')
        self.assertContains(
            response,
            f'data-booking-date="{future_date:%Y-%m-%d}"',
        )
        self.assertContains(response, 'data-booking-time="09:00"')
        content = response.content.decode()
        details_attribute = content.index('data-service-details')
        details_tag = content[
            content.rfind('<a', 0, details_attribute):content.index('>', details_attribute)
        ]
        self.assertNotIn('target="_blank"', details_tag)

    def test_service_lists_have_live_filters(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)

        booking_response = self.client.get(
            reverse('booking'),
            {
                'doctor': self.doctor.id,
                'date': future_date.isoformat(),
                'time': '09:00',
            },
        )
        doctor_detail_response = self.client.get(
            reverse('doctor_detail', args=[self.doctor.id]),
        )

        self.assertContains(booking_response, 'id="booking-service-search"')
        self.assertContains(booking_response, 'data-live-filter-item')
        self.assertContains(booking_response, 'data-live-filter-clear')
        self.assertContains(doctor_detail_response, 'id="patient-service-search"')
        self.assertContains(doctor_detail_response, 'data-live-filter-item')

    def test_patient_can_book_another_person_without_mixing_cards(self):
        self.client.login(username='patient@test.local', password='pass12345')
        own_visit_date = timezone.localdate() + timedelta(days=7)
        other_visit_date = timezone.localdate() + timedelta(days=14)

        own_response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': own_visit_date.isoformat(),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Мій прийом',
            },
        )
        other_response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': other_visit_date.isoformat(),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Прийом для іншої людини',
                'booked_for_other': 'on',
                'other_first_name': 'Марія',
                'other_last_name': 'Пацієнт',
            },
        )

        self.assertEqual(own_response.status_code, 302)
        self.assertEqual(other_response.status_code, 302)
        other_appointment = Appointment.objects.get(
            reason='Прийом для іншої людини',
        )
        self.assertEqual(other_appointment.patient, self.patient)
        self.assertTrue(other_appointment.booked_for_other)
        self.assertEqual(other_appointment.patient_name, 'Марія Пацієнт')
        self.assertEqual(
            other_appointment.booking_owner_name,
            self.patient.get_full_name(),
        )

        owner_card = DoctorPatientCard.objects.get(patient=self.patient)
        visitor_card = DoctorPatientCard.objects.get(
            patient__isnull=True,
            patient_first_name='Марія',
            patient_last_name='Пацієнт',
        )
        self.assertNotEqual(owner_card.id, visitor_card.id)
        self.assertEqual(owner_card.patient_phone, visitor_card.patient_phone)

        self.client.login(username='doctor@test.local', password='pass12345')
        requests_response = self.client.get(reverse('doctor_requests'))
        detail_response = self.client.get(
            reverse(
                'doctor_appointment_detail',
                args=[other_appointment.id],
            ),
        )
        self.assertContains(
            requests_response,
            f'Від {self.patient.get_full_name()}',
        )
        self.assertContains(detail_response, 'На прийом прийде')
        self.assertContains(detail_response, 'Заявку створив')
        self.assertContains(detail_response, 'Марія Пацієнт')

    def test_booking_for_other_requires_first_and_last_name(self):
        self.client.login(username='patient@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': visit_date.isoformat(),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Неповні дані',
                'booked_for_other': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Appointment.objects.filter(reason='Неповні дані').exists(),
        )
        self.assertContains(
            response,
            'Вкажіть ім’я та прізвище людини, яка прийде на прийом.',
            count=2,
        )

    def test_service_details_returns_to_the_same_booking(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        detail_response = self.client.get(
            reverse(
                'service_detail',
                args=[self.doctor.id, self.service.id],
            ),
            {
                'from': 'booking',
                'date': future_date.strftime('%Y-%m-%d'),
                'time': '09:00',
            },
        )
        expected_return_url = f"{reverse('booking')}?{urlencode({
            'doctor': self.doctor.id,
            'service': self.service.id,
            'date': future_date.strftime('%Y-%m-%d'),
            'time': '09:00',
        })}"

        self.assertEqual(
            detail_response.context['booking_return_url'],
            expected_return_url,
        )
        self.assertContains(detail_response, 'Повернутися до заявки', count=2)

        booking_response = self.client.get(expected_return_url)

        self.assertEqual(booking_response.status_code, 200)
        self.assertEqual(
            str(booking_response.context['reason_form']['service'].value()),
            str(self.service.id),
        )

    def test_active_appointment_slot_is_unique(self):
        visit_date = timezone.localdate()

        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=visit_date,
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка',
        )

        with self.assertRaises(IntegrityError):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient=self.patient,
                patient_first_name='Інший',
                patient_last_name='Пацієнт',
                patient_phone='+380503333333',
                patient_email='other@test.local',
                date=visit_date,
                time='09:00',
                city='Дніпро',
                address='вул. Тестова, 1',
                reason='Повтор',
            )

    def test_booking_checks_every_requested_slot(self):
        visit_date = timezone.localdate()
        self.schedule.slot_minutes = 20
        self.schedule.save(update_fields=['slot_minutes'])
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=visit_date,
            time='09:40',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Зайнятий третій слот',
        )

        self.assertFalse(appointment_conflicts(self.doctor, visit_date, time(9, 0), duration_slots=2))
        self.assertTrue(appointment_conflicts(self.doctor, visit_date, time(9, 0), duration_slots=3))

    def test_patient_cannot_book_same_time_with_another_doctor(self):
        other_doctor, other_service = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перший прийом',
            duration_minutes_exact=60,
            status=Appointment.STATUS_APPROVED,
        )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': other_doctor.id,
                'date': visit_date.isoformat(),
                'time': '09:00',
                'service': other_service.id,
                'reason': 'Другий прийом на той самий час',
            },
            follow=True,
        )

        self.assertFalse(
            Appointment.objects.filter(
                doctor=other_doctor,
                patient=self.patient,
                date=visit_date,
            ).exists()
        )
        self.assertContains(
            response,
            'У цей час у вас уже є інша заявка або прийом. Оберіть вільний час.',
        )

    def test_patient_cannot_create_third_active_booking_for_same_day(self):
        other_doctor, other_service = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        for index, appointment_time in enumerate((time(9, 0), time(9, 20))):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient=self.patient,
                patient_first_name='Тест',
                patient_last_name='Пацієнт',
                patient_phone='+380501111111',
                date=visit_date,
                time=appointment_time,
                city='Дніпро',
                address='вул. Тестова, 1',
                reason=f'Активний запис {index + 1}',
                duration_minutes_exact=20,
                status=(
                    Appointment.STATUS_PENDING
                    if index == 0
                    else Appointment.STATUS_APPROVED
                ),
            )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': other_doctor.id,
                'date': visit_date.isoformat(),
                'time': '10:00',
                'service': other_service.id,
                'reason': 'Третя заявка цього дня',
            },
            follow=True,
        )

        self.assertFalse(
            Appointment.objects.filter(reason='Третя заявка цього дня').exists()
        )
        self.assertContains(
            response,
            'Самостійно можна створити не більше 2 заявок або прийомів на один день.',
        )
        self.assertContains(response, 'Ліміт на цей день вичерпано')
        self.assertNotContains(response, 'Надіслати заявку')

    def test_canceled_and_rejected_bookings_do_not_use_daily_limit(self):
        other_doctor, other_service = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        for index, status in enumerate(
            (Appointment.STATUS_CANCELED, Appointment.STATUS_REJECTED)
        ):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient=self.patient,
                patient_first_name='Тест',
                patient_last_name='Пацієнт',
                patient_phone='+380501111111',
                date=visit_date,
                time=time(9, index * 20),
                city='Дніпро',
                address='вул. Тестова, 1',
                reason=f'Неактивний запис {index + 1}',
                duration_minutes_exact=20,
                status=status,
            )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': other_doctor.id,
                'date': visit_date.isoformat(),
                'time': '10:00',
                'service': other_service.id,
                'reason': 'Дозволена нова заявка',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Appointment.objects.filter(
                patient=self.patient,
                reason='Дозволена нова заявка',
                status=Appointment.STATUS_PENDING,
            ).exists()
        )

    def test_doctor_can_add_third_non_overlapping_booking_for_patient(self):
        other_doctor, other_service = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        for index, appointment_time in enumerate((time(9, 0), time(9, 20))):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient=self.patient,
                patient_first_name='Тест',
                patient_last_name='Пацієнт',
                patient_phone='+380501111111',
                date=visit_date,
                time=appointment_time,
                city='Дніпро',
                address='вул. Тестова, 1',
                reason=f'Попередній запис {index + 1}',
                duration_minutes_exact=20,
                status=Appointment.STATUS_APPROVED,
            )
        self.client.login(
            username='other-doctor@test.local',
            password='pass12345',
        )

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.isoformat(),
                'time': '10:00',
                'patient': self.patient.id,
                'service': other_service.id,
                'duration_minutes': 20,
                'reason': 'Третій запис від лікаря',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Appointment.objects.filter(
                doctor=other_doctor,
                patient=self.patient,
                reason='Третій запис від лікаря',
                status=Appointment.STATUS_APPROVED,
            ).exists()
        )

    def test_patient_conflict_uses_full_appointment_duration(self):
        visit_date = timezone.localdate() + timedelta(days=7)
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Тривалий прийом',
            duration_minutes_exact=60,
            status=Appointment.STATUS_APPROVED,
        )

        self.assertTrue(
            patient_appointment_conflicts(
                self.patient,
                visit_date,
                time(9, 40),
                20,
            )
        )
        self.assertFalse(
            patient_appointment_conflicts(
                self.patient,
                visit_date,
                time(10, 0),
                20,
            )
        )

    def test_doctor_cannot_book_registered_patient_during_another_appointment(self):
        other_doctor, other_service = self.create_other_doctor()
        visit_date = timezone.localdate() + timedelta(days=7)
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Запис до першого лікаря',
            duration_minutes_exact=60,
            status=Appointment.STATUS_APPROVED,
        )
        self.client.login(username='other-doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.isoformat(),
                'time': '09:00',
                'patient': self.patient.id,
                'service': other_service.id,
                'duration_minutes': 20,
                'reason': 'Спроба запису до другого лікаря',
            },
        )

        self.assertFalse(
            Appointment.objects.filter(
                doctor=other_doctor,
                patient=self.patient,
                date=visit_date,
            ).exists()
        )
        self.assertContains(
            response,
            'У цей час пацієнт уже має іншу заявку або прийом.',
        )

    def test_patient_cannot_book_past_date(self):
        self.client.login(username='patient@test.local', password='pass12345')
        yesterday = timezone.localdate() - timedelta(days=1)

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': yesterday.strftime('%Y-%m-%d'),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Спроба запису на вчора',
            },
            follow=True,
        )

        self.assertEqual(Appointment.objects.count(), 0)
        self.assertContains(response, 'Записатися можна лише починаючи із завтрашнього дня.')

    def test_patient_cannot_book_today(self):
        self.client.login(username='patient@test.local', password='pass12345')
        today = timezone.localdate()

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': today.strftime('%Y-%m-%d'),
                'time': '10:00',
                'service': self.service.id,
                'reason': 'Спроба запису на сьогодні',
            },
            follow=True,
        )

        self.assertEqual(Appointment.objects.count(), 0)
        self.assertContains(response, 'Записатися можна лише починаючи із завтрашнього дня.')

    def test_booking_calendar_starts_from_tomorrow(self):
        self.client.login(username='patient@test.local', password='pass12345')
        tomorrow = timezone.localdate() + timedelta(days=1)
        first_working_date = next_working_date(
            tomorrow,
            [self.schedule.weekday],
        )

        response = self.client.get(reverse('booking'), {'date': timezone.localdate().isoformat()})

        self.assertEqual(response.context['selected_date'], first_working_date)
        self.assertContains(response, f'min="{tomorrow.isoformat()}"')
        self.assertContains(response, f'value="{first_working_date.isoformat()}"')
        self.assertContains(response, 'data-working-date-picker')
        self.assertContains(
            response,
            f'data-working-weekdays="{self.schedule.weekday}"',
        )
        self.assertContains(response, 'clinic/working_date_picker.js?v=20260802-1')

    def test_patient_cannot_book_doctor_day_off(self):
        self.client.login(username='patient@test.local', password='pass12345')
        off_weekday = (self.schedule.weekday + 1) % 7
        off_date = timezone.localdate() + timedelta(days=1)
        while off_date.weekday() != off_weekday:
            off_date += timedelta(days=1)
        WorkSchedule.objects.create(
            doctor=self.doctor,
            workplace=self.workplace,
            weekday=off_weekday,
            city='Дніпро',
            address='вул. Тестова, 1',
            start_time=time(9, 0),
            end_time=time(11, 0),
            slot_minutes=60,
            is_working=False,
        )

        response = self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': off_date.isoformat(),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Спроба запису у вихідний',
            },
            follow=True,
        )

        self.assertFalse(
            Appointment.objects.filter(reason='Спроба запису у вихідний').exists()
        )
        self.assertContains(
            response,
            'У цей день лікар не приймає. Оберіть робочий день у календарі.',
        )

    def test_doctor_booking_calendar_disables_days_off(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        off_weekday = (self.schedule.weekday + 1) % 7
        off_date = timezone.localdate() + timedelta(days=1)
        while off_date.weekday() != off_weekday:
            off_date += timedelta(days=1)
        WorkSchedule.objects.create(
            doctor=self.doctor,
            workplace=self.workplace,
            weekday=off_weekday,
            city='Дніпро',
            address='вул. Тестова, 1',
            start_time=time(9, 0),
            end_time=time(11, 0),
            slot_minutes=60,
            is_working=False,
        )

        calendar_response = self.client.get(reverse('doctor_book_patient'))
        self.assertContains(calendar_response, 'data-working-date-picker')
        self.assertContains(
            calendar_response,
            f'data-working-weekdays="{self.schedule.weekday}"',
        )

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': off_date.isoformat(),
                'time': '09:00',
                'patient': self.patient.id,
                'service': self.service.id,
                'duration_minutes': 60,
                'reason': 'Спроба лікаря записати у вихідний',
            },
            follow=True,
        )

        self.assertFalse(
            Appointment.objects.filter(
                reason='Спроба лікаря записати у вихідний'
            ).exists()
        )
        self.assertContains(
            response,
            'У цей день ви не приймаєте. Оберіть робочий день у календарі.',
        )

    def test_patient_does_not_choose_appointment_duration(self):
        self.assertNotIn('duration_slots', BookingReasonForm(doctor=self.doctor).fields)
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)

        self.client.post(
            reverse('booking'),
            data={
                'doctor': self.doctor.id,
                'date': future_date.strftime('%Y-%m-%d'),
                'time': '09:00',
                'service': self.service.id,
                'reason': 'Заявка без вибору тривалості',
                'duration_slots': 3,
            },
        )

        appointment = Appointment.objects.get(reason='Заявка без вибору тривалості')
        self.assertEqual(appointment.duration_slots, 1)

    def test_patient_can_add_photos_to_appointment_request(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        image = SimpleUploadedFile(
            'request.gif',
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;',
            content_type='image/gif',
        )

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse('booking'),
                data={
                    'doctor': self.doctor.id,
                    'date': future_date.strftime('%Y-%m-%d'),
                    'time': '09:00',
                    'service': self.service.id,
                    'reason': 'Заявка з фотографією',
                    'photos': [image],
                },
            )

            appointment = Appointment.objects.get(reason='Заявка з фотографією')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(AppointmentImage.objects.filter(appointment=appointment).count(), 1)

    def test_doctor_can_open_own_appointment_details(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=timezone.localdate() + timedelta(days=7),
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Детальний опис заявки',
        )

        response = self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Детальний опис заявки')
        self.assertContains(response, 'Фото та відео до заявки')

    def test_doctor_requests_page_shows_only_own_pending_requests(self):
        request_date = timezone.localdate() + timedelta(days=7)
        own_request = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=request_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Власна нова заявка',
            status=Appointment.STATUS_PENDING,
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Підтверджений',
            patient_last_name='Пацієнт',
            patient_phone='+380509999991',
            date=request_date,
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Вже підтверджений прийом',
            status=Appointment.STATUS_APPROVED,
        )
        other_doctor, other_service = self.create_other_doctor()
        Appointment.objects.create(
            doctor=other_doctor,
            service=other_service,
            patient_first_name='Чужий',
            patient_last_name='Пацієнт',
            patient_phone='+380509999992',
            date=request_date,
            time=time(9, 0),
            city='Київ',
            address='вул. Інша, 2',
            reason='Чужа нова заявка',
            status=Appointment.STATUS_PENDING,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('doctor_requests'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [appointment.id for appointment in response.context['appointments']],
            [own_request.id],
        )
        self.assertContains(response, 'Мої заявки')
        self.assertContains(response, 'Власна нова заявка')
        self.assertContains(response, 'Нова заявка')
        self.assertContains(response, reverse('doctor_appointment_detail', args=[own_request.id]))
        self.assertNotContains(response, 'Вже підтверджений прийом')
        self.assertNotContains(response, 'Чужа нова заявка')

    def test_doctor_appointments_are_grouped_and_show_only_summary(self):
        today = timezone.localdate()
        first_date = today - timedelta(days=today.weekday()) + timedelta(days=9)
        second_date = first_date + timedelta(days=1)
        later = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Пізній',
            patient_last_name='Пацієнт',
            patient_phone='+380509999991',
            date=first_date,
            time=time(10, 0),
            city='Приховане місто',
            address='Прихована адреса',
            reason='Прихована причина',
            status=Appointment.STATUS_APPROVED,
        )
        earlier = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Ранній',
            patient_last_name='Пацієнт',
            patient_phone='+380509999992',
            date=first_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Другий прихований опис',
        )
        next_day = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Наступний',
            patient_last_name='Пацієнт',
            patient_phone='+380509999993',
            date=second_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Третій прихований опис',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(
            reverse('doctor_appointments'),
            {'week': first_date.isoformat()},
        )
        rendered_appointments = list(response.context['appointments'])
        appointment_ids = [item.id for item in rendered_appointments]
        weekday_names = ('Понеділок', 'Вівторок', 'Середа', 'Четвер', "П'ятниця", 'Субота', 'Неділя')

        self.assertEqual(appointment_ids, [earlier.id, later.id, next_day.id])
        self.assertEqual(rendered_appointments[0].weekday_name, weekday_names[first_date.weekday()])
        self.assertEqual(rendered_appointments[-1].weekday_name, weekday_names[second_date.weekday()])
        self.assertContains(response, first_date.strftime('%d.%m.%Y'))
        self.assertContains(response, second_date.strftime('%d.%m.%Y'))
        self.assertContains(response, 'Відкрити картку', count=3)
        self.assertContains(response, 'Заявка очікує')
        self.assertContains(response, 'Підтверджений прийом')
        self.assertContains(response, 'appointment-kind-pending')
        self.assertContains(response, 'appointment-kind-approved')
        self.assertNotContains(response, 'Прихована причина')
        self.assertContains(response, 'data-live-filter-input')
        self.assertContains(response, 'data-live-filter-item')
        self.assertContains(response, '+380509999991')

    def test_doctor_appointments_show_working_days_and_collapse_after_five_items(self):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        busy_date = week_start + timedelta(days=self.schedule.weekday)
        for index in range(6):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient_first_name=f'Пацієнт {index + 1}',
                patient_last_name='Тестовий',
                patient_phone=f'+3805099998{index:02d}',
                date=busy_date,
                time=time(9 + index, 0),
                city='Дніпро',
                address='вул. Тестова, 1',
                reason='Перевірка тижневого розкладу',
            )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(
            reverse('doctor_appointments'),
            {'week': busy_date.isoformat()},
        )
        target_week = response.context['appointment_week']

        self.assertEqual(len(target_week['days']), 1)
        self.assertEqual(
            target_week['days'][0]['weekday_name'],
            (
                'Понеділок',
                'Вівторок',
                'Середа',
                'Четвер',
                "П'ятниця",
                'Субота',
                'Неділя',
            )[self.schedule.weekday],
        )
        self.assertEqual(len(target_week['days'][0]['appointments']), 6)
        self.assertContains(
            response,
            f"Тиждень з {week_start.strftime('%d.%m')} по "
            f"{(week_start + timedelta(days=6)).strftime('%d.%m')}",
        )
        self.assertNotContains(response, 'У цей день ви відпочиваєте')
        self.assertContains(response, 'Розгорнути всі (6)')
        self.assertContains(response, 'data-day-extra', count=1)
        self.assertContains(
            response,
            f'?week={(week_start - timedelta(days=7)).isoformat()}',
        )
        self.assertContains(
            response,
            f'?week={(week_start + timedelta(days=7)).isoformat()}',
        )

        current_response = self.client.get(reverse('doctor_appointments'))
        current_week_start = today - timedelta(days=today.weekday())
        self.assertEqual(
            current_response.context['appointment_week']['start'],
            current_week_start,
        )
        self.assertTrue(current_response.context['is_current_week'])
        self.assertContains(
            current_response,
            'appointment-week-kicker is-current',
        )
        self.assertContains(current_response, 'Поточний тиждень')

    def test_doctor_week_has_day_booking_and_between_appointments_actions(self):
        visit_date = timezone.localdate() + timedelta(days=7)
        self.schedule.slot_minutes = 20
        self.schedule.save(update_fields=['slot_minutes'])
        first_appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Перший',
            patient_last_name='Пацієнт',
            patient_phone='+380501010101',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перший сусідній прийом',
            duration_minutes_exact=20,
            status=Appointment.STATUS_APPROVED,
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Другий',
            patient_last_name='Пацієнт',
            patient_phone='+380502020202',
            date=visit_date,
            time=time(9, 20),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Другий сусідній прийом',
            duration_minutes_exact=20,
            status=Appointment.STATUS_APPROVED,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(
            reverse('doctor_appointments'),
            {'week': visit_date.isoformat()},
        )

        self.assertContains(response, 'Записати на цей день')
        self.assertContains(response, 'Записати між ними')
        self.assertContains(response, '>09:10</span>', html=False)
        self.assertContains(
            response,
            (
                f"{reverse('doctor_book_patient')}?date={visit_date.isoformat()}"
                f"&split={first_appointment.id}"
            ),
        )

    def test_doctor_views_show_registered_patient_age(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            service=self.service,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка віку',
            status=Appointment.STATUS_APPROVED,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        appointments_response = self.client.get(
            reverse('doctor_appointments'),
            {'week': appointment.date.isoformat()},
        )
        patients_response = self.client.get(reverse('doctor_patient_cards'))
        detail_response = self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))

        self.assertContains(appointments_response, '25 років')
        self.assertContains(patients_response, '25 років')
        self.assertContains(detail_response, '25 років')

    def test_appointment_details_show_day_schedule_and_highlight_active_visit(self):
        fixed_now = timezone.make_aware(datetime(2026, 7, 15, 10, 30))
        active = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Активний',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=fixed_now.date(),
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Поточний прийом',
            duration_minutes_exact=60,
            status=Appointment.STATUS_APPROVED,
        )
        later = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Наступний',
            patient_last_name='Пацієнт',
            patient_phone='+380504444444',
            date=fixed_now.date(),
            time=time(12, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Наступна заявка',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        with patch('clinic.views.timezone.localtime', return_value=fixed_now):
            response = self.client.get(reverse('doctor_appointment_detail', args=[later.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Записи на Середа, 15.07.2026')
        self.assertContains(response, active.patient_name)
        self.assertContains(response, later.patient_name)
        self.assertContains(response, 'Зараз на прийомі')
        self.assertContains(response, 'active-now')

    def test_doctor_cannot_open_another_doctors_appointment(self):
        other_user = User.objects.create_user(username='other-doctor', password='pass12345')
        Profile.objects.create(user=other_user, role=Profile.ROLE_DOCTOR)
        other_doctor = Doctor.objects.create(user=other_user, specialization='Хірург')
        appointment = Appointment.objects.create(
            doctor=other_doctor,
            patient_first_name='Інший',
            patient_last_name='Пацієнт',
            patient_phone='+380503333333',
            date=timezone.localdate() + timedelta(days=7),
            time='09:00',
            city='Київ',
            address='вул. Тестова, 2',
            reason='Чужа заявка',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))

        self.assertEqual(response.status_code, 404)

    def test_doctor_confirms_request_using_minutes(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=future_date,
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Підтвердження у хвилинах',
            status=Appointment.STATUS_PENDING,
        )

        response = self.client.post(
            reverse('doctor_review_appointment', args=[appointment.id]),
            data={'action': 'approve', 'duration_minutes': 120},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)
        self.assertEqual(appointment.duration_slots, 2)
        self.assertEqual(appointment.duration_minutes_exact, 120)
        self.assertContains(response, 'Заявку підтверджено.')
        self.assertContains(response, 'data-auto-dismiss')
        self.assertNotContains(response, '120 хв')

    def test_doctor_duration_must_match_schedule_slot(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=future_date,
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Некоректна тривалість',
            status=Appointment.STATUS_PENDING,
        )

        response = self.client.post(
            reverse('doctor_review_appointment', args=[appointment.id]),
            data={'action': 'approve', 'duration_minutes': 75},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_PENDING)
        self.assertContains(response, 'Тривалість має бути кратною тривалості слота: 60 хв.')

    def test_doctor_can_propose_new_time_and_patient_sees_alert(self):
        original_date = timezone.localdate() + timedelta(days=7)
        proposed_date = original_date + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=original_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Потрібно перенести прийом',
            status=Appointment.STATUS_PENDING,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        detail_response = self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))
        response = self.client.post(
            reverse('doctor_propose_reschedule', args=[appointment.id]),
            data={
                'date': proposed_date.strftime('%Y-%m-%d'),
                'time': '10:00',
                'duration_minutes': 60,
            },
        )

        appointment.refresh_from_db()
        self.assertContains(detail_response, 'Перенести прийом')
        self.assertRedirects(response, reverse('doctor_appointment_detail', args=[appointment.id]))
        self.assertEqual(appointment.status, Appointment.STATUS_RESCHEDULE_PROPOSED)
        self.assertEqual(appointment.previous_date, original_date)
        self.assertEqual(appointment.previous_time, time(9, 0))
        self.assertEqual(appointment.date, proposed_date)
        self.assertEqual(appointment.time, time(10, 0))

        self.client.logout()
        self.client.login(username='patient@test.local', password='pass12345')
        dashboard_response = self.client.get(reverse('patient_dashboard'))
        patient_detail_response = self.client.get(
            reverse('patient_appointment_detail', args=[appointment.id])
        )

        self.assertContains(dashboard_response, 'хоче змінити час прийому')
        self.assertContains(dashboard_response, reverse('patient_appointment_detail', args=[appointment.id]))
        self.assertContains(dashboard_response, 'Деталі')
        self.assertContains(dashboard_response, 'aria-label="Погодитися з новим часом"')
        self.assertContains(dashboard_response, 'aria-label="Відхилити та скасувати запис"')
        self.assertContains(patient_detail_response, 'Погодитися з новим часом')
        self.assertNotContains(patient_detail_response, 'patient-reschedule-decision')
        self.assertContains(
            patient_detail_response,
            f'{original_date.strftime("%d.%m.%Y")}, 09:00',
        )
        self.assertContains(
            patient_detail_response,
            f'{proposed_date.strftime("%d.%m.%Y")}, 10:00',
        )
        self.assertContains(patient_detail_response, original_date.strftime('%d.%m.%Y'))
        self.assertContains(patient_detail_response, proposed_date.strftime('%d.%m.%Y'))

    def test_patient_can_accept_doctor_reschedule_proposal(self):
        original_date = timezone.localdate() + timedelta(days=7)
        proposed_date = original_date + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=proposed_date,
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Погодження нового часу',
            duration_minutes_exact=60,
            status=Appointment.STATUS_RESCHEDULE_PROPOSED,
            previous_date=original_date,
            previous_time=time(9, 0),
            reschedule_requested_at=timezone.now(),
        )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('patient_reschedule_response', args=[appointment.id]),
            data={'action': 'accept'},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)
        self.assertIsNotNone(appointment.approved_at)
        self.assertContains(response, 'Новий час прийому підтверджено.')
        self.assertNotContains(response, 'хоче змінити час прийому')

    def test_patient_can_reject_doctor_reschedule_proposal(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() + timedelta(days=14),
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Відмова від нового часу',
            status=Appointment.STATUS_RESCHEDULE_PROPOSED,
            previous_date=timezone.localdate() + timedelta(days=7),
            previous_time=time(9, 0),
            reschedule_requested_at=timezone.now(),
        )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('patient_reschedule_response', args=[appointment.id]),
            data={'action': 'reject'},
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_CANCELED)
        self.assertContains(response, 'Запропонований час відхилено. Запис скасовано.')

    def test_doctor_cannot_propose_time_that_overlaps_another_appointment(self):
        original_date = timezone.localdate() + timedelta(days=7)
        target_date = original_date + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=original_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Спроба перенесення',
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Інший',
            patient_last_name='Пацієнт',
            patient_phone='+380509999999',
            date=target_date,
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Зайнятий час',
            status=Appointment.STATUS_APPROVED,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_propose_reschedule', args=[appointment.id]),
            data={
                'date': target_date.strftime('%Y-%m-%d'),
                'time': '10:00',
                'duration_minutes': 60,
            },
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_PENDING)
        self.assertEqual(appointment.date, original_date)
        self.assertContains(response, 'Обраний час перетинається з іншим записом')

    def test_lunch_break_removes_slots_and_blocks_overlapping_appointment(self):
        visit_date = timezone.localdate()
        self.schedule.start_time = time(9, 0)
        self.schedule.end_time = time(12, 0)
        self.schedule.slot_minutes = 20
        self.schedule.break_start_time = time(10, 0)
        self.schedule.break_duration_minutes = 45
        self.schedule.save(
            update_fields=[
                'start_time',
                'end_time',
                'slot_minutes',
                'break_start_time',
                'break_duration_minutes',
            ]
        )

        slots = [slot.strftime('%H:%M') for slot in self.schedule.get_slots()]

        self.assertNotIn('10:00', slots)
        self.assertNotIn('10:20', slots)
        self.assertNotIn('10:40', slots)
        self.assertIn('11:00', slots)
        self.assertTrue(
            appointment_conflicts(
                self.doctor,
                visit_date,
                time(9, 50),
                duration_minutes=20,
            )
        )
        self.assertFalse(
            appointment_conflicts(
                self.doctor,
                visit_date,
                time(9, 30),
                duration_minutes=20,
            )
        )

    def test_patient_can_restore_future_canceled_appointment(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=future_date,
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка відновлення',
            status=Appointment.STATUS_CANCELED,
        )

        response = self.client.post(
            reverse('restore_appointment', args=[appointment.id]),
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_PENDING)
        self.assertContains(response, 'Заявку відновлено і знову відправлено лікарю.')

    def test_canceled_appointment_frees_slot_for_another_patient(self):
        future_date = timezone.localdate() + timedelta(days=7)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=future_date,
            time='09:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Скасований прийом',
            status=Appointment.STATUS_APPROVED,
        )

        self.assertTrue(
            appointment_conflicts(
                self.doctor,
                future_date,
                time(9, 0),
                duration_minutes=appointment.duration_minutes,
            )
        )

        self.client.login(username='patient@test.local', password='pass12345')
        self.client.post(reverse('cancel_appointment', args=[appointment.id]))
        appointment.refresh_from_db()

        self.assertEqual(appointment.status, Appointment.STATUS_CANCELED)
        self.assertFalse(
            appointment_conflicts(
                self.doctor,
                future_date,
                time(9, 0),
                duration_minutes=appointment.duration_minutes,
            )
        )

    def test_canceled_appointment_cannot_be_restored_after_slot_is_taken(self):
        self.client.login(username='patient@test.local', password='pass12345')
        future_date = timezone.localdate() + timedelta(days=7)
        canceled = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=future_date,
            time='09:00',
            duration_slots=2,
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Скасований прийом',
            status=Appointment.STATUS_CANCELED,
        )
        other_patient = User.objects.create_user(
            username='other-patient@test.local',
            password='pass12345',
            first_name='Інший',
            last_name='Пацієнт',
        )
        Profile.objects.create(
            user=other_patient,
            role=Profile.ROLE_PATIENT,
            phone='+380503333333',
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=other_patient,
            patient_first_name='Інший',
            patient_last_name='Пацієнт',
            patient_phone='+380503333333',
            date=future_date,
            time='10:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Новий прийом на звільнений час',
            status=Appointment.STATUS_APPROVED,
        )

        dashboard_response = self.client.get(reverse('patient_dashboard'))
        self.assertContains(dashboard_response, 'Цей час уже зайнятий іншим записом')
        self.assertNotContains(
            dashboard_response,
            reverse('restore_appointment', args=[canceled.id]),
        )

        response = self.client.post(
            reverse('restore_appointment', args=[canceled.id]),
            follow=True,
        )
        canceled.refresh_from_db()

        self.assertEqual(canceled.status, Appointment.STATUS_CANCELED)
        self.assertContains(
            response,
            'Цей запис уже не можна відновити: час зайнятий іншим прийомом.',
        )

    def test_approved_appointment_becomes_completed_after_end_time(self):
        self.client.login(username='patient@test.local', password='pass12345')
        appointment_start = timezone.localtime() - timedelta(hours=2)
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=appointment_start.date(),
            time=appointment_start.time().replace(second=0, microsecond=0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка автозавершення',
            status=Appointment.STATUS_APPROVED,
            approved_at=timezone.now() - timedelta(hours=3),
        )

        self.client.get(reverse('patient_dashboard'))

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_COMPLETED)

    def test_guest_sees_published_clinic_and_doctor_news(self):
        NewsPost.objects.create(title='Новина клініки', text='Текст клініки')
        NewsPost.objects.create(doctor=self.doctor, title='Новина лікаря', text='Текст лікаря')

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Новина клініки')
        self.assertContains(response, 'Новина лікаря')
        self.assertContains(response, 'Галерея')
        self.assertContains(response, 'data-home-slide')
        self.assertContains(response, 'data-home-reveal-header')
        self.assertContains(response, 'clinic/home.js?v=20260729-1')

        content = response.content.decode()
        self.assertLess(content.index('Новини клініки'), content.index('Новини лікарів'))
        self.assertLess(content.index('Новини лікарів'), content.index('id="home-gallery-title"'))
        self.assertLess(content.index('id="home-gallery-title"'), content.index('Наші провідні лікарі'))

        home_script = (Path(settings.BASE_DIR) / 'static' / 'clinic' / 'home.js').read_text(encoding='utf-8')
        self.assertIn('10000', home_script)

    def test_long_doctor_service_list_is_moved_to_detail_page(self):
        for index in range(4):
            MedicalService.objects.create(
                doctor=self.doctor,
                name=f'Додаткова послуга {index + 1}',
                approximate_price=600 + index,
            )

        list_response = self.client.get(reverse('doctors'))
        detail_response = self.client.get(reverse('doctor_detail', args=[self.doctor.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, 'Додаткова послуга 1')
        self.assertContains(list_response, 'Детальніше')
        for index in range(4):
            self.assertContains(detail_response, f'Додаткова послуга {index + 1}')

    def test_authenticated_user_can_open_home_without_login_button(self):
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новини клініки')
        self.assertNotContains(response, 'Увійти через Google')
        self.assertContains(response, 'Записатися на прийом')
        self.assertContains(response, 'Переглянути лікарів')

    def test_guest_can_start_claiming_doctor_created_record_by_phone(self):
        phone = '+380501234567'
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Новий',
            patient_last_name='Пацієнт',
            patient_phone=phone,
            date=timezone.localdate() + timedelta(days=7),
            time='10:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Запис створив лікар',
            status=Appointment.STATUS_APPROVED,
        )

        response = self.client.post(
            reverse('claim_patient'),
            data={'phone': '050 123 45 67'},
            follow=True,
        )

        self.assertRedirects(response, reverse('pending_patient_dashboard'))
        self.assertContains(response, 'Увійдіть через Google для подальшої роботи із сайтом.')
        self.assertContains(response, 'claim-google-message')
        self.assertContains(response, 'next=%2Fpatient%2Fclaim%2Fcomplete%2F')
        self.assertContains(response, 'Новий Пацієнт')
        self.assertContains(response, phone)
        self.assertContains(response, 'Підтверджені прийоми')
        self.assertContains(response, self.service.name)
        self.assertContains(response, (timezone.localdate() + timedelta(days=7)).strftime('%d.%m.%Y'))
        self.assertNotContains(response, 'Запис створив лікар')
        self.assertEqual(self.client.session['patient_claim_phone'], phone)

    def test_guest_can_register_with_phone_without_doctor_records(self):
        phone = '+380501234577'

        response = self.client.post(
            reverse('claim_patient'),
            data={'phone': phone},
            follow=True,
        )

        self.assertRedirects(response, reverse('pending_patient_dashboard'))
        self.assertContains(response, 'Увійдіть через Google для подальшої роботи із сайтом.')
        self.assertContains(response, 'Новий пацієнт')
        self.assertEqual(self.client.session['patient_claim_phone'], phone)

    def test_pending_patient_home_hides_registration_buttons(self):
        session = self.client.session
        session['patient_claim_phone'] = '+380501234577'
        session.save()

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Перейти до кабінету')
        self.assertContains(response, 'Переглянути лікарів')
        self.assertNotContains(response, 'Реєстрація за номером телефону')

    def test_pending_patient_booking_returns_to_temporary_cabinet(self):
        session = self.client.session
        session['patient_claim_phone'] = '+380501234577'
        session.save()

        response = self.client.get(reverse('booking'), follow=True)

        self.assertRedirects(response, reverse('pending_patient_dashboard'))
        self.assertContains(response, 'Спочатку увійдіть в акаунт Google.')
        self.assertContains(response, 'Профіль пацієнта')

    def test_google_login_creates_empty_patient_profile_for_new_phone(self):
        phone = '+380501234578'
        google_user = User.objects.create_user(
            username='new-google-patient@test.local',
            email='new-google-patient@test.local',
        )
        profile = Profile.objects.create(
            user=google_user,
            role=Profile.ROLE_PATIENT,
            phone='',
        )
        SocialAccount.objects.create(
            user=google_user,
            provider='google',
            uid='new-google-patient-uid',
        )
        self.client.force_login(google_user)
        session = self.client.session
        session['patient_claim_phone'] = phone
        session.save()

        response = self.client.get(reverse('claim_patient_complete'), follow=True)

        self.assertRedirects(response, reverse('patient_dashboard'))
        self.assertContains(response, 'Ваш кабінет створено. Заповніть особисті дані у профілі.')
        self.assertContains(response, 'Редагувати профіль')
        profile.refresh_from_db()
        self.assertEqual(profile.phone, phone)
        self.assertFalse(Appointment.objects.filter(patient=google_user).exists())
        self.assertNotIn('patient_claim_phone', self.client.session)

    def test_phone_registration_rejects_number_of_existing_patient(self):
        response = self.client.post(
            reverse('claim_patient'),
            data={'phone': self.patient.profile.phone},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Цей номер уже прив’язаний до кабінету.')
        self.assertNotIn('patient_claim_phone', self.client.session)

    def test_guest_booking_requires_google_login(self):
        response = self.client.get(reverse('booking'), follow=True)

        self.assertRedirects(response, reverse('home'))
        self.assertContains(response, 'Спочатку увійдіть в акаунт Google.')

    def test_local_patient_without_google_cannot_use_patient_cabinet(self):
        local_patient = User.objects.create_user(
            username='local-only-patient',
            password='pass12345',
        )
        Profile.objects.create(
            user=local_patient,
            role=Profile.ROLE_PATIENT,
            phone='+380501234599',
            age=30,
        )
        self.client.force_login(local_patient)

        response = self.client.get(reverse('patient_dashboard'), follow=True)

        self.assertRedirects(response, reverse('home'))
        self.assertContains(response, 'Спочатку увійдіть в акаунт Google.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_google_patient_can_claim_appointments_and_cards_by_phone(self):
        phone = '+380501234568'
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Новий',
            patient_last_name='Пацієнт',
            patient_phone='050 123 45 68',
            date=timezone.localdate() + timedelta(days=7),
            time='10:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Запис для прив’язування',
            status=Appointment.STATUS_APPROVED,
        )
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient_first_name='Новий',
            patient_last_name='Пацієнт',
            patient_phone='050 123 45 68',
        )
        google_user = User.objects.create_user(
            username='google-patient@test.local',
            email='google-patient@test.local',
            first_name='Імʼя Google',
            last_name='Прізвище Google',
        )
        profile = Profile.objects.create(
            user=google_user,
            role=Profile.ROLE_PATIENT,
            phone='',
        )
        SocialAccount.objects.create(
            user=google_user,
            provider='google',
            uid='google-patient-uid',
        )
        self.client.force_login(google_user)
        session = self.client.session
        session['patient_claim_phone'] = phone
        session.save()

        response = self.client.get(reverse('claim_patient_complete'))

        self.assertRedirects(response, reverse('patient_dashboard'))
        appointment.refresh_from_db()
        card.refresh_from_db()
        profile.refresh_from_db()
        google_user.refresh_from_db()
        self.assertEqual(appointment.patient, google_user)
        self.assertEqual(card.patient, google_user)
        self.assertEqual(profile.phone, phone)
        self.assertEqual(google_user.first_name, 'Новий')
        self.assertEqual(google_user.last_name, 'Пацієнт')
        self.assertNotIn('patient_claim_phone', self.client.session)

    def test_claiming_records_requires_linked_google_account(self):
        phone = '+380501234569'
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Новий',
            patient_last_name='Пацієнт',
            patient_phone=phone,
            date=timezone.localdate() + timedelta(days=7),
            time='10:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Запис без Google',
            status=Appointment.STATUS_APPROVED,
        )
        local_user = User.objects.create_user(
            username='local-patient',
            password='pass12345',
        )
        Profile.objects.create(
            user=local_user,
            role=Profile.ROLE_PATIENT,
            phone='',
        )
        self.client.force_login(local_user)
        session = self.client.session
        session['patient_claim_phone'] = phone
        session.save()

        response = self.client.get(reverse('claim_patient_complete'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('google_login'), response.url)
        appointment.refresh_from_db()
        self.assertIsNone(appointment.patient)

    def test_patient_and_doctor_profile_forms_do_not_show_email(self):
        self.assertNotIn('email', PatientProfileForm(user=self.patient).fields)
        self.assertNotIn('email', DoctorProfileForm(doctor=self.doctor).fields)
        self.assertNotIn('email', DoctorPatientBookingForm(doctor=self.doctor).fields)

    def test_admin_leaves_doctor_description_and_photo_link_for_doctor(self):
        admin_user = User.objects.create_superuser(
            username='admin-create-doctor',
            password='pass12345',
            email='admin@example.com',
        )
        self.client.force_login(admin_user)

        page = self.client.get(reverse('admin_add_doctor'))
        self.assertContains(page, 'Фото з пристрою')
        self.assertNotContains(page, 'Опис:')
        self.assertNotContains(page, 'Посилання на фото')

        response = self.client.post(
            reverse('admin_add_doctor'),
            data={
                'username': 'new-doctor',
                'first_name': 'Новий',
                'last_name': 'Лікар',
                'email': '',
                'phone': '+380501234568',
                'password': 'pass12345',
                'specialization': 'Ортодонт',
                'description': 'Це поле адміністратор не повинен задавати.',
                'photo_url': 'https://example.com/doctor.jpg',
            },
        )

        doctor = Doctor.objects.get(user__username='new-doctor')
        self.assertRedirects(response, reverse('admin_panel'))
        self.assertEqual(doctor.description, '')
        self.assertEqual(doctor.photo_url, '')

    def test_doctor_can_publish_short_about_text(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        about_text = 'Працюю уважно та пояснюю кожен етап лікування.'

        response = self.client.post(
            reverse('doctor_edit_profile'),
            data={
                'first_name': self.doctor.user.first_name,
                'last_name': self.doctor.user.last_name,
                'phone': self.doctor.phone,
                'specialization': self.doctor.specialization,
                'photo_url': '',
                'description': about_text,
            },
        )

        self.doctor.refresh_from_db()
        self.assertRedirects(response, reverse('doctor_dashboard'))
        self.assertEqual(self.doctor.description, about_text)

        detail_response = self.client.get(reverse('doctor_detail', args=[self.doctor.id]))
        self.assertContains(detail_response, 'Про лікаря')
        self.assertContains(detail_response, about_text)

        NewsPost.objects.create(
            doctor=self.doctor,
            title='Новина лікаря для профілю',
            text='Короткий текст новини.',
        )
        dashboard_response = self.client.get(reverse('doctor_dashboard'))
        self.assertContains(dashboard_response, 'Профіль лікаря')
        self.assertContains(dashboard_response, about_text)
        self.assertContains(dashboard_response, self.doctor.specialization)
        self.assertContains(dashboard_response, 'doctor-profile-photo')
        self.assertContains(dashboard_response, 'Записати пацієнта')
        self.assertContains(dashboard_response, 'Редагувати профіль')
        self.assertContains(dashboard_response, 'Редагувати графік')
        self.assertContains(dashboard_response, 'Редагувати послуги')
        self.assertContains(dashboard_response, 'Редагувати новини')
        self.assertContains(dashboard_response, 'Новина лікаря для профілю')
        self.assertNotContains(dashboard_response, '>Графік</a>')
        self.assertNotContains(dashboard_response, '>Послуги</a>')
        self.assertNotContains(dashboard_response, '>Мої новини</a>')
        self.assertNotContains(dashboard_response, 'action-board')
        self.assertNotContains(dashboard_response, 'Швидкий доступ')
        self.assertNotContains(dashboard_response, 'Найближчі прийоми')

    def test_patient_can_upload_profile_photo(self):
        self.client.login(username='patient@test.local', password='pass12345')
        photo = SimpleUploadedFile(
            'profile.gif',
            b'GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00'
            b'\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;',
            content_type='image/gif',
        )

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('patient_edit_profile'),
                    data={
                        'first_name': self.patient.first_name,
                        'last_name': self.patient.last_name,
                        'age': 25,
                        'phone': self.patient.profile.phone,
                        'photo': photo,
                    },
                )

                self.patient.profile.refresh_from_db()
                self.assertRedirects(response, reverse('patient_dashboard'))
                self.assertTrue(self.patient.profile.photo.name.startswith('patient_photos/'))

    def test_patient_dashboard_shows_doctor_photo_on_appointment_card(self):
        self.doctor.photo_url = 'https://example.com/doctor-photo.jpg'
        self.doctor.save(update_fields=['photo_url'])
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка нового кабінету',
            status=Appointment.STATUS_PENDING,
        )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('patient_dashboard'))

        self.assertContains(response, 'patient-dashboard-profile')
        self.assertContains(response, 'patient-appointment-doctor')
        self.assertContains(response, self.doctor.photo_url)
        self.assertContains(response, self.doctor.full_name)
        self.assertContains(response, self.doctor.specialization)

    def test_patient_dashboard_highlights_only_nearest_approved_appointment(self):
        for day_offset in (7, 14):
            Appointment.objects.create(
                doctor=self.doctor,
                service=self.service,
                patient=self.patient,
                patient_first_name=self.patient.first_name,
                patient_last_name=self.patient.last_name,
                patient_phone=self.patient.profile.phone,
                date=timezone.localdate() + timedelta(days=day_offset),
                time=time(9, 0),
                city='Дніпро',
                address='вул. Тестова, 1',
                reason='Майбутній прийом',
                status=Appointment.STATUS_APPROVED,
            )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('patient_dashboard'))
        content = response.content.decode()

        self.assertContains(response, 'Найближчий прийом')
        self.assertEqual(content.count('patient-appointment-card-featured'), 1)
        self.assertEqual(content.count('patient-nearest-badge'), 1)

    def test_incomplete_patient_profile_shows_notice_and_blocks_booking(self):
        self.patient.profile.age = None
        self.patient.profile.save(update_fields=['age'])
        self.client.login(username='patient@test.local', password='pass12345')

        dashboard_response = self.client.get(reverse('patient_dashboard'))
        booking_response = self.client.get(reverse('booking'))

        self.assertContains(dashboard_response, 'Будь ласка, заповніть профіль')
        self.assertContains(dashboard_response, 'Редагувати профіль')
        self.assertRedirects(booking_response, reverse('patient_edit_profile'))

    def test_saving_age_completes_patient_profile(self):
        self.patient.profile.age = None
        self.patient.profile.save(update_fields=['age'])
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.post(
            reverse('patient_edit_profile'),
            data={
                'first_name': 'Олена',
                'last_name': 'Коваль',
                'age': 34,
                'phone': self.patient.profile.phone,
            },
            follow=True,
        )

        self.patient.profile.refresh_from_db()
        self.assertEqual(self.patient.profile.age, 34)
        self.assertNotContains(response, 'Будь ласка, заповніть профіль')

    def test_doctor_can_search_patient_cards_by_name_and_phone(self):
        DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient_first_name='Марія',
            patient_last_name='Коваль',
            patient_phone='+380671112233',
        )
        DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient_first_name='Олег',
            patient_last_name='Бондар',
            patient_phone='+380932224455',
        )
        DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('doctor_patient_cards'), {'q': 'Марія Коваль'})
        self.assertContains(response, 'Марія Коваль')
        self.assertNotContains(response, 'Олег Бондар')

        response = self.client.get(reverse('doctor_patient_cards'), {'q': '093 222 44 55'})
        self.assertContains(response, 'Олег Бондар')
        self.assertNotContains(response, 'Марія Коваль')

        response = self.client.get(reverse('doctor_patient_cards'), {'q': '25'})
        self.assertContains(response, self.patient.get_full_name())
        self.assertNotContains(response, 'Марія Коваль')

    def test_list_searches_use_live_filters_without_search_buttons(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name=self.patient.first_name,
            patient_last_name=self.patient.last_name,
            patient_phone=self.patient.profile.phone,
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка живого фільтра',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        patient_response = self.client.get(reverse('doctor_patient_cards'))
        appointment_response = self.client.get(
            reverse('doctor_appointments'),
            {'week': appointment.date.isoformat()},
        )

        self.assertContains(patient_response, 'Фільтр пацієнтів')
        self.assertContains(patient_response, 'data-live-filter-input')
        self.assertContains(patient_response, 'data-live-filter-item')
        self.assertContains(patient_response, str(self.patient.profile.age))
        self.assertNotContains(patient_response, '>Знайти</button>')

        self.assertContains(appointment_response, 'Фільтр заявок і прийомів')
        self.assertContains(appointment_response, 'data-live-filter-group')
        self.assertContains(appointment_response, 'data-live-filter-clear')
        self.assertContains(appointment_response, f'data-live-filter-value="{appointment.patient_name}"')
        self.assertContains(appointment_response, appointment.patient_phone)
        self.assertNotContains(appointment_response, '>Знайти</button>')

        public_response = self.client.get(reverse('doctors'))
        self.assertContains(public_response, 'Фільтр лікарів')
        self.assertContains(public_response, 'clinic/live_filter.js')
        self.assertNotContains(public_response, '>Знайти</button>')

        self.client.logout()
        self.client.login(username='patient@test.local', password='pass12345')
        patient_dashboard_response = self.client.get(reverse('patient_dashboard'))
        self.assertNotContains(patient_dashboard_response, 'Фільтр заявок')
        self.assertNotContains(patient_dashboard_response, 'Фільтр підтверджених прийомів')
        self.assertNotContains(patient_dashboard_response, 'Фільтр завершених прийомів')
        self.assertNotContains(patient_dashboard_response, 'Фільтр скасованих прийомів')
        self.assertContains(patient_dashboard_response, 'data-live-pagination')
        self.assertContains(patient_dashboard_response, 'data-live-filter-item')

    def test_doctor_booking_rejects_manual_phone_of_registered_patient(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.strftime('%Y-%m-%d'),
                'time': '09:00',
                'patient': '',
                'service': self.service.id,
                'duration_minutes': 60,
                'first_name': 'Помилкове',
                'last_name': "Ім'я",
                'phone': '050 111 11 11',
                'reason': 'Перевірка наявного номера',
            },
            follow=True,
        )

        self.assertFalse(Appointment.objects.filter(reason='Перевірка наявного номера').exists())
        self.assertContains(response, 'Цей номер належить зареєстрованому пацієнту')
        self.assertContains(response, 'data-patient-picker')
        self.assertContains(response, 'Почніть вводити ім’я, прізвище або телефон')
        self.assertContains(response, 'clinic/patient_picker.js')

    def test_doctor_can_book_registered_patient_after_selecting_search_result(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.strftime('%Y-%m-%d'),
                'time': '09:00',
                'patient': self.patient.id,
                'service': self.service.id,
                'duration_minutes': 60,
                'reason': 'Пацієнта обрано через пошук',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('doctor_appointments')}?week={visit_date.isoformat()}",
        )
        appointment = Appointment.objects.get(reason='Пацієнта обрано через пошук')
        self.assertEqual(appointment.patient, self.patient)
        self.assertEqual(appointment.patient_first_name, self.patient.first_name)
        self.assertEqual(appointment.patient_last_name, self.patient.last_name)

    def test_doctor_booking_has_live_service_filter(self):
        MedicalService.objects.create(
            doctor=self.doctor,
            name='Професійна гігієна',
        )
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)

        response = self.client.get(
            reverse('doctor_book_patient'),
            {
                'date': visit_date.isoformat(),
                'time': '09:00',
            },
        )

        self.assertContains(response, 'id="doctor-booking-service-search"')
        self.assertContains(response, 'data-live-filter-input')
        self.assertContains(response, 'data-live-filter-clear')
        self.assertContains(response, 'data-live-filter-item', count=2)
        self.assertContains(response, 'type="radio"')
        self.assertContains(response, 'Професійна гігієна')

    def test_doctor_can_split_slot_and_book_patient_between_appointments(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)
        self.schedule.slot_minutes = 20
        self.schedule.save(update_fields=['slot_minutes'])
        first_appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Перший',
            patient_last_name='Пацієнт',
            patient_phone='+380501000001',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перший прийом',
            duration_minutes_exact=20,
            status=Appointment.STATUS_APPROVED,
        )
        Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Другий',
            patient_last_name='Пацієнт',
            patient_phone='+380501000002',
            date=visit_date,
            time=time(9, 20),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Другий прийом',
            duration_minutes_exact=20,
            status=Appointment.STATUS_APPROVED,
        )

        selection_response = self.client.get(
            reverse('doctor_book_patient'),
            {
                'date': visit_date.isoformat(),
                'split': first_appointment.id,
            },
        )

        self.assertContains(selection_response, 'Запис між двома прийомами')
        self.assertContains(selection_response, '09:10')
        self.assertContains(selection_response, 'Між прийомами · 10 хв')

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.isoformat(),
                'time': '09:10',
                'split_appointment': first_appointment.id,
                'patient': self.patient.id,
                'service': self.service.id,
                'duration_minutes': 10,
                'reason': 'Терміновий запис між прийомами',
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('doctor_appointments')}?week={visit_date.isoformat()}",
        )
        first_appointment.refresh_from_db()
        inserted_appointment = Appointment.objects.get(
            reason='Терміновий запис між прийомами'
        )
        self.assertEqual(first_appointment.duration_minutes, 10)
        self.assertEqual(inserted_appointment.time, time(9, 10))
        self.assertEqual(inserted_appointment.duration_minutes, 10)
        self.assertEqual(inserted_appointment.status, Appointment.STATUS_APPROVED)

    def test_doctor_cannot_split_slot_without_following_appointment(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)
        self.schedule.slot_minutes = 20
        self.schedule.save(update_fields=['slot_minutes'])
        first_appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient_first_name='Єдиний',
            patient_last_name='Пацієнт',
            patient_phone='+380501000003',
            date=visit_date,
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Єдиний прийом',
            duration_minutes_exact=20,
            status=Appointment.STATUS_APPROVED,
        )

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.isoformat(),
                'time': '09:10',
                'split_appointment': first_appointment.id,
                'patient': self.patient.id,
                'service': self.service.id,
                'duration_minutes': 10,
                'reason': 'Недозволений поділ',
            },
        )

        first_appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Цей слот уже не можна поділити.')
        self.assertEqual(first_appointment.duration_minutes, 20)
        self.assertFalse(
            Appointment.objects.filter(reason='Недозволений поділ').exists()
        )

    def test_doctor_booking_reuses_unregistered_patient_card_by_phone(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        visit_date = timezone.localdate() + timedelta(days=7)
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient_first_name='Відомий',
            patient_last_name='Пацієнт',
            patient_phone='050 222 33 44',
        )

        response = self.client.post(
            reverse('doctor_book_patient'),
            data={
                'date': visit_date.strftime('%Y-%m-%d'),
                'time': '10:00',
                'patient': '',
                'service': self.service.id,
                'duration_minutes': 60,
                'first_name': 'Інше',
                'last_name': "Ім'я",
                'phone': '+380502223344',
                'reason': 'Повторний запис без акаунта',
            },
            follow=True,
        )

        appointment = Appointment.objects.get(reason='Повторний запис без акаунта')
        card.refresh_from_db()
        self.assertIsNone(appointment.patient)
        self.assertEqual(appointment.patient_first_name, 'Відомий')
        self.assertEqual(appointment.patient_last_name, 'Пацієнт')
        self.assertEqual(card.patient_phone, '+380502223344')
        self.assertEqual(self.doctor.patient_cards.count(), 1)
        self.assertContains(response, 'використано наявну картку')

    def test_doctor_home_button_opens_doctor_dashboard(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Перейти до кабінету лікаря')
        self.assertContains(response, reverse('doctor_dashboard'))
        self.assertNotContains(response, 'Переглянути лікарів')

    def test_admin_home_button_opens_admin_panel(self):
        User.objects.create_superuser(
            username='admin@test.local',
            email='admin@test.local',
            password='pass12345',
        )
        self.client.login(username='admin@test.local', password='pass12345')

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'Перейти до панелі адміністратора')
        self.assertContains(response, reverse('admin_panel'))
        self.assertNotContains(response, 'Переглянути лікарів')

    def test_admin_panel_has_clear_management_sections(self):
        admin_user = User.objects.create_superuser(
            username='admin-panel@test.local',
            email='admin-panel@test.local',
            password='pass12345',
        )
        self.client.login(username='admin-panel@test.local', password='pass12345')

        response = self.client.get(reverse('admin_panel'))

        self.assertContains(response, 'Керування клінікою')
        self.assertContains(response, 'Що потрібно зробити?')
        self.assertContains(response, 'Знайти користувача')
        self.assertContains(response, 'data-page-size="10"')
        self.assertContains(response, 'Нещодавно створені заявки та прийоми.')
        self.assertContains(response, 'Це ви')
        self.assertContains(response, reverse('admin_edit_user', args=[admin_user.id]))
        self.assertNotContains(response, '<th>Електронна пошта</th>', html=True)

    def test_telegram_broadcast_page_requires_admin(self):
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('admin_telegram_broadcast'))

        self.assertRedirects(response, reverse('administration_login'))

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='test-clinic-bot',
    )
    @patch('clinic.telegram.TelegramBotClient.send_message')
    def test_admin_can_broadcast_telegram_message_to_all_active_connections(self, send_message):
        admin_user = User.objects.create_superuser(
            username='broadcast-admin@test.local',
            email='broadcast-admin@test.local',
            password='pass12345',
        )
        TelegramConnection.objects.create(
            user=self.patient,
            chat_id=101,
            username='patient_chat',
        )
        TelegramConnection.objects.create(
            user=self.doctor.user,
            chat_id=202,
            username='doctor_chat',
        )
        inactive_user = User.objects.create_user(
            username='inactive@test.local',
            password='pass12345',
            is_active=False,
        )
        TelegramConnection.objects.create(
            user=inactive_user,
            chat_id=303,
            username='inactive_chat',
        )
        self.client.login(username=admin_user.username, password='pass12345')

        response = self.client.post(
            reverse('admin_telegram_broadcast'),
            data={
                'audience': 'all',
                'recipient': '',
                'message': 'Графік <змінено> & перевірено.',
            },
        )

        self.assertRedirects(response, reverse('admin_telegram_broadcast'))
        self.assertEqual(send_message.call_count, 2)
        self.assertEqual({call.args[0] for call in send_message.call_args_list}, {101, 202})
        sent_text = send_message.call_args_list[0].args[1]
        self.assertIn('<b>Повідомлення від клініки</b>', sent_text)
        self.assertIn('Графік &lt;змінено&gt; &amp; перевірено.', sent_text)
        self.assertTrue(
            AuditLog.objects.filter(action='Надіслано Telegram-повідомлення').exists()
        )

    @override_settings(
        TELEGRAM_BOT_TOKEN='test-token',
        TELEGRAM_BOT_USERNAME='test-clinic-bot',
    )
    @patch('clinic.telegram.TelegramBotClient.send_message')
    def test_admin_can_send_telegram_message_to_one_user(self, send_message):
        admin_user = User.objects.create_superuser(
            username='single-broadcast-admin@test.local',
            email='single-broadcast-admin@test.local',
            password='pass12345',
        )
        patient_connection = TelegramConnection.objects.create(
            user=self.patient,
            chat_id=404,
            username='single_patient',
        )
        TelegramConnection.objects.create(
            user=self.doctor.user,
            chat_id=505,
            username='other_doctor',
        )
        self.client.login(username=admin_user.username, password='pass12345')

        response = self.client.post(
            reverse('admin_telegram_broadcast'),
            data={
                'audience': 'single',
                'recipient': patient_connection.pk,
                'message': 'Особисте повідомлення.',
            },
        )

        self.assertRedirects(response, reverse('admin_telegram_broadcast'))
        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args[0], 404)

    def test_admin_broadcast_requires_recipient_in_single_mode(self):
        admin_user = User.objects.create_superuser(
            username='invalid-broadcast-admin@test.local',
            email='invalid-broadcast-admin@test.local',
            password='pass12345',
        )
        TelegramConnection.objects.create(user=self.patient, chat_id=606)
        self.client.login(username=admin_user.username, password='pass12345')

        response = self.client.post(
            reverse('admin_telegram_broadcast'),
            data={
                'audience': 'single',
                'recipient': '',
                'message': 'Текст без адресата.',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Оберіть користувача, якому потрібно надіслати повідомлення.')

    def test_admin_can_add_home_hero_slide(self):
        User.objects.create_superuser(
            username='content-admin@test.local',
            email='content-admin@test.local',
            password='pass12345',
        )
        self.client.login(username='content-admin@test.local', password='pass12345')
        image = SimpleUploadedFile(
            'reception.gif',
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;',
            content_type='image/gif',
        )

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                reverse('admin_content'),
                data={
                    'action': 'save_hero',
                    'hero-title': 'Рецепція клініки',
                    'hero-image': image,
                    'hero-is_active': 'on',
                },
            )

            self.assertRedirects(
                response,
                f"{reverse('admin_content')}#hero-slides",
                fetch_redirect_response=False,
            )
            slide = HomeHeroSlide.objects.get()
            self.assertEqual(slide.title, 'Рецепція клініки')
            self.assertTrue(slide.is_active)
            self.assertEqual(slide.sort_order, 1)

    def test_admin_can_change_home_hero_slide_order(self):
        User.objects.create_superuser(
            username='slide-admin@test.local',
            email='slide-admin@test.local',
            password='pass12345',
        )
        first = HomeHeroSlide.objects.create(title='Перше', image='clinic/hero/first.jpg', sort_order=1)
        second = HomeHeroSlide.objects.create(title='Друге', image='clinic/hero/second.jpg', sort_order=2)
        self.client.login(username='slide-admin@test.local', password='pass12345')

        response = self.client.post(
            reverse('admin_content'),
            data={'action': 'move_hero', 'hero_id': second.id, 'direction': 'up'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(HomeHeroSlide.objects.values_list('id', flat=True)),
            [second.id, first.id],
        )

    def test_home_uses_only_active_admin_hero_slides(self):
        HomeHeroSlide.objects.create(
            title='Активний слайд',
            image='clinic/hero/active.jpg',
            is_active=True,
        )
        HomeHeroSlide.objects.create(
            title='Прихований слайд',
            image='clinic/hero/hidden.jpg',
            is_active=False,
        )

        response = self.client.get(reverse('home'))

        self.assertContains(response, '/media/clinic/hero/active.jpg')
        self.assertNotContains(response, '/media/clinic/hero/hidden.jpg')
        hero_markup = response.content.decode().split('<div class="home-hero-shade">', 1)[0]
        self.assertNotIn('hero-treatment-room.webp', hero_markup)

    def test_admin_content_screen_has_separate_editing_sections(self):
        User.objects.create_superuser(
            username='editor-admin@test.local',
            email='editor-admin@test.local',
            password='pass12345',
        )
        self.client.login(username='editor-admin@test.local', password='pass12345')

        response = self.client.get(reverse('admin_content'))

        self.assertContains(response, 'Верхні фотографії')
        self.assertContains(response, 'Додати у слайдер')
        self.assertContains(response, 'Оформлення клініки')
        self.assertContains(response, 'Новини')
        self.assertContains(response, 'Галерея')

    def test_home_renders_selected_background_effect(self):
        branding, _ = ClinicSettings.objects.get_or_create(pk=1)
        branding.home_effect = ClinicSettings.EFFECT_TEETH
        branding.save(update_fields=['home_effect'])

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'data-particle-effect="teeth"')

    def test_selected_background_is_used_on_internal_pages(self):
        branding, _ = ClinicSettings.objects.get_or_create(pk=1)
        branding.home_background = 'clinic/branding/site-background.jpg'
        branding.save(update_fields=['home_background'])
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('patient_dashboard'))

        self.assertContains(response, 'has-site-background')
        self.assertContains(response, '/media/clinic/branding/site-background.jpg')

    def test_home_renders_interactive_dental_field_preset(self):
        branding, _ = ClinicSettings.objects.get_or_create(pk=1)
        branding.home_effect = ClinicSettings.EFFECT_DENTAL_FIELD
        branding.save(update_fields=['home_effect'])

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'data-particle-effect="dental_field"')
        self.assertContains(response, 'clinic/home.js')
        home_script = (Path(settings.BASE_DIR) / 'static' / 'clinic' / 'home.js').read_text(encoding='utf-8')
        self.assertIn('pointermove', home_script)
        self.assertIn('110 : 260', home_script)
        self.assertIn('separateDentalParticles', home_script)

    def test_doctor_can_create_only_own_news(self):
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.post(
            reverse('doctor_news'),
            data={
                'title': 'Порада лікаря',
                'text': 'Корисний текст',
                'is_published': 'on',
            },
        )

        self.assertRedirects(response, reverse('doctor_news'))
        post = NewsPost.objects.get(title='Порада лікаря')
        self.assertEqual(post.doctor, self.doctor)

    def test_active_appointment_is_detected_by_start_and_end_time(self):
        fixed_now = timezone.make_aware(datetime(2026, 7, 14, 10, 10))
        self.schedule.weekday = fixed_now.date().weekday()
        self.schedule.slot_minutes = 20
        self.schedule.save(update_fields=['weekday', 'slot_minutes'])
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=fixed_now.date(),
            time='10:00',
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка визначення активного часу',
            duration_slots=2,
            status=Appointment.STATUS_APPROVED,
            approved_at=fixed_now,
        )

        with patch('clinic.views.timezone.localtime', return_value=fixed_now):
            active = active_appointment_for_doctor(self.doctor)

        self.assertEqual(active, appointment)

    def test_current_visit_opens_patient_records_before_full_card(self):
        fixed_now = timezone.make_aware(datetime(2026, 7, 20, 10, 30))
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=fixed_now.date(),
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Поточний прийом',
            duration_minutes_exact=60,
            status=Appointment.STATUS_APPROVED,
        )
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
        )
        entry = PatientRecordEntry.objects.create(
            card=card,
            doctor=self.doctor,
            appointment=appointment,
            kind=PatientRecordEntry.KIND_TREATMENT,
            title='План лікування',
            details='Запис лікаря про поточного пацієнта.',
            recommendations='Дотримуватися рекомендацій.',
        )
        self.client.login(username='doctor@test.local', password='pass12345')

        with patch('clinic.views.timezone.localtime', return_value=fixed_now):
            response = self.client.get(reverse('doctor_dashboard'))

        create_url = (
            f"{reverse('doctor_patient_card_detail', args=[card.id])}"
            f"?appointment={appointment.id}#new-entry"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['doctor_visit_card'], card)
        self.assertQuerySetEqual(response.context['doctor_visit_entries'], [entry])
        self.assertContains(response, 'current-visit-banner')
        self.assertContains(response, 'Прийом триває зараз')
        self.assertContains(response, 'Переглянути записи пацієнта')
        self.assertContains(response, 'Записи про Тест Пацієнт')
        self.assertContains(response, 'План лікування')
        self.assertContains(response, 'Запис лікаря про поточного пацієнта.')
        self.assertContains(response, 'Створити новий запис')
        self.assertContains(response, create_url)
        self.assertNotContains(response, 'Відкрити прийом і додати матеріали')

    def test_doctor_can_add_extended_patient_record(self):
        self.client.login(username='doctor@test.local', password='pass12345')
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
        )

        response = self.client.post(
            reverse('doctor_patient_card_detail', args=[card.id]),
            data={
                'action': 'add_entry',
                'kind': PatientRecordEntry.KIND_EXAMINATION,
                'title': 'Первинний огляд',
                'details': 'Стан пацієнта та результати огляду.',
                'recommendations': 'Повторний огляд через місяць.',
            },
        )

        self.assertRedirects(response, reverse('doctor_patient_card_detail', args=[card.id]))
        entry = PatientRecordEntry.objects.get(card=card)
        self.assertEqual(entry.doctor, self.doctor)
        self.assertEqual(entry.title, 'Первинний огляд')

    def test_patient_record_form_uses_ukrainian_labels(self):
        form = PatientRecordEntryForm()

        self.assertEqual(form.fields['kind'].label, 'Тип запису')
        self.assertEqual(form.fields['title'].label, 'Короткий заголовок')
        self.assertEqual(form.fields['details'].label, 'Детальна інформація')
        self.assertEqual(form.fields['recommendations'].label, 'Рекомендації пацієнту')

    def test_patient_sees_treatment_but_not_internal_doctor_note(self):
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
        )
        PatientRecordEntry.objects.create(
            card=card,
            doctor=self.doctor,
            kind=PatientRecordEntry.KIND_TREATMENT,
            title='План лікування',
            details='Інформація для пацієнта.',
            recommendations='Виконувати рекомендації лікаря.',
        )
        PatientRecordEntry.objects.create(
            card=card,
            doctor=self.doctor,
            kind=PatientRecordEntry.KIND_NOTE,
            title='Внутрішня нотатка',
            details='Це бачить лише лікар.',
        )
        self.client.login(username='patient@test.local', password='pass12345')

        response = self.client.get(reverse('patient_dashboard'))

        self.assertContains(response, 'План лікування')
        self.assertContains(response, 'Інформація для пацієнта.')
        self.assertNotContains(response, 'Внутрішня нотатка')

    def test_private_appointment_photo_requires_related_user(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Приватне фото',
        )
        relative_path = 'appointment_images/tests/private.gif'
        file_path = Path(settings.PRIVATE_MEDIA_ROOT) / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff'
            b'!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
            b'\x00\x00\x02\x02D\x01\x00;'
        )
        image = AppointmentImage.objects.create(appointment=appointment, image=relative_path)
        url = reverse('private_media', args=[relative_path])

        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertIn(self.client.get(f'/media/{relative_path}').status_code, {403, 404})

        unrelated = User.objects.create_user(username='unrelated@test.local', password='pass12345')
        Profile.objects.create(
            user=unrelated,
            role=Profile.ROLE_PATIENT,
            phone='+380503333333',
        )
        self.client.login(username='unrelated@test.local', password='pass12345')
        self.assertEqual(self.client.get(url).status_code, 403)

        appointment.patient = unrelated
        appointment.save(update_fields=['patient'])
        self.assertEqual(self.client.get(url).status_code, 403)
        appointment.patient = self.patient
        appointment.save(update_fields=['patient'])

        self.client.login(username='patient@test.local', password='pass12345')
        patient_response = self.client.get(url)
        self.assertEqual(patient_response.status_code, 200)
        patient_response.close()

        self.client.login(username='doctor@test.local', password='pass12345')
        doctor_response = self.client.get(url)
        self.assertEqual(doctor_response.status_code, 200)
        doctor_response.close()
        image.delete()

    def test_video_limits_and_signature_validation(self):
        valid_video = SimpleUploadedFile(
            'visit.mp4',
            b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42',
            content_type='video/mp4',
        )
        validate_video_upload(valid_video)

        invalid_video = SimpleUploadedFile(
            'visit.mp4',
            b'not a real video',
            content_type='video/mp4',
        )
        with self.assertRaisesMessage(ValidationError, 'Файл не містить підтримуваного відео'):
            validate_video_upload(invalid_video)

        oversized_video = SimpleUploadedFile(
            'large.mp4',
            b'\x00\x00\x00\x18ftypmp42',
            content_type='video/mp4',
        )
        oversized_video.size = MAX_VIDEO_BYTES + 1
        with self.assertRaisesMessage(ValidationError, 'Відео завелике'):
            validate_video_upload(oversized_video)

        too_many_videos = [
            SimpleUploadedFile(
                f'visit-{index}.mp4',
                b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42',
                content_type='video/mp4',
            )
            for index in range(3)
        ]
        with self.assertRaisesMessage(ValidationError, 'не більше 2 відео'):
            MultipleVideoField().clean(too_many_videos)

    def test_image_size_and_count_limits(self):
        gif_data = (
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff'
            b'!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
            b'\x00\x00\x02\x02D\x01\x00;'
        )
        oversized_image = SimpleUploadedFile(
            'large.gif',
            gif_data,
            content_type='image/gif',
        )
        oversized_image.size = MAX_IMAGE_BYTES + 1
        with self.assertRaisesMessage(ValidationError, 'Фотографія завелика'):
            validate_image_upload(oversized_image)

        too_many_images = [
            SimpleUploadedFile(
                f'photo-{index}.gif',
                gif_data,
                content_type='image/gif',
            )
            for index in range(7)
        ]
        with self.assertRaisesMessage(ValidationError, 'не більше 6 фотографій'):
            MultipleImageField().clean(too_many_images)

    def test_clinic_logo_accepts_safe_svg(self):
        logo = SimpleUploadedFile(
            'clinic-logo.svg',
            (
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                b'<defs><linearGradient id="g"><stop stop-color="#009b87"/></linearGradient></defs>'
                b'<circle cx="50" cy="50" r="45" fill="url(#g)"/>'
                b'</svg>'
            ),
            content_type='image/svg+xml',
        )

        validate_logo_upload(logo)
        form = ClinicSettingsForm(
            data={
                'clinic_name': 'LClinic',
                'home_effect': ClinicSettings.EFFECT_NONE,
            },
            files={'logo': logo},
            instance=ClinicSettings(),
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_clinic_logo_rejects_unsafe_svg(self):
        unsafe_logo = SimpleUploadedFile(
            'unsafe-logo.svg',
            (
                b'<svg xmlns="http://www.w3.org/2000/svg" '
                b'onload="alert(1)"><script>alert(1)</script></svg>'
            ),
            content_type='image/svg+xml',
        )

        with self.assertRaises(ValidationError):
            validate_logo_upload(unsafe_logo)

        oversized_logo = SimpleUploadedFile(
            'large-logo.svg',
            b'<svg xmlns="http://www.w3.org/2000/svg"/>',
            content_type='image/svg+xml',
        )
        oversized_logo.size = MAX_LOGO_BYTES + 1
        with self.assertRaisesMessage(ValidationError, 'Логотип завеликий'):
            validate_logo_upload(oversized_logo)

    def test_patient_profile_rejects_phone_used_by_another_patient(self):
        other_patient = User.objects.create_user(username='phone-owner@test.local')
        Profile.objects.create(
            user=other_patient,
            role=Profile.ROLE_PATIENT,
            phone='+380504444444',
        )
        form = PatientProfileForm(
            data={
                'first_name': self.patient.first_name,
                'last_name': self.patient.last_name,
                'age': 25,
                'phone': '+38 (050) 444-44-44',
            },
            user=self.patient,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

    def test_phone_validator_accepts_only_full_ukrainian_numbers(self):
        for phone in (
            '0501234567',
            '+380501234567',
            '+38 (050) 123-45-67',
        ):
            validate_ukrainian_phone(phone)

        for phone in (
            '050123456',
            '+3805012345678',
            '+480501234567',
            '+38050phone67',
        ):
            with self.subTest(phone=phone):
                with self.assertRaises(ValidationError):
                    validate_ukrainian_phone(phone)

        form = PatientProfileForm(
            data={
                'first_name': self.patient.first_name,
                'last_name': self.patient.last_name,
                'age': 25,
                'phone': '050 111 11 11',
            },
            user=self.patient,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '+380501111111')

    def test_common_text_and_number_limits_are_enforced(self):
        self.patient.profile.age = 121
        with self.assertRaises(ValidationError):
            self.patient.profile.full_clean()

        self.schedule.slot_minutes = 481
        with self.assertRaises(ValidationError):
            self.schedule.full_clean()

        appointment = Appointment(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() + timedelta(days=1),
            time=time(10, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Перевірка тривалості',
            duration_minutes_exact=MAX_APPOINTMENT_DURATION_MINUTES + 1,
        )
        with self.assertRaises(ValidationError):
            appointment.full_clean()

        reason_form = BookingReasonForm(
            data={
                'service': self.service.pk,
                'reason': 'x' * (MAX_REASON_LENGTH + 1),
            },
            doctor=self.doctor,
        )
        self.assertFalse(reason_form.is_valid())
        self.assertIn('reason', reason_form.errors)

    def test_admin_archives_user_without_deleting_medical_history(self):
        admin_user = User.objects.create_superuser(
            username='security-admin',
            email='admin@test.local',
            password='pass12345',
        )
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Історія має зберегтися',
        )
        self.client.login(username='security-admin', password='pass12345')

        self.client.post(reverse('admin_toggle_user', args=[self.patient.id]))

        self.patient.refresh_from_db()
        self.assertFalse(self.patient.is_active)
        self.assertTrue(User.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                actor=admin_user,
                action='Архівовано акаунт',
                target_id=str(self.patient.pk),
            ).exists()
        )

    def test_admin_can_permanently_delete_archived_patient_profile(self):
        admin_user = User.objects.create_superuser(
            username='delete-admin',
            email='delete-admin@test.local',
            password='pass12345',
        )
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() + timedelta(days=7),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Історія після видалення профілю',
        )
        card = DoctorPatientCard.objects.create(
            doctor=self.doctor,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
        )
        patient_id = self.patient.id
        self.patient.is_active = False
        self.patient.save(update_fields=['is_active'])
        self.client.login(username='delete-admin', password='pass12345')

        response = self.client.post(
            reverse('admin_delete_user', args=[patient_id]),
            follow=True,
        )

        self.assertFalse(User.objects.filter(pk=patient_id).exists())
        appointment.refresh_from_db()
        card.refresh_from_db()
        self.assertIsNone(appointment.patient)
        self.assertIsNone(card.patient)
        self.assertContains(response, 'Профіль пацієнта видалено назавжди')
        self.assertTrue(
            AuditLog.objects.filter(
                actor=admin_user,
                action='Назавжди видалено профіль пацієнта',
                target_id=str(patient_id),
            ).exists()
        )

    def test_admin_must_archive_patient_before_permanent_deletion(self):
        admin_user = User.objects.create_superuser(
            username='careful-admin',
            email='careful-admin@test.local',
            password='pass12345',
        )
        self.client.login(username='careful-admin', password='pass12345')

        response = self.client.post(
            reverse('admin_delete_user', args=[self.patient.id]),
            follow=True,
        )

        self.assertTrue(User.objects.filter(pk=self.patient.id).exists())
        self.assertContains(response, 'Спочатку перенесіть профіль пацієнта до архіву.')

    def test_past_appointment_cannot_be_canceled_by_direct_post(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            date=timezone.localdate() - timedelta(days=1),
            time=time(9, 0),
            city='Дніпро',
            address='вул. Тестова, 1',
            reason='Минулий прийом',
            status=Appointment.STATUS_APPROVED,
        )

        self.client.login(username='patient@test.local', password='pass12345')
        self.client.post(reverse('cancel_appointment', args=[appointment.id]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)

        self.client.login(username='doctor@test.local', password='pass12345')
        self.client.post(reverse('doctor_cancel_appointment', args=[appointment.id]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)

        User.objects.create_superuser(
            username='appointment-admin',
            email='appointment-admin@test.local',
            password='pass12345',
        )
        self.client.login(username='appointment-admin', password='pass12345')
        self.client.post(reverse('admin_cancel_appointment', args=[appointment.id]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)

    def test_administration_login_page_is_shared(self):
        response = self.client.get(reverse('administration_login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Вхід для адміністрації')
        self.assertContains(response, 'логін і пароль лікаря або адміністратора')

    def test_shared_administration_login_detects_doctor(self):
        response = self.client.post(
            reverse('administration_login'),
            data={'username': 'doctor@test.local', 'password': 'pass12345'},
        )

        self.assertRedirects(response, reverse('doctor_dashboard'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.doctor.user_id)

    def test_shared_administration_login_detects_admin(self):
        admin_user = User.objects.create_superuser(
            username='shared-login-admin',
            email='shared-login-admin@test.local',
            password='pass12345',
        )

        response = self.client.post(
            reverse('administration_login'),
            data={'username': admin_user.username, 'password': 'pass12345'},
        )

        self.assertRedirects(response, reverse('admin_panel'))
        self.assertEqual(int(self.client.session['_auth_user_id']), admin_user.id)

    def test_patient_cannot_use_shared_administration_login(self):
        response = self.client.post(
            reverse('administration_login'),
            data={'username': 'patient@test.local', 'password': 'pass12345'},
            follow=True,
        )

        self.assertContains(response, 'Цей акаунт не належить лікарю або адміністратору.')
        self.assertNotIn('_auth_user_id', self.client.session)
