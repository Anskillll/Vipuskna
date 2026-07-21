from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clinic.models import (
    Appointment,
    AuditLog,
    Doctor,
    DoctorPatientCard,
    GalleryImage,
    NewsPost,
    Profile,
)


class Command(BaseCommand):
    help = (
        'Видаляє демонстраційні дані та залишає одного адміністратора, '
        'одного лікаря і одного пацієнта.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--admin-username', default='admin')
        parser.add_argument('--doctor-username', default='Lina')
        parser.add_argument('--patient-username', default='linadentis1@gmail.com')
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Підтвердити незворотне видалення. Без цього параметра дані не змінюються.',
        )

    def handle(self, *args, **options):
        admin_user = self._get_user(options['admin_username'], 'адміністратора')
        doctor_user = self._get_user(options['doctor_username'], 'лікаря')
        patient_user = self._get_user(options['patient_username'], 'пацієнта')

        if not admin_user.is_staff:
            raise CommandError(f'{admin_user.username} не є адміністратором.')

        try:
            doctor = doctor_user.doctor_profile
        except Doctor.DoesNotExist as error:
            raise CommandError(f'{doctor_user.username} не має профілю лікаря.') from error

        try:
            patient_profile = patient_user.profile
        except Profile.DoesNotExist as error:
            raise CommandError(f'{patient_user.username} не має профілю пацієнта.') from error

        if patient_profile.role != Profile.ROLE_PATIENT:
            raise CommandError(f'{patient_user.username} не є пацієнтом.')

        kept_user_ids = {admin_user.pk, doctor_user.pk, patient_user.pk}
        kept_appointments = Appointment.objects.filter(
            doctor=doctor,
            patient=patient_user,
        )
        kept_cards = DoctorPatientCard.objects.filter(
            doctor=doctor,
            patient=patient_user,
        )

        summary = {
            'users': User.objects.exclude(pk__in=kept_user_ids).count(),
            'appointments': Appointment.objects.exclude(pk__in=kept_appointments).count(),
            'cards': DoctorPatientCard.objects.exclude(pk__in=kept_cards).count(),
            'news': NewsPost.objects.count(),
            'gallery': GalleryImage.objects.count(),
            'audit': AuditLog.objects.count(),
        }
        self._write_summary(summary)

        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                'Попередній перегляд: нічого не видалено. Додайте --apply для очищення.'
            ))
            return

        public_files = self._collect_public_files(
            User.objects.exclude(pk__in=kept_user_ids),
            NewsPost.objects.all(),
            GalleryImage.objects.all(),
        )

        with transaction.atomic():
            Appointment.objects.exclude(pk__in=kept_appointments).delete()
            DoctorPatientCard.objects.exclude(pk__in=kept_cards).delete()
            NewsPost.objects.all().delete()
            GalleryImage.objects.all().delete()
            User.objects.exclude(pk__in=kept_user_ids).delete()
            AuditLog.objects.all().delete()
            AuditLog.objects.create(
                actor=admin_user,
                action='Очищено демонстраційні дані',
                target_type='система',
                target_label='Залишено адміністратора, Ліну Віниченко та Іларіона Віниченка',
                details=(
                    f'Видалено користувачів: {summary["users"]}; '
                    f'прийомів і заявок: {summary["appointments"]}; '
                    f'карток пацієнтів: {summary["cards"]}; '
                    f'новин: {summary["news"]}; фото галереї: {summary["gallery"]}.'
                ),
            )

        for storage, name in public_files:
            if name:
                storage.delete(name)

        self.stdout.write(self.style.SUCCESS(
            'Готово. Залишено admin, Ліну Віниченко, Іларіона Віниченка та їхні спільні дані.'
        ))

    @staticmethod
    def _get_user(username, label):
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist as error:
            raise CommandError(f'Не знайдено {label} з логіном {username}.') from error

    def _write_summary(self, summary):
        self.stdout.write('Буде видалено:')
        self.stdout.write(f'  користувачів: {summary["users"]}')
        self.stdout.write(f'  прийомів і заявок: {summary["appointments"]}')
        self.stdout.write(f'  карток пацієнтів: {summary["cards"]}')
        self.stdout.write(f'  новин: {summary["news"]}')
        self.stdout.write(f'  фото галереї: {summary["gallery"]}')
        self.stdout.write(f'  старих записів журналу: {summary["audit"]}')

    @staticmethod
    def _collect_public_files(users, news_posts, gallery_images):
        files = []
        for user in users.select_related('profile', 'doctor_profile'):
            profile = getattr(user, 'profile', None)
            doctor = getattr(user, 'doctor_profile', None)
            if profile and profile.photo:
                files.append((profile.photo.storage, profile.photo.name))
            if doctor and doctor.photo:
                files.append((doctor.photo.storage, doctor.photo.name))

        for post in news_posts:
            if post.image:
                files.append((post.image.storage, post.image.name))
        for gallery in gallery_images:
            if gallery.image:
                files.append((gallery.image.storage, gallery.image.name))
        return files
