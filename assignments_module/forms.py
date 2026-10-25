from datetime import datetime

from django import forms
from django.utils import timezone

from student_records.fields import ShamsiDateField
from .audience import audience_catalog, matching_student_ids
from .models import Assignment, Submission
from .permissions import students_for


def validate_upload(upload):
    if upload.size > 50 * 1024 * 1024:
        raise forms.ValidationError('حجم فایل نباید بیشتر از ۵۰ مگابایت باشد.')
    if not upload.size:
        raise forms.ValidationError('فایل خالی قابل ارسال نیست.')
    return upload


class AssignmentForm(forms.ModelForm):
    province = forms.ChoiceField(label='استان', required=False)
    grade = forms.ChoiceField(label='پایه تحصیلی', required=False)
    field_of_study = forms.ChoiceField(label='رشته تحصیلی', required=False)
    subject = forms.ChoiceField(label='درس', required=False)
    student_search = forms.CharField(label='جست‌وجوی دانش‌آموز', required=False, max_length=100, widget=forms.TextInput(attrs={'placeholder': 'نام، نام کاربری یا مدرسه…', 'data-audience-search': ''}))
    send_to_all = forms.BooleanField(label='ارسال برای همه', required=False, widget=forms.CheckboxInput(attrs={'data-audience-all': ''}))
    due_date = ShamsiDateField(label='تاریخ پایان (شمسی)')
    due_time = forms.TimeField(label='ساعت پایان (به وقت تهران)', widget=forms.TimeInput(attrs={'type': 'time'}))
    audience_filter_names = ('province', 'grade', 'field_of_study', 'subject')
    audience_control_names = (*audience_filter_names, 'student_search', 'send_to_all')

    class Meta:
        model = Assignment
        fields = ['title', 'description', 'attachment', 'students', 'max_score']
        widgets = {'description': forms.Textarea(attrs={'rows': 5}), 'students': forms.CheckboxSelectMultiple}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['students'].queryset = students_for(user)
        self.fields['students'].required = False
        self.fields['students'].label_from_instance = lambda student: f'{student.user.get_full_name() or student.user.username} · {student.field.grade.title} · {student.field.title} · {student.field.grade.school.name}'
        self.audience_catalog = audience_catalog(self.fields['students'].queryset)
        self.fields['province'].choices = [('', 'همه استان‌ها')] + [(item['value'], item['label']) for item in self.audience_catalog['provinces']]
        for name, key, label in (
            ('grade', 'grades', 'همه پایه‌ها'),
            ('field_of_study', 'fields', 'همه رشته‌ها'),
            ('subject', 'subject_titles', 'همه درس‌ها'),
        ):
            self.fields[name].choices = [('', label)] + [(value, value) for value in self.audience_catalog[key]]
        for name in self.audience_filter_names:
            self.fields[name].widget.attrs['data-audience-filter'] = name
        self.fields['attachment'].widget = forms.FileInput()
        self.fields['due_date'].widget.attrs['data-jdp'] = ''
        self.fields['attachment'].help_text = 'هر نوع فایل، حداکثر ۵۰ مگابایت؛ فایل‌ها فقط از مسیر امن دانلود می‌شوند.'
        if self.instance.pk:
            local_deadline = timezone.localtime(self.instance.deadline)
            self.initial.update(due_date=local_deadline.date(), due_time=local_deadline.time())

    @property
    def audience_filters(self):
        return [self[name] for name in self.audience_filter_names]

    def clean_attachment(self):
        return validate_upload(self.cleaned_data['attachment'])

    def clean(self):
        cleaned = super().clean()
        if not any(name in self.errors for name in self.audience_control_names):
            matching_ids = matching_student_ids(self.audience_catalog, cleaned)
            recipients = self.fields['students'].queryset.filter(pk__in=matching_ids)
            if cleaned.get('send_to_all'):
                if not matching_ids:
                    self.add_error('students', 'هیچ دانش‌آموزی با فیلترهای انتخاب‌شده در محدوده دسترسی شما پیدا نشد.')
                else:
                    cleaned['students'] = recipients
            elif 'students' not in self.errors:
                selected = cleaned.get('students')
                if selected is None or not selected.exists():
                    self.add_error('students', 'حداقل یک دانش‌آموز را انتخاب کنید یا گزینه «ارسال برای همه» را فعال کنید.')
                elif not set(map(str, selected.values_list('pk', flat=True))).issubset(matching_ids):
                    self.add_error('students', 'دانش‌آموزان انتخاب‌شده باید با فیلترهای فعلی مطابقت داشته باشند.')
        if cleaned.get('due_date') and cleaned.get('due_time'):
            deadline = timezone.make_aware(datetime.combine(cleaned['due_date'], cleaned['due_time']))
            if deadline <= timezone.now():
                self.add_error('due_date', 'مهلت ارسال باید در آینده باشد.')
            self.instance.deadline = deadline
        return cleaned


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['attachment', 'note']
        widgets = {'note': forms.Textarea(attrs={'rows': 3})}

    def clean_attachment(self):
        return validate_upload(self.cleaned_data['attachment'])


class GradeForm(forms.Form):
    score = forms.DecimalField(label='نمره', min_value=0, max_digits=7, decimal_places=2)
    feedback = forms.CharField(label='بازخورد برای دانش‌آموز', required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, assignment, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['score'].max_value = assignment.max_score
        self.fields['score'].widget.attrs.update(min=0, max=assignment.max_score, step='0.01')
        self.assignment = assignment

    def clean_score(self):
        score = self.cleaned_data['score']
        if score > self.assignment.max_score:
            raise forms.ValidationError('نمره نمی‌تواند بیشتر از نمره کل تکلیف باشد.')
        return score


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=[('published', 'تأیید و انتشار'), ('rejected', 'بازگشت برای اصلاح')])
    note = forms.CharField(label='توضیح بررسی', required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('decision') == 'rejected' and not cleaned.get('note', '').strip():
            self.add_error('note', 'برای بازگشت تکلیف، علت اصلاح را بنویسید.')
        return cleaned
