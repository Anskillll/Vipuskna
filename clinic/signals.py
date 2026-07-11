from django.db.models import Q
from django.dispatch import receiver

from allauth.account.signals import user_signed_up

from .models import Appointment, Profile


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
