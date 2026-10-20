from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from auth_module.models import UserRole
from .models import Access, FieldOfStudy, Grade, Province
from .school_structure import is_general_grade

User = get_user_model()
INPUT_CLASS = "w-full rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-700 outline-none transition focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100"


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(label="رمز عبور", min_length=8, widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}))
    password_confirm = forms.CharField(label="تکرار رمز عبور", min_length=8, widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}))
    accesses = forms.ModelMultipleChoiceField(label="دسترسی‌های سامانه", queryset=Access.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    field = forms.ModelChoiceField(label="رشته تحصیلی", queryset=FieldOfStudy.objects.none(), required=False, widget=forms.Select(attrs={"class": INPUT_CLASS}))
    grade = forms.ModelChoiceField(label='پایه تحصیلی', queryset=Grade.objects.none(), required=False, widget=forms.Select(attrs={'class': INPUT_CLASS}))
    province = forms.ModelChoiceField(label="استان تحت مدیریت", queryset=Province.objects.all(), required=False, widget=forms.Select(attrs={"class": INPUT_CLASS}))

    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "phone_number", "gender", "role", "is_active", "accesses", "grade", "field", "province")
        labels = {"first_name": "نام", "last_name": "نام خانوادگی", "username": "نام کاربری", "phone_number": "شماره موبایل", "gender": "جنسیت", "role": "نقش کاربر", "is_active": "کاربر فعال باشد"}
        widgets = {
            "first_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "last_name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "username": forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "ltr", "autocomplete": "username"}),
            "phone_number": forms.TextInput(attrs={"class": INPUT_CLASS, "dir": "ltr", "inputmode": "tel"}),
            "gender": forms.Select(attrs={"class": INPUT_CLASS}),
            "role": forms.Select(attrs={"class": INPUT_CLASS}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["accesses"].queryset = Access.objects.select_related("subject").order_by("name", "subject__title")
        self.fields["field"].queryset = FieldOfStudy.objects.filter(is_active=True).select_related("grade", "grade__school", "grade__school__province").order_by("grade__school__province__name", "grade__title", "title")
        self.fields['grade'].queryset = Grade.objects.filter(is_active=True).select_related('school')
        self.fields['grade'].label_from_instance = lambda grade: f'{grade.title} · {grade.school.name}'
        self.fields['field'].label_from_instance = lambda field: f'{field.title} · {field.grade.title} · {field.grade.school.name}'
        selected_grade = self.data.get('grade') if self.is_bound else None
        if selected_grade:
            grade = self.fields['grade'].queryset.filter(pk=selected_grade).first() if str(selected_grade).isdecimal() else None
            if grade and is_general_grade(grade):
                self.fields['field'].disabled = True
                self.initial['field'] = FieldOfStudy.objects.filter(grade=grade, title='عمومی', is_active=True).values_list('pk', flat=True).first()

    def clean_phone_number(self):
        phone = (self.cleaned_data.get("phone_number") or "").strip()
        if phone and (len(phone) != 11 or not phone.startswith("09") or not phone.isdigit()):
            raise forms.ValidationError("شماره موبایل باید ۱۱ رقم و با ۰۹ شروع شود.")
        return phone or None

    def clean(self):
        cleaned = super().clean()
        grade, field = cleaned.get('grade'), cleaned.get('field')
        if grade and is_general_grade(grade):
            cleaned['field'] = FieldOfStudy.objects.filter(grade=grade, title='عمومی', is_active=True).first()
        elif grade and field and field.grade_id != grade.pk:
            self.add_error('field', 'رشته انتخاب‌شده متعلق به پایه انتخابی نیست.')
        if cleaned.get("password") and cleaned.get("password") != cleaned.get("password_confirm"):
            self.add_error("password_confirm", "تکرار رمز عبور با رمز عبور یکسان نیست.")
        if cleaned.get("password"):
            try:
                validate_password(cleaned["password"])
            except forms.ValidationError as error:
                self.add_error("password", error)
        if cleaned.get("role") == UserRole.STUDENT and not cleaned.get("field"):
            self.add_error("field", "برای کاربر دانش‌آموز، انتخاب رشته الزامی است.")
        if cleaned.get("role") == UserRole.PROVINCE_TRUSTEE and not cleaned.get("province"):
            self.add_error("province", "برای مسئول منطقه، انتخاب استان الزامی است.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.is_staff = user.role == UserRole.ADMIN
        if commit:
            user.save()
            self.save_m2m()
        return user
