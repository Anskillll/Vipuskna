from .models import ClinicSettings


def clinic_branding(request):
    branding, _ = ClinicSettings.objects.get_or_create(pk=1)
    return {'clinic_branding': branding}
