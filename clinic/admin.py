from django.contrib import admin

from .models import (
    Appointment,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    GalleryImage,
    MedicalService,
    NewsPost,
    Profile,
    WorkSchedule,
)


class MedicalServiceInline(admin.TabularInline):
    model = MedicalService
    extra = 1


class WorkScheduleInline(admin.TabularInline):
    model = WorkSchedule
    extra = 1


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone')
    list_filter = ('role',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'phone')


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'specialization', 'phone', 'is_active')
    search_fields = ('user__first_name', 'user__last_name', 'specialization', 'phone')
    inlines = [MedicalServiceInline, WorkScheduleInline]

    @admin.display(boolean=True, description='Активен')
    def is_active(self, obj):
        return obj.user.is_active


@admin.register(MedicalService)
class MedicalServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'doctor', 'price')
    list_filter = ('doctor__specialization',)
    search_fields = ('name', 'doctor__user__last_name')


@admin.register(WorkSchedule)
class WorkScheduleAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'weekday', 'city', 'address', 'start_time', 'end_time', 'is_working')
    list_filter = ('weekday', 'city', 'is_working')


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient_name', 'doctor', 'date', 'time', 'city', 'status')
    list_filter = ('status', 'date', 'doctor__specialization', 'city')
    search_fields = (
        'patient_first_name',
        'patient_last_name',
        'patient_phone',
        'doctor__user__last_name',
    )


@admin.register(DoctorPatientCard)
class DoctorPatientCardAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'doctor', 'patient_phone', 'patient_email', 'updated_at')
    list_filter = ('doctor__specialization',)
    search_fields = (
        'patient_first_name',
        'patient_last_name',
        'patient_phone',
        'patient_email',
        'doctor__user__last_name',
    )


@admin.register(ClinicSettings)
class ClinicSettingsAdmin(admin.ModelAdmin):
    list_display = ('clinic_name',)


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'doctor', 'is_published', 'created_at')
    list_filter = ('is_published', 'doctor')
    search_fields = ('title', 'text')


@admin.register(GalleryImage)
class GalleryImageAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_published', 'created_at')
    list_filter = ('is_published',)
