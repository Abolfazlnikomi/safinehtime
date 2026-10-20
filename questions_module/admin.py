from django.contrib import admin
from django import forms
from users_module.models import Access
from users_module.curriculum_access import subjects_for_user
from .models import (
    Chapter, Question, Choice,
    PracticeSession, ExamSession, PracticeAnswer, ExamAnswer, Category, Topic, Lesson
)


def has_access(user, code, subject=None):
    if subject is not None:
        return subjects_for_user(user, code).filter(pk=subject.pk).exists()
    return user.is_authenticated and (user.is_superuser or subjects_for_user(user, code).exists())


class ChapterScopeAdminMixin:
    subject_path = 'chapter__subject'

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        if request.user.is_superuser:
            return filters
        return tuple(name for name in filters if isinstance(name, str) and '__' not in name and name not in {'subject', 'chapter', 'topic'})

    def get_queryset(self, request):
        return super().get_queryset(request).filter(**{f'{self.subject_path}__in': subjects_for_user(request.user)})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        subjects = subjects_for_user(request.user)
        if db_field.name == 'subject':
            kwargs['queryset'] = subjects
        elif db_field.name == 'chapter':
            kwargs['queryset'] = Chapter.objects.filter(subject__in=subjects)
        elif db_field.name == 'topic':
            kwargs['queryset'] = Topic.objects.filter(chapter__subject__in=subjects)
        elif db_field.name == 'lesson':
            kwargs['queryset'] = Lesson.objects.filter(chapter__subject__in=subjects)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ─────────────────────────────────────────────────────────────
# ۱. ثبت دسته‌بندی
# ─────────────────────────────────────────────────────────────
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)

    def has_add_permission(self, request):
        return has_access(request.user, Access.Code.BANK)

    def has_change_permission(self, request, obj=None):
        return has_access(request.user, Access.Code.BANK)


