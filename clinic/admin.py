from django.contrib import admin

from .models import (
    Appointment,
    AppointmentImage,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    DoctorWorkplace,
    GalleryImage,
    MedicalService,
    MedicalServiceImage,
    NewsPost,
    PatientRecordEntry,
    PatientRecordImage,
    Profile,
    WorkSchedule,
)

admin.site.site_header = 'Адміністрування MedClinic'
admin.site.site_title = 'Адміністрування MedClinic'
admin.site.index_title = 'Керування клінікою'


class MedicalServiceInline(admin.TabularInline):
    model = MedicalService
    extra = 1


class WorkScheduleInline(admin.TabularInline):
    model = WorkSchedule
    extra = 1


class DoctorWorkplaceInline(admin.TabularInline):
    model = DoctorWorkplace
    extra = 1


class AppointmentImageInline(admin.TabularInline):
    model = AppointmentImage
    extra = 0


class MedicalServiceImageInline(admin.TabularInline):
    model = MedicalServiceImage
    extra = 0


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone')
    list_filter = ('role',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'phone')


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ('doctor_name', 'specialization', 'phone', 'is_active')
    search_fields = ('user__first_name', 'user__last_name', 'specialization', 'phone')
    inlines = [DoctorWorkplaceInline, MedicalServiceInline, WorkScheduleInline]

    @admin.display(description="Ім'я лікаря")
    def doctor_name(self, obj):
        return obj.full_name

    @admin.display(boolean=True, description='Активний')
    def is_active(self, obj):
        return obj.user.is_active


@admin.register(MedicalService)
class MedicalServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'doctor', 'approximate_price', 'is_patient_selectable', 'sort_order')
    list_filter = ('doctor__specialization', 'is_patient_selectable')
    search_fields = ('name', 'doctor__user__last_name')
    inlines = [MedicalServiceImageInline]


@admin.register(WorkSchedule)
class WorkScheduleAdmin(admin.ModelAdmin):
    list_display = ('doctor', 'weekday', 'city', 'address', 'start_time', 'end_time', 'is_working')
    list_filter = ('weekday', 'city', 'is_working')


@admin.register(DoctorWorkplace)
class DoctorWorkplaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'doctor', 'city', 'address')
    list_filter = ('city',)
    search_fields = ('name', 'city', 'address', 'doctor__user__last_name')


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ('patient_display_name', 'doctor', 'date', 'time', 'city', 'status')
    list_filter = ('status', 'date', 'doctor__specialization', 'city')
    search_fields = (
        'patient_first_name',
        'patient_last_name',
        'patient_phone',
        'doctor__user__last_name',
    )
    inlines = [AppointmentImageInline]

    @admin.display(description="Ім'я пацієнта")
    def patient_display_name(self, obj):
        return obj.patient_name


@admin.register(DoctorPatientCard)
class DoctorPatientCardAdmin(admin.ModelAdmin):
    list_display = ('patient_display_name', 'doctor', 'patient_phone', 'patient_email', 'updated_at')
    list_filter = ('doctor__specialization',)
    search_fields = (
        'patient_first_name',
        'patient_last_name',
        'patient_phone',
        'patient_email',
        'doctor__user__last_name',
    )

    @admin.display(description="Ім'я пацієнта")
    def patient_display_name(self, obj):
        return obj.full_name


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


class PatientRecordImageInline(admin.TabularInline):
    model = PatientRecordImage
    extra = 0


@admin.register(PatientRecordEntry)
class PatientRecordEntryAdmin(admin.ModelAdmin):
    list_display = ('title', 'card', 'doctor', 'kind', 'created_at')
    list_filter = ('kind', 'doctor')
    search_fields = ('title', 'details', 'card__patient_first_name', 'card__patient_last_name')
    inlines = [PatientRecordImageInline]
