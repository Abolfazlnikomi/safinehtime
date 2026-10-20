from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Consultant, ProvinceTrustee, Student, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("اطلاعات سامانه", {"fields": ("role", "phone_number", "gender", "allowed_grades")}),
        ("محدوده نظارت بر تیکت", {"fields": ("moderation_student_gender",)}),
    )
    list_display = ("username", "get_full_name", "role", "phone_number", "is_active")
    list_filter = UserAdmin.list_filter + ("role", "gender", "moderation_student_gender")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, 'allowed_grades')


@admin.register(Consultant)
class ConsultantScopeAdmin(admin.ModelAdmin):
    list_display = (
        "consultant", "accesses_display", "can_answer_psychology", "ticket_student_gender"
    )
    list_filter = ("can_answer_psychology", "accesses__name", "ticket_student_gender")
    filter_horizontal = ("accesses",)
    autocomplete_fields = ("consultant",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("accesses__subject")

    @admin.display(description="دسترسی‌ها")
    def accesses_display(self, obj):
        return "، ".join(str(access) for access in obj.accesses.all()) or "بدون دسترسی"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("user", "field")
    autocomplete_fields = ("user",)


@admin.register(ProvinceTrustee)
class ProvinceTrusteeAdmin(admin.ModelAdmin):
    list_display = ("user", "province")
    list_filter = ("province",)
    autocomplete_fields = ("user",)
