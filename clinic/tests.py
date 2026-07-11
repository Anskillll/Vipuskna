from datetime import time, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, Doctor, MedicalService, NewsPost, Profile, WorkSchedule
from .views import appointment_conflicts


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
        appointment = Appointment.objects.create(
            doctor=self.doctor,
            service=self.service,
            patient=self.patient,
            patient_first_name='Тест',
            patient_last_name='Пацієнт',
            patient_phone='+380501111111',
            patient_email='patient@test.local',
            date=timezone.localdate(),
            time=(timezone.localtime() - timedelta(hours=2)).time().replace(second=0, microsecond=0),
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
