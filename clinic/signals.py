from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver

from allauth.account.signals import user_signed_up

from .models import (
    Appointment,
    AppointmentImage,
    AppointmentVideo,
    MedicalServiceImage,
    MedicalServiceVideo,
    PatientRecordImage,
    PatientRecordVideo,
    Profile,
)


@receiver(user_signed_up)
def create_patient_profile_on_signup(request, user, sociallogin=None, **kwargs):
    if user.is_staff:
        return

    Profile.objects.get_or_create(
        user=user,
        defaults={
            'role': Profile.ROLE_PATIENT,
            'phone': '',
        },
    )

    if user.email:
        Appointment.objects.filter(patient__isnull=True).filter(
            Q(patient_email__iexact=user.email)
        ).update(patient=user)


@receiver(post_delete, sender=AppointmentImage)
@receiver(post_delete, sender=AppointmentVideo)
@receiver(post_delete, sender=PatientRecordImage)
@receiver(post_delete, sender=PatientRecordVideo)
@receiver(post_delete, sender=MedicalServiceImage)
@receiver(post_delete, sender=MedicalServiceVideo)
def delete_attachment_file(sender, instance, **kwargs):
    field = getattr(instance, 'image', None) or getattr(instance, 'video', None)
    if field and field.name:
        field.storage.delete(field.name)
