from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .models import Profile


User = get_user_model()


class ClinicSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        email = (sociallogin.user.email or '').lower().strip()
        if not email:
            return

        existing_user = User.objects.filter(email__iexact=email).first()
        if not existing_user:
            return

        if existing_user.is_staff:
            messages.error(
                request,
                'Адміністратор входить лише за логіном і паролем, не через Google.',
            )
            raise ImmediateHttpResponse(redirect('home'))

        profile = getattr(existing_user, 'profile', None)
        if profile and profile.role in {Profile.ROLE_DOCTOR, Profile.ROLE_CLINIC_ADMIN}:
            messages.error(
                request,
                'Лікар входить лише за логіном і паролем, не через Google.',
            )
            raise ImmediateHttpResponse(redirect('home'))

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        if user.email:
            user.username = user.email.lower()
            user.save(update_fields=['username'])
        return user
