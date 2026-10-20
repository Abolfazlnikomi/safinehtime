from django import forms
from django.forms import inlineformset_factory

from users_module.forms import INPUT_CLASS
from users_module.models import Subject
from .fields import ShamsiDateField
from .models import CounselingNote, ReportCard, SubjectGrade


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLASS


class ReportForm(StyledForm):
    date = ShamsiDateField(label='تاریخ کارنامه (شمسی)')

    class Meta:
        model = ReportCard
        fields = ['title', 'date', 'average', 'image']

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError('حداکثر حجم تصویر ۵ مگابایت است.')
        return image


class GradeForm(StyledForm):
    class Meta:
        model = SubjectGrade
        fields = ['subject', 'score']

    def __init__(self, *args, student, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'].queryset = Subject.objects.filter(field=student.field, is_active=True)


GradeFormSet = inlineformset_factory(ReportCard, SubjectGrade, form=GradeForm, extra=1, can_delete=True, max_num=100, validate_max=True)


class NoteForm(StyledForm):
    follow_up = ShamsiDateField(label='تاریخ پیگیری (شمسی)', required=False)

    class Meta:
        model = CounselingNote
        fields = ['text', 'status', 'follow_up']
        widgets = {'text': forms.Textarea(attrs={'rows': 4})}


class SubjectForm(StyledForm):
    class Meta:
        model = Subject
        fields = ['title']

    def clean_title(self):
        return self.cleaned_data['title'].strip()
