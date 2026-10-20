from django import forms
from django.forms import inlineformset_factory
from ckeditor_uploader.widgets import CKEditorUploadingWidget

from users_module.models import Access, FieldOfStudy, Grade, Subject
from users_module.curriculum_access import subjects_for_user
from users_module.school_structure import is_general_grade

from .models import Chapter, Choice, Question, Topic


FORM_CONTROL = {"class": "form-control"}
FORM_SELECT = {"class": "form-select"}


class QuestionForm(forms.ModelForm):
    """فرم سؤال با مسیر پایه ← رشته ← درس ← فصل ← مبحث."""

    grade = forms.ModelChoiceField(label="پایه", queryset=Grade.objects.none(), widget=forms.Select(attrs=FORM_SELECT))
    field = forms.ModelChoiceField(label="رشته", queryset=FieldOfStudy.objects.none(), widget=forms.Select(attrs=FORM_SELECT))
    subject = forms.ModelChoiceField(label="درس", queryset=Subject.objects.none(), widget=forms.Select(attrs=FORM_SELECT))
    chapter = forms.ModelChoiceField(label="فصل", queryset=Chapter.objects.none(), widget=forms.Select(attrs=FORM_SELECT))

    class Meta:
        model = Question
        fields = ["question_type", "grade", "field", "subject", "chapter", "topic", "difficulty", "text", "image", "explanation", "is_active"]
        labels = {
            "question_type": "نوع سؤال", "topic": "مبحث", "difficulty": "سطح دشواری",
            "text": "متن سؤال", "image": "تصویر سؤال (اختیاری)",
            "explanation": "پاسخ تشریحی", "is_active": "فعال باشد؟",
        }
        widgets = {
            "question_type": forms.HiddenInput(), "topic": forms.Select(attrs=FORM_SELECT),
            "difficulty": forms.Select(attrs=FORM_SELECT), "text": CKEditorUploadingWidget(),
            "explanation": CKEditorUploadingWidget(),
            "image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/jpeg,image/png,image/webp"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        subjects = Subject.objects.filter(is_active=True).select_related("field__grade")
        subjects = subjects_for_user(user) if user is not None else subjects.none()
        subject_ids = subjects.values_list("id", flat=True)
        fields = FieldOfStudy.objects.filter(is_active=True, subjects__id__in=subject_ids).distinct()
        grades = Grade.objects.filter(is_active=True, fields__id__in=fields.values_list("id", flat=True)).distinct()
        chapters = Chapter.objects.filter(subject_id__in=subject_ids).select_related("subject")
        topics = Topic.objects.filter(is_active=True, chapter__subject_id__in=subject_ids).select_related("chapter__subject")
        self.fields["grade"].queryset = grades.order_by("title")
        self.fields['grade'].label_from_instance = lambda grade: f'{grade.title} · {grade.school.name}'
        self.fields["field"].queryset = fields.order_by("title")
        self.fields["subject"].queryset = subjects.order_by("title")
        self.fields["chapter"].queryset = chapters.order_by("name")
        self.fields["topic"].queryset = topics.order_by("name")
        if self.instance and self.instance.topic_id:
            chapter = self.instance.topic.chapter
            self.initial.update({"topic": self.instance.topic_id, "chapter": chapter.pk,
                                 "subject": chapter.subject_id, "field": chapter.subject.field_id,
                                 "grade": chapter.subject.field.grade_id})
        grade_id = self.data.get('grade') if self.is_bound else self.initial.get('grade')
        selected_grade = grades.filter(pk=grade_id).first() if str(grade_id or '').isdecimal() else None
        if selected_grade and is_general_grade(selected_grade):
            self.fields['field'].disabled = True
            self.fields['field'].required = False
            self.initial['field'] = fields.filter(grade=selected_grade, title='عمومی').values_list('pk', flat=True).first()

    def clean(self):
        cleaned = super().clean()
        grade, field = cleaned.get("grade"), cleaned.get("field")
        subject, chapter, topic = cleaned.get("subject"), cleaned.get("chapter"), cleaned.get("topic")
        if field and grade and field.grade_id != grade.pk:
            self.add_error("field", "رشته انتخاب‌شده متعلق به این پایه نیست.")
        if subject and field and subject.field_id != field.pk:
            self.add_error("subject", "درس انتخاب‌شده متعلق به این رشته نیست.")
        if chapter and subject and chapter.subject_id != subject.pk:
            self.add_error("chapter", "فصل انتخاب‌شده متعلق به این درس نیست.")
        if topic and chapter and topic.chapter_id != chapter.pk:
            self.add_error("topic", "مبحث انتخاب‌شده متعلق به این فصل نیست.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.chapter = self.cleaned_data["chapter"]
        instance.topic = self.cleaned_data["topic"]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ChapterForm(forms.ModelForm):
    class Meta:
        model = Chapter
        fields = ("subject", "name")
        widgets = {"subject": forms.Select(attrs=FORM_SELECT), "name": forms.TextInput(attrs={**FORM_CONTROL, "placeholder": "نام فصل"})}


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ("chapter", "lesson", "name", "is_active")
        widgets = {"chapter": forms.Select(attrs=FORM_SELECT), "name": forms.TextInput(attrs={**FORM_CONTROL, "placeholder": "نام مبحث"}), "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"})}


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ("title", "field", "is_active")
        widgets = {"title": forms.TextInput(attrs={**FORM_CONTROL, "placeholder": "نام درس"}), "field": forms.Select(attrs=FORM_SELECT), "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"})}


class FieldForm(forms.ModelForm):
    class Meta:
        model = FieldOfStudy
        fields = ("title", "grade", "is_active")
        widgets = {"title": forms.TextInput(attrs={**FORM_CONTROL, "placeholder": "نام رشته"}), "grade": forms.Select(attrs=FORM_SELECT), "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"})}


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ("title", "school", "is_active")
        widgets = {"title": forms.TextInput(attrs={**FORM_CONTROL, "placeholder": "نام پایه"}), "school": forms.Select(attrs=FORM_SELECT), "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"})}


class BaseChoiceFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors) or self.instance.question_type == Question.Type.DESCRIPTIVE:
            return
        active_forms = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE", False)
                        and (form.cleaned_data.get("text", "").strip() or form.cleaned_data.get("image") or (form.instance.pk and form.instance.image))]
        correct_answers = [form for form in active_forms if form.cleaned_data.get("is_correct")]
        if len(active_forms) != 4:
            raise forms.ValidationError("هر سؤال تستی باید دقیقاً چهار گزینه داشته باشد.")
        if len(correct_answers) != 1:
            raise forms.ValidationError("دقیقاً یک گزینه باید به‌عنوان پاسخ صحیح انتخاب شود.")


ChoiceFormSet = inlineformset_factory(
    Question, Choice, formset=BaseChoiceFormSet, fields=("text", "image", "is_correct"),
    extra=4, min_num=0, max_num=4, validate_min=False, validate_max=True, can_delete=True,
    labels={"text": "متن گزینه", "is_correct": "گزینه صحیح", "image": "تصویر گزینه (اختیاری)"},
    widgets={"text": CKEditorUploadingWidget(), "is_correct": forms.CheckboxInput(attrs={"class": "form-check-input"}),
             "image": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/jpeg,image/png,image/webp"})},
)
