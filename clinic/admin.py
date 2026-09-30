from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import (
    Appointment,
    AppointmentImage,
    AppointmentVideo,
    AuditLog,
    ClinicSettings,
    Doctor,
    DoctorPatientCard,
    DoctorWorkplace,
    GalleryImage,
    HomeHeroSlide,
    MedicalService,
    MedicalServiceImage,
    MedicalServiceVideo,
    NewsPost,
    PatientRecordEntry,
    PatientRecordImage,
    PatientRecordVideo,
    Profile,
    TelegramConnection,
    TelegramLoginChallenge,
    TelegramLinkToken,
    TelegramNotification,
    WorkSchedule,
)

admin.site.site_header = 'Адміністрування MedClinic'
admin.site.site_title = 'Адміністрування MedClinic'
admin.site.index_title = 'Керування клінікою'


class LockedAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.unregister(User)
admin.site.register(User, type('LockedUserAdmin', (LockedAdmin, UserAdmin), {}))


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


class AppointmentVideoInline(admin.TabularInline):
    model = AppointmentVideo
    extra = 0


class MedicalServiceImageInline(admin.TabularInline):
    model = MedicalServiceImage
    extra = 0


class MedicalServiceVideoInline(admin.TabularInline):
    model = MedicalServiceVideo
    extra = 0


@admin.register(Profile)
class ProfileAdmin(LockedAdmin):
    list_display = ('user', 'role', 'phone')
    list_filter = ('role',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'phone')


@admin.register(Doctor)
class DoctorAdmin(LockedAdmin):
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
class MedicalServiceAdmin(LockedAdmin):
    list_display = ('name', 'doctor', 'approximate_price', 'is_patient_selectable', 'sort_order')
    list_filter = ('doctor__specialization', 'is_patient_selectable')
    search_fields = ('name', 'doctor__user__last_name')
    inlines = [MedicalServiceImageInline, MedicalServiceVideoInline]


@admin.register(WorkSchedule)
class WorkScheduleAdmin(LockedAdmin):
    list_display = ('doctor', 'weekday', 'city', 'address', 'start_time', 'end_time', 'is_working')
    list_filter = ('weekday', 'city', 'is_working')


@admin.register(DoctorWorkplace)
class DoctorWorkplaceAdmin(LockedAdmin):
    list_display = ('name', 'doctor', 'city', 'address')
    list_filter = ('city',)
    search_fields = ('name', 'city', 'address', 'doctor__user__last_name')


@admin.register(Appointment)
class AppointmentAdmin(LockedAdmin):
    list_display = ('patient_display_name', 'doctor', 'date', 'time', 'city', 'status')
    list_filter = ('status', 'date', 'doctor__specialization', 'city')
    search_fields = (
        'patient_first_name',
        'patient_last_name',
        'patient_phone',
        'doctor__user__last_name',
    )
    inlines = [AppointmentImageInline, AppointmentVideoInline]

    @admin.display(description="Ім'я пацієнта")
    def patient_display_name(self, obj):
        return obj.patient_name


@admin.register(DoctorPatientCard)
class DoctorPatientCardAdmin(LockedAdmin):
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
    exclude = ('home_effect', 'particle_image')


@admin.register(HomeHeroSlide)
class HomeHeroSlideAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active', 'sort_order', 'created_at')
    list_editable = ('is_active', 'sort_order')
    list_filter = ('is_active',)


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


class PatientRecordVideoInline(admin.TabularInline):
    model = PatientRecordVideo
    extra = 0


@admin.register(PatientRecordEntry)
class PatientRecordEntryAdmin(LockedAdmin):
    list_display = ('title', 'card', 'doctor', 'kind', 'created_at')
    list_filter = ('kind', 'doctor')
    search_fields = ('title', 'details', 'card__patient_first_name', 'card__patient_last_name')
    inlines = [PatientRecordImageInline, PatientRecordVideoInline]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_label')
    list_filter = ('action', 'target_type', 'created_at')
    search_fields = ('actor__username', 'action', 'target_label', 'details')
    readonly_fields = (
        'actor',
        'action',
        'target_type',
        'target_id',
        'target_label',
        'details',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TelegramConnection)
class TelegramConnectionAdmin(admin.ModelAdmin):
    list_display = ('user', 'username', 'chat_id', 'is_active', 'linked_at')
    list_filter = ('is_active', 'linked_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'username', 'chat_id')
    readonly_fields = ('chat_id', 'username', 'first_name', 'linked_at', 'updated_at')


@admin.register(TelegramLinkToken)
class TelegramLinkTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'expires_at', 'used_at')
    readonly_fields = ('user', 'token', 'created_at', 'expires_at', 'used_at')

    def has_add_permission(self, request):
        return False


@admin.register(TelegramLoginChallenge)
class TelegramLoginChallengeAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'created_at', 'expires_at', 'consumed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    readonly_fields = (
        'user',
        'token',
        'status',
        'created_at',
        'expires_at',
        'resolved_at',
        'consumed_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(TelegramNotification)
class TelegramNotificationAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'recipient', 'kind', 'status', 'appointment')
    list_filter = ('status', 'kind', 'created_at')
    search_fields = ('recipient__username', 'appointment__patient_phone', 'event_key', 'error')
    readonly_fields = (
        'appointment',
        'recipient',
        'event_key',
        'kind',
        'status',
        'error',
        'created_at',
        'sent_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
