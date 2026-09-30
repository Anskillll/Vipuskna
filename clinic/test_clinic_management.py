from datetime import time, timedelta
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, AppointmentImage, AuditLog, Doctor, DoctorPatientCard, PatientRecordEntry, Profile, TelegramConnection
from . import tests as existing_tests


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ClinicManagementTests(TestCase):
    def setUp(self):
        existing_tests.ClinicModelTests.setUp(self)
        self.manager = User.objects.create_user('clinic-manager', password='ManagerTest!7264', first_name='Олена', last_name='Адміністратор')
        Profile.objects.create(user=self.manager, role=Profile.ROLE_CLINIC_ADMIN, phone='+380507777777')
        self.chief = User.objects.create_superuser('chief', password='ChiefTest!7264')
        self.date = timezone.localdate() + timedelta(days=7)
        self.client.force_login(self.manager)

    def appointment(self, **kwargs):
        data = dict(doctor=self.doctor, patient=self.patient, patient_first_name=self.patient.first_name,
                    patient_last_name=self.patient.last_name, patient_phone=self.patient.profile.phone,
                    date=self.date, time=time(9), service=self.service, status=Appointment.STATUS_PENDING)
        data.update(kwargs)
        return Appointment.objects.create(**data)

    def test_login_and_all_workspace_pages(self):
        self.client.logout()
        response = self.client.post(reverse('administration_login'), {'username': self.manager.username, 'password': 'ManagerTest!7264'})
        self.assertRedirects(response, reverse('clinic_dashboard'))
        self.appointment()
        for tab in ('schedule', 'requests', 'patients', 'doctors'):
            with self.subTest(tab=tab):
                response = self.client.get(reverse('clinic_dashboard'), {'tab': tab, 'date': self.date.isoformat()})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Олена Адміністратор')
                self.assertNotContains(response, 'Керуємо турботою')
                self.assertNotContains(response, '>Знайти</button>')
                self.assertNotContains(response, 'Скинути</a>')
                self.assertContains(response, 'data-clear-clinic-filter')
                self.assertNotContains(response, 'Контент сайту')
        for name in ('home', 'admin_add_doctor', 'admin_telegram_broadcast', 'clinic_admin_profile', 'clinic_admin_password'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Керувати клінікою')
        self.assertContains(response, 'account-request-count')
        self.assertNotContains(response, 'Кабінет пацієнта')

    def test_chief_creates_nonstaff_admin_with_validated_password(self):
        self.client.force_login(self.chief)
        data = {'username': 'new-manager', 'first_name': 'Ірина', 'last_name': 'Клініка', 'phone': '0501234567',
                'password1': 'Violet!74Turbine', 'password2': 'Violet!74Turbine', 'is_staff': '1', 'is_superuser': '1'}
        response = self.client.post(reverse('admin_add_clinic_admin'), data)
        self.assertRedirects(response, reverse('admin_panel'))
        user = User.objects.get(username='new-manager')
        self.assertEqual(user.profile.role, Profile.ROLE_CLINIC_ADMIN)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(data['password1']))
        self.assertTrue(AuditLog.objects.filter(actor=self.chief, action='Створено адміністратора клініки').exists())
        data.update(username='weak', password1='123', password2='123')
        self.assertEqual(self.client.post(reverse('admin_add_clinic_admin'), data).status_code, 200)
        self.assertFalse(User.objects.filter(username='weak').exists())
        data.update(username='NEW-MANAGER', password1='Violet!74Turbine', password2='Violet!74Turbine')
        self.client.post(reverse('admin_add_clinic_admin'), data)
        self.assertEqual(User.objects.filter(username__iexact='new-manager').count(), 1)

    def test_chief_moderator_dashboard_renders_with_existing_clinic_data(self):
        self.client.force_login(self.chief)
        self.appointment()
        AuditLog.objects.create(
            actor=None, action='Попередня дія', target_type='Запис',
            target_id='1', target_label='Історичний запис',
        )
        legacy = User.objects.create_user('legacy-without-profile', password='Irrelevant!9384')
        self.assertFalse(hasattr(legacy, 'profile'))
        for page in ('admin_panel', 'admin_content', 'admin_add_clinic_admin'):
            with self.subTest(page=page):
                response = self.client.get(reverse(page))
                self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новий адміністратор')

    def test_clinic_admin_can_add_home_news_and_gallery_without_site_settings_access(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Додати новину')
        self.assertContains(response, 'Додати фото')

        response = self.client.get(reverse('admin_content'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="action" value="save_news"')
        self.assertContains(response, 'name="action" value="save_gallery"')
        self.assertNotContains(response, 'Оформлення клініки')
        self.assertContains(response, 'Верхні фотографії')
        self.assertContains(response, 'name="action" value="save_hero"')

        response = self.client.post(reverse('admin_content'), {
            'action': 'save_news', 'news-title': 'Графік роботи',
            'news-text': 'У суботу клініка працює до 15:00.',
            'news-doctor': str(self.doctor.pk), 'news-is_published': 'on',
        })
        self.assertRedirects(response, f'{reverse("admin_content")}#news')
        post = existing_tests.NewsPost.objects.get(title='Графік роботи')
        self.assertEqual(post.doctor_id, self.doctor.pk)
        self.assertTrue(post.is_published)

        self.assertEqual(self.client.post(reverse('admin_content'), {'action': 'save_settings'}).status_code, 403)

    def test_admin_cannot_escalate_or_edit_other_users_or_doctor_settings(self):
        for name, args in [('admin_panel', []), ('admin_content', []), ('admin_add_clinic_admin', []),
                           ('admin_edit_user', [self.chief.id]), ('admin_toggle_user', [self.chief.id]),
                           ('admin_delete_user', [self.patient.id]), ('doctor_schedule', []),
                           ('doctor_edit_profile', []), ('doctor_services', [])]:
            with self.subTest(route=name):
                response = self.client.post(reverse(name, args=args), {'is_staff': '1', 'is_active': ''})
                self.assertIn(response.status_code, (302, 403))
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.chief.refresh_from_db()
        self.assertTrue(self.chief.is_active)
        self.assertTrue(User.objects.filter(pk=self.patient.pk).exists())
        self.manager.refresh_from_db()
        self.assertFalse(self.manager.is_staff)

    def test_patient_doctor_anonymous_and_inactive_cannot_access_management(self):
        self.client.logout()
        response = self.client.get(reverse('clinic_dashboard'))
        self.assertRedirects(response, reverse('administration_login'))
        for user in (self.patient, self.doctor.user):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for name in ('clinic_dashboard', 'clinic_admin_profile', 'admin_add_doctor', 'admin_telegram_broadcast'):
                with self.subTest(user=user, route=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.manager.is_active = False
        self.manager.save()
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('clinic_dashboard')).status_code, 302)

    @patch('clinic.views.notify_patient_status')
    def test_admin_books_registered_and_guest_patients_with_conflict_validation(self, notify):
        data = {'doctor': self.doctor.id, 'date': self.date.isoformat(), 'time': '09:00', 'patient': self.patient.id,
                'service': self.service.id, 'duration_minutes': 60, 'reason': 'Запис адміністратором'}
        response = self.client.post(reverse('doctor_book_patient'), data)
        self.assertRedirects(response, f'{reverse("clinic_dashboard")}?date={self.date.isoformat()}')
        self.assertEqual(Appointment.objects.get().patient, self.patient)
        self.client.post(reverse('doctor_book_patient'), data)
        self.assertEqual(Appointment.objects.count(), 1)
        data.update(patient='', first_name='Новий', last_name='Пацієнт', phone='0503334444', time='10:00')
        response = self.client.post(reverse('doctor_book_patient'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Appointment.objects.count(), 2)
        self.assertEqual(DoctorPatientCard.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(actor=self.manager, target_type=Appointment._meta.verbose_name).exists())
        data['time'] = '11:00'
        self.client.post(reverse('doctor_book_patient'), data)
        self.assertEqual(Appointment.objects.count(), 2)

    def test_booking_keeps_doctor_between_calendar_time_and_form(self):
        self.assertRedirects(self.client.get(reverse('doctor_book_patient')), reverse('clinic_dashboard') + '?tab=doctors')
        response = self.client.get(reverse('doctor_book_patient'), {'doctor': self.doctor.id, 'date': self.date.isoformat(), 'time': '09:00'})
        self.assertContains(response, f'name="doctor" value="{self.doctor.id}"', count=2)
        self.assertContains(response, f'&doctor={self.doctor.id}')
        self.assertEqual(self.client.post(reverse('doctor_book_patient'), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse('doctor_book_patient'), {'doctor': 'invalid'}).status_code, 403)

    def test_booking_from_card_prefills_patient(self):
        card = DoctorPatientCard.objects.create(doctor=self.doctor, patient_first_name='Анна', patient_last_name='Тест', patient_phone='+380502223344')
        response = self.client.get(reverse('doctor_book_patient'), {'doctor': self.doctor.id, 'card': card.id, 'date': self.date.isoformat(), 'time': '09:00'})
        self.assertContains(response, 'value="Анна"')
        self.assertContains(response, 'value="+380502223344"')
        self.assertContains(response, f'&card={card.id}')

    def test_admin_can_access_clinical_media_but_other_patient_cannot(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(PRIVATE_MEDIA_ROOT=media_root):
            appointment = self.appointment()
            image = AppointmentImage.objects.create(appointment=appointment, image=SimpleUploadedFile('test.png', b'test-image', content_type='image/png'))
            response = self.client.get(image.image.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'test-image')
            self.client.logout()
            self.assertEqual(self.client.get(image.image.url).status_code, 403)
            outsider = User.objects.create_user('outsider')
            Profile.objects.create(user=outsider, role=Profile.ROLE_PATIENT)
            self.client.force_login(outsider)
            self.assertEqual(self.client.get(image.image.url).status_code, 403)

    @patch('clinic.views.notify_patient_status')
    def test_admin_reviews_reschedules_and_cancels(self, notify):
        appointment = self.appointment()
        response = self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))
        self.assertContains(response, 'До кабінету адміністратора')
        self.client.post(reverse('doctor_review_appointment', args=[appointment.id]), {'action': 'approve', 'duration_minutes': 60})
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_APPROVED)
        response = self.client.get(reverse('doctor_propose_reschedule', args=[appointment.id]), {'date': self.date.isoformat(), 'duration_minutes': 60})
        self.assertEqual(response.json()['times'], ['09:00', '10:00'])
        self.client.post(reverse('doctor_propose_reschedule', args=[appointment.id]), {'date': self.date.isoformat(), 'time': '10:00', 'duration_minutes': 60})
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_RESCHEDULE_PROPOSED)
        self.client.post(reverse('doctor_cancel_appointment', args=[appointment.id]))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.STATUS_CANCELED)

    def test_records_have_admin_author_and_private_doctor_notes_are_protected(self):
        appointment = self.appointment()
        self.client.get(reverse('doctor_appointment_detail', args=[appointment.id]))
        card = DoctorPatientCard.objects.get()
        card.notes = 'Приватна нотатка лікаря 927'
        card.save()
        url = reverse('doctor_patient_card_detail', args=[card.id])
        response = self.client.get(url)
        self.assertNotContains(response, card.notes)
        self.assertEqual(self.client.post(url, {'action': 'update_card', 'notes': 'tampered'}).status_code, 403)
        response = self.client.post(url, {'action': 'add_entry', 'kind': 'note', 'title': 'Дзвінок', 'details': 'Пацієнт підтвердив візит', 'appointment_id': appointment.id})
        self.assertRedirects(response, url)
        entry = PatientRecordEntry.objects.get()
        self.assertEqual(entry.created_by, self.manager)
        self.assertEqual(entry.author_label, self.manager.get_full_name())
        self.assertEqual(entry.doctor, self.doctor)
        self.assertContains(self.client.get(url), entry.author_label)
        self.client.post(url, {'action': 'delete_entry', 'entry_id': entry.id})
        self.assertFalse(PatientRecordEntry.objects.exists())
        entry = PatientRecordEntry.objects.create(card=card, doctor=self.doctor, title='Лікарський запис', details='Details')
        self.assertEqual(self.client.post(url, {'action': 'delete_entry', 'entry_id': entry.id}).status_code, 403)
        card.refresh_from_db()
        self.assertEqual(card.notes, 'Приватна нотатка лікаря 927')

    def test_doctor_cannot_use_manager_scope_to_access_other_doctor(self):
        other_user = User.objects.create_user('other-doctor')
        other = Doctor.objects.create(user=other_user, specialization='Ортодонт')
        appointment = self.appointment(doctor=other)
        card = DoctorPatientCard.objects.create(doctor=other, patient=self.patient, patient_phone=self.patient.profile.phone)
        self.client.force_login(self.doctor.user)
        for name, ident in [('doctor_appointment_detail', appointment.id), ('doctor_patient_card_detail', card.id),
                            ('doctor_cancel_appointment', appointment.id), ('doctor_review_appointment', appointment.id)]:
            with self.subTest(route=name):
                self.assertEqual(self.client.post(reverse(name, args=[ident]), {'doctor': other.id}).status_code, 404)

    def test_profile_changes_do_not_change_role_or_other_users(self):
        response = self.client.post(reverse('clinic_admin_profile'), {'first_name': 'Нове', 'last_name': 'Ім’я', 'phone': '0507777777',
                                    'role': 'doctor', 'is_staff': '1', 'user': self.chief.id})
        self.assertRedirects(response, reverse('clinic_admin_profile'))
        self.manager.refresh_from_db()
        self.assertEqual(self.manager.first_name, 'Нове')
        self.assertEqual(self.manager.profile.role, Profile.ROLE_CLINIC_ADMIN)
        self.assertFalse(self.manager.is_staff)
        response = self.client.post(reverse('clinic_admin_password'), {'old_password': 'ManagerTest!7264', 'new_password1': 'NewPass!836423', 'new_password2': 'NewPass!836423'})
        self.assertRedirects(response, reverse('clinic_dashboard'))
        self.manager.refresh_from_db()
        self.assertTrue(self.manager.check_password('NewPass!836423'))

    def test_admin_can_create_doctor_but_not_staff(self):
        response = self.client.post(reverse('admin_add_doctor'), {'username': 'added-doctor', 'password': 'DoctorPass!9328',
                  'first_name': 'Іван', 'last_name': 'Новий', 'phone': '0502225566', 'specialization': 'Терапевт', 'is_staff': '1'})
        self.assertRedirects(response, reverse('clinic_dashboard'))
        user = User.objects.get(username='added-doctor')
        self.assertFalse(user.is_staff)
        self.assertEqual(user.profile.role, Profile.ROLE_DOCTOR)
        self.assertTrue(Doctor.objects.filter(user=user).exists())

    @override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_BOT_USERNAME='test-bot')
    @patch('clinic.views.send_admin_broadcast', return_value=(1, []))
    def test_admin_can_link_telegram_and_send_targeted_message(self, send):
        connection = TelegramConnection.objects.create(user=self.patient, chat_id=77211)
        response = self.client.get(reverse('telegram_connect'))
        self.assertTrue(response.url.startswith('https://t.me/'))
        response = self.client.post(reverse('admin_telegram_broadcast'), {'audience': 'single', 'recipient': connection.pk, 'message': 'Графік роботи'})
        self.assertRedirects(response, reverse('admin_telegram_broadcast'))
        self.assertEqual(send.call_args.args[0], [connection])
        self.assertTrue(AuditLog.objects.filter(actor=self.manager, action='Надіслано Telegram-повідомлення').exists())

    def test_dashboard_filters_and_pagination(self):
        self.appointment()
        for index in range(27):
            DoctorPatientCard.objects.create(doctor=self.doctor, patient_first_name='Пошук', patient_last_name=str(index), patient_phone=f'+38050{index:07d}')
        response = self.client.get(reverse('clinic_dashboard'), {'tab': 'patients', 'q': 'Пошук', 'doctor': self.doctor.pk, 'page': 2})
        self.assertEqual(len(response.context['page']), 2)
        response = self.client.get(reverse('clinic_dashboard'), {'tab': 'requests', 'q': 'missing'})
        self.assertEqual(response.context['page'].paginator.count, 0)