# ─────────────────────────────────────────────────────────────
# ۲. مدیریت درس‌ها و فصل‌ها
# ─────────────────────────────────────────────────────────────
@admin.register(Chapter)
class ChapterAdmin(ChapterScopeAdminMixin, admin.ModelAdmin):
    subject_path = 'subject'
    fields = ("subject", "name")
    list_display = ("name", "subject", "questions_count")
    list_filter = ("subject",)
    search_fields = ("name", "subject__title")

    def questions_count(self, obj):
        return obj.questions.count()
    questions_count.short_description = "تعداد سوالات"

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.role == 'ADMIN'

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.role == 'ADMIN'

    def has_view_permission(self, request, obj=None):
        return has_access(
            request.user,
            Access.Code.BANK,
            obj.subject if obj else None,
        )

    def has_change_permission(self, request, obj=None):
        return has_access(
            request.user,
            Access.Code.BANK,
            obj.subject if obj else None,
        )

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'subject':
            kwargs['queryset'] = subjects_for_user(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.filter(subject__in=subjects_for_user(request.user))


@admin.register(Topic)
class TopicAdmin(ChapterScopeAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'chapter', 'lesson', 'is_active')
    list_filter = ('is_active', 'chapter__subject')
    search_fields = ('name', 'chapter__name', 'chapter__subject__title')

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.role == 'ADMIN'

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(Lesson)
class LessonAdmin(ChapterScopeAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'chapter')
    search_fields = ('name', 'chapter__name', 'chapter__subject__title')

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.role == 'ADMIN'


# ─────────────────────────────────────────────────────────────
# ۳. گزینه‌های تستی و اعتبارسنجی
# ─────────────────────────────────────────────────────────────
class ChoiceInlineFormSet(forms.models.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        valid_choices = [
            form for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get('DELETE', False)
            and (
                form.cleaned_data.get('text', '').strip()
                or form.cleaned_data.get('image')
                or (form.instance.pk and form.instance.image)
            )
        ]

        if not valid_choices:
            return

        correct_count = sum(
            1 for form in valid_choices if form.cleaned_data.get('is_correct')
        )

        if len(valid_choices) != 4:
            raise forms.ValidationError("هر سؤال باید دقیقاً چهار گزینه داشته باشد.")

        if correct_count == 0:
            raise forms.ValidationError("لطفاً یک گزینه را به عنوان پاسخ صحیح انتخاب کنید.")
        elif correct_count > 1:
            raise forms.ValidationError("تنها یک گزینه می‌تواند پاسخ صحیح باشد.")


class ChoiceInline(admin.TabularInline):
    model = Choice
    formset = ChoiceInlineFormSet
    extra = 4
    min_num = 4
    max_num = 4
    fields = ("text", "image", "is_correct")


# ─────────────────────────────────────────────────────────────
# ۴. مدیریت سوالات
# ─────────────────────────────────────────────────────────────
@admin.register(Question)
class QuestionAdmin(ChapterScopeAdminMixin, admin.ModelAdmin):
    list_display = (
        "short_text", 
        "creator",
        "get_course", "chapter", "topic", "question_type",
        "difficulty", 
        "approval_status",
        "is_active", 
        "choices_status"
    )
    list_filter = (
        "is_active", 
        "approval_status",
        "difficulty", 
        "chapter__subject",
        "chapter"
    )
    search_fields = ("text", "explanation", "chapter__name", "chapter__subject__title")
    list_editable = ("is_active",)
    inlines = [ChoiceInline]
    save_on_top = True

    fieldsets = (
        ("اطلاعات دسته‌بندی", {
            "fields": (("chapter", "topic", "category"), ("question_type", "difficulty"), "approval_status", "is_active")
        }),
        ("محتوای سوال", {
            "fields": ("text", "image", "explanation")
        }),
        ("گردش تأیید", {
            "fields": ("creator", "approved_by", "approved_at", "approval_note"),
        }),
    )

    def short_text(self, obj):
        if not obj.text:
            return "سؤال تصویری"
        return obj.text[:60] + "..." if len(obj.text) > 60 else obj.text
    short_text.short_description = "متن سوال"

    def get_readonly_fields(self, request, obj=None):
        return ('creator', 'approved_by', 'approved_at')

    def get_course(self, obj):
        return obj.chapter.subject.title if obj.chapter and obj.chapter.subject else "-"
    get_course.short_description = "درس"
    get_course.admin_order_field = "chapter__subject__title"

    def has_add_permission(self, request):
        return has_access(request.user, Access.Code.BANK)

    def has_module_permission(self, request):
        return has_access(request.user, Access.Code.BANK)

    def has_view_permission(self, request, obj=None):
        return has_access(
            request.user,
            Access.Code.BANK,
            obj.chapter.subject if obj else None,
        )

    def has_change_permission(self, request, obj=None):
        return has_access(
            request.user,
            Access.Code.BANK,
            obj.chapter.subject if obj else None,
        )

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'chapter':
            chapters = Chapter.objects.filter(subject__in=subjects_for_user(request.user))
            kwargs['queryset'] = chapters.select_related('subject')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.filter(chapter__subject__in=subjects_for_user(request.user))

    def choices_status(self, obj):
        count = obj.choices.count()
        correct = obj.choices.filter(is_correct=True).count()
        return f"{count} گزینه ({correct} صحیح)"
    choices_status.short_description = "وضعیت گزینه‌ها"

    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, 'creator_id') and not obj.creator_id:
            obj.creator = request.user
        super().save_model(request, obj, form, change)


# ─────────────────────────────────────────────────────────────
# ۵. نشست‌های تمرین و آزمون
# ─────────────────────────────────────────────────────────────
@admin.register(PracticeSession)
class PracticeSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "percent", "correct_count", "wrong_count")
    list_filter = ("status",)
    readonly_fields = [f.name for f in PracticeSession._meta.fields]


@admin.register(ExamSession)
class ExamSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "percent", "correct_count", "wrong_count")
    list_filter = ("status", "auto_submitted")
    readonly_fields = [f.name for f in ExamSession._meta.fields]
