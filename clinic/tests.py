from datetime import datetime, time, timedelta
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
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
    DoctorWorkplace,
    MedicalService,
    MedicalServiceImage,
    NewsPost,
    PatientRecordEntry,
    Profile,
    WorkSchedule,
)
from .forms import (
    BookingReasonForm,
    DoctorPatientBookingForm,
    DoctorProfileForm,
    DoctorWorkplaceForm,
    PatientProfileForm,
    PatientRecordEntryForm,
    ServiceForm,
    UsernameLoginForm,
    WorkScheduleForm,
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
            age=25,
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
        self.assertContains(list_response, reverse('doctor_detail', args=[self.doctor.id]))
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
        self.assertContains(response, 'data-live-filter-input')
        self.assertContains(response, 'data-live-filter-item')
        self.assertContains(response, '+380509999991')

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

        appointments_response = self.client.get(reverse('doctor_appointments'))
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
        self.assertContains(patient_detail_response, 'Погодитися з новим часом')
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
        self.assertContains(response, 'clinic/home.js')

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
        appointment_response = self.client.get(reverse('doctor_appointments'))

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
