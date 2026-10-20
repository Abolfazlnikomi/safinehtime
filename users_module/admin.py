from django.contrib import admin
from .models import Province, School, Grade, FieldOfStudy, Subject, Access
from .curriculum_access import grades_for_user, subjects_for_user


class CurriculumScopeAdminMixin:
    def get_list_filter(self, request):
        return super().get_list_filter(request) if request.user.is_superuser else ('is_active',)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if self.model is Grade and not request.user.is_superuser:
            return (*fields, 'school')
        return fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        if self.model is Grade:
            return queryset.filter(pk__in=grades_for_user(request.user))
        if self.model is FieldOfStudy:
            return queryset.filter(grade__in=grades_for_user(request.user), is_active=True)
        return queryset.filter(pk__in=subjects_for_user(request.user))

    def get_inlines(self, request, obj):
        return super().get_inlines(request, obj) if request.user.is_superuser else []

    def has_add_permission(self, request):
        if self.model is Grade and not request.user.is_superuser:
            return False
        return super().has_add_permission(request)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'grade':
            kwargs['queryset'] = grades_for_user(request.user)
        elif db_field.name == 'field':
            kwargs['queryset'] = FieldOfStudy.objects.filter(grade__in=grades_for_user(request.user), is_active=True)
        elif db_field.name == 'school' and not request.user.is_superuser:
            kwargs['queryset'] = School.objects.filter(grades__in=grades_for_user(request.user)).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class GlobalStructureAdminMixin:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


# ==========================================
# Inlines for nested inline management
# ==========================================

class AccessInline(admin.TabularInline):
    model = Access
    extra = 1


class SubjectInline(admin.TabularInline):
    model = Subject
    extra = 1
    show_change_link = True


class FieldOfStudyInline(admin.TabularInline):
    model = FieldOfStudy
    extra = 1
    show_change_link = True


class GradeInline(admin.TabularInline):
    model = Grade
    extra = 1
    show_change_link = True


class SchoolInline(admin.TabularInline):
    model = School
    extra = 1
    show_change_link = True


# ==========================================
# Model Admins
# ==========================================

@admin.register(Province)
class ProvinceAdmin(GlobalStructureAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'name_display', 'schools_count')
    list_filter = ('name',)
    search_fields = ('name',)
    inlines = [SchoolInline]

    @admin.display(description='نام استان')
    def name_display(self, obj):
        return obj.get_name_display()

    @admin.display(description='تعداد مدارس')
    def schools_count(self, obj):
        return obj.schools.count()


@admin.register(School)
class SchoolAdmin(GlobalStructureAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'name', 'province_display', 'grades_count')
    list_filter = ('province',)
    search_fields = ('name', 'province__name')
    autocomplete_fields = ('province',)
    inlines = [GradeInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('province')

    @admin.display(description='استان', ordering='province__name')
    def province_display(self, obj):
        return obj.province.get_name_display()

    @admin.display(description='تعداد پایه‌ها')
    def grades_count(self, obj):
        return obj.grades.count()


@admin.register(Grade)
class GradeAdmin(CurriculumScopeAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'title', 'school', 'province_name', 'is_active')
    list_filter = ('is_active', 'school__province', 'school')
    search_fields = ('title', 'school__name')
    autocomplete_fields = ('school',)
    list_editable = ('is_active',)
    inlines = [FieldOfStudyInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('school__province')

    @admin.display(description='استان', ordering='school__province__name')
    def province_name(self, obj):
        return obj.school.province.get_name_display()


@admin.register(FieldOfStudy)
class FieldOfStudyAdmin(CurriculumScopeAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'title', 'grade', 'school_name', 'is_active')
    list_filter = ('is_active', 'grade__school__province', 'grade__school')
    search_fields = ('title', 'grade__title', 'grade__school__name')
    autocomplete_fields = ('grade',)
    list_editable = ('is_active',)
    inlines = [SubjectInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('grade__school__province')

    @admin.display(description='مدرسه', ordering='grade__school__name')
    def school_name(self, obj):
        return obj.grade.school.name


@admin.register(Subject)
class SubjectAdmin(CurriculumScopeAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'title', 'field', 'grade_title', 'school_name', 'is_active')
    list_filter = ('is_active', 'field__grade__school__province', 'field__grade__school')
    search_fields = ('title', 'field__title', 'field__grade__title', 'field__grade__school__name')
    autocomplete_fields = ('field',)
    list_editable = ('is_active',)
    inlines = [AccessInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'field__grade__school__province'
        )

    @admin.display(description='پایه', ordering='field__grade__title')
    def grade_title(self, obj):
        return obj.field.grade.title

    @admin.display(description='مدرسه', ordering='field__grade__school__name')
    def school_name(self, obj):
        return obj.field.grade.school.name


@admin.register(Access)
class AccessAdmin(admin.ModelAdmin):
    list_display = ('id', 'name_display', 'subject', 'subject_field', 'subject_grade', 'subject_school')
    list_filter = ('name', 'subject__field__grade__school__province')
    search_fields = ('name', 'subject__title')
    autocomplete_fields = ('subject',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'subject__field__grade__school__province'
        )

    @admin.display(description='نوع دسترسی', ordering='name')
    def name_display(self, obj):
        return obj.get_name_display()

    @admin.display(description='رشته', ordering='subject__field__title')
    def subject_field(self, obj):
        return obj.subject.field.title

    @admin.display(description='پایه', ordering='subject__field__grade__title')
    def subject_grade(self, obj):
        return obj.subject.field.grade.title

    @admin.display(description='مدرسه', ordering='subject__field__grade__school__name')
    def subject_school(self, obj):
        return obj.subject.field.grade.school.name
