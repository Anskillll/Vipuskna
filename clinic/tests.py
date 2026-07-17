from datetime import datetime, time, timedelta
import tempfile
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Appointment,
    AppointmentImage,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    MedicalService,
    NewsPost,
    PatientRecordEntry,
    Profile,
    WorkSchedule,
)
from .forms import (
    BookingReasonForm,
    DoctorPatientBookingForm,
    DoctorProfileForm,
    PatientProfileForm,
    PatientRecordEntryForm,
)
from .views import active_appointment_for_doctor, appointment_conflicts


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
            price=500,
        )
        self.schedule = WorkSchedule.objects.create(
            doctor=self.doctor,
            weekday=timezone.localdate().weekday(),
            city='Дніпро',
            address='вул. Тестова, 1',
            start_time=time(9, 0),
            end_time=time(11, 0),
            slot_minutes=60,
        )

    def test_schedule_creates_hour_slots(self):
        slots = self.schedule.get_slots()

        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].strftime('%H:%M'), '09:00')
        self.assertEqual(slots[1].strftime('%H:%M'), '10:00')

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
        self.assertContains(response, 'Не можна записатися на минулу дату або час.')

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
        self.assertContains(response, 'Фотографії до заявки')

    def test_doctor_appointments_are_grouped_and_show_only_summary(self):
        first_date = timezone.localdate() + timedelta(days=7)
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

        response = self.client.get(reverse('doctor_appointments'))
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
        self.assertNotContains(response, 'Прихована адреса')
        self.assertNotContains(response, '+380509999991')

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

    def test_long_doctor_service_list_can_be_expanded(self):
        for index in range(4):
            MedicalService.objects.create(
                doctor=self.doctor,
                name=f'Додаткова послуга {index + 1}',
                price=600 + index,
            )

        response = self.client.get(reverse('doctors'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Показати всі послуги')
        self.assertContains(response, 'class="service-extra" hidden', count=2)
        self.assertContains(response, 'data-service-toggle')

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
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('google_login'), response.url)
        self.assertEqual(self.client.session['patient_claim_phone'], phone)

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
            first_name='Новий',
            last_name='Пацієнт',
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
        self.assertEqual(appointment.patient, google_user)
        self.assertEqual(card.patient, google_user)
        self.assertEqual(profile.phone, phone)
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
                        'phone': self.patient.profile.phone,
                        'photo': photo,
                    },
                )

                self.patient.profile.refresh_from_db()
                self.assertRedirects(response, reverse('patient_dashboard'))
                self.assertTrue(self.patient.profile.photo.name.startswith('patient_photos/'))

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
        self.client.login(username='doctor@test.local', password='pass12345')

        response = self.client.get(reverse('doctor_patient_cards'), {'q': 'Марія Коваль'})
        self.assertContains(response, 'Марія Коваль')
        self.assertNotContains(response, 'Олег Бондар')

        response = self.client.get(reverse('doctor_patient_cards'), {'q': '093 222 44 55'})
        self.assertContains(response, 'Олег Бондар')
        self.assertNotContains(response, 'Марія Коваль')

    def test_doctor_booking_recognizes_registered_patient_phone_format(self):
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

        appointment = Appointment.objects.get(reason='Перевірка наявного номера')
        self.assertEqual(appointment.patient, self.patient)
        self.assertEqual(appointment.patient_first_name, self.patient.first_name)
        self.assertEqual(appointment.patient_last_name, self.patient.last_name)
        self.assertContains(response, 'Номер уже належить зареєстрованому пацієнту')

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

    def test_home_renders_selected_background_effect(self):
        branding, _ = ClinicSettings.objects.get_or_create(pk=1)
        branding.home_effect = ClinicSettings.EFFECT_TEETH
        branding.save(update_fields=['home_effect'])

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'data-particle-effect="teeth"')

    def test_home_renders_interactive_dental_field_preset(self):
        branding, _ = ClinicSettings.objects.get_or_create(pk=1)
        branding.home_effect = ClinicSettings.EFFECT_DENTAL_FIELD
        branding.save(update_fields=['home_effect'])

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'data-particle-effect="dental_field"')
        self.assertContains(response, 'pointermove')
        self.assertContains(response, '110 : 260')
        self.assertContains(response, 'separateDentalParticles')

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
