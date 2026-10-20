import re
from django import forms
from django.utils import timezone
import jdatetime

from auth_module.models import UserRole
from users_module.models import FieldOfStudy, Grade, Province, School, Subject
from users_module.curriculum_access import grades_for_user, subjects_for_user
from users_module.school_structure import GENERAL_GRADES
from .models import Course, CourseResource, CourseSection, CourseEpisode


class CourseCreateForm(forms.ModelForm):
    start_date = forms.CharField(
        label="تاریخ و ساعت شروع",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "glass-input",
                "data-jdp": "",
                "data-jdp-time": "true",
                "autocomplete": "off",
                "placeholder": "مثلاً ۱۴۰۵/۰۷/۰۷ ۱۸:۳۰",
            }
        ),
    )
    end_date = forms.CharField(
        label="تاریخ و ساعت پایان",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "glass-input",
                "data-jdp": "",
                "data-jdp-time": "true",
                "autocomplete": "off",
                "placeholder": "مثلاً ۱۴۰۵/۰۸/۰۷ ۱۸:۳۰",
            }
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        grades = grades_for_user(user)
        subjects = subjects_for_user(user, access_code='course')
        fields = FieldOfStudy.objects.filter(is_active=True, grade__in=grades)
        self.fields["allowed_grades"].queryset = grades.select_related("school")
        self.fields["allowed_fields"].queryset = fields.select_related("grade")
        self.fields["subjects"].queryset = subjects.select_related("field__grade")
        self.curriculum_fields = list(fields.values('id', 'grade_id'))
        self.curriculum_books = list(subjects.values('id', 'field_id', 'field__grade_id'))
        self.fields['allowed_grades'].label_from_instance = lambda grade: f'{grade.title} · {grade.school.name}'
        self.fields['allowed_fields'].label_from_instance = lambda field: f'{field.title} · {field.grade.title}'
        self.fields['subjects'].label_from_instance = lambda book: f'{book.title} · {book.field.grade.title} · {book.field.title}'
        self.fields["allowed_provinces"].queryset = Province.objects.order_by("name")
        self.fields["allowed_cities"].queryset = School.objects.select_related("province").order_by("province__name", "name")
        self.fields["allowed_cities"].label_from_instance = lambda school: f"{school.name} · {school.province}"
        self.fields["allowed_provinces"].required = False
        self.fields["allowed_cities"].required = False

        # اختیاری کردن فیلترها برای ثبت سریع دوره توسط ادمین و سوپریوزر
        self.fields["allowed_grades"].required = False
        self.fields["allowed_fields"].required = False
        self.fields["subjects"].required = False

        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple) and name not in ("start_date", "end_date"):
                field.widget.attrs["class"] = "glass-input"

        for name in ("start_date", "end_date"):
            current = getattr(self.instance, name, None)
            if current:
                local = timezone.localtime(current)
                jalali = jdatetime.datetime.fromgregorian(datetime=local)
                self.initial[name] = f"{jalali.year:04d}/{jalali.month:02d}/{jalali.day:02d} {local.hour:02d}:{local.minute:02d}"

    class Meta:
        model = Course
        fields = [
            "title", "description", "learning_outcomes", "prerequisites", "level",
            "estimated_duration", "capacity", "cover_image", "allowed_grades",
            "allowed_fields", "subjects", "allowed_provinces", "allowed_cities", "start_date", "end_date"
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "learning_outcomes": forms.Textarea(attrs={"rows": 3}),
            "prerequisites": forms.Textarea(attrs={"rows": 3}),
            "allowed_grades": forms.CheckboxSelectMultiple(),
            "allowed_fields": forms.CheckboxSelectMultiple(),
            "subjects": forms.CheckboxSelectMultiple(),
            "allowed_provinces": forms.CheckboxSelectMultiple(),
            "allowed_cities": forms.CheckboxSelectMultiple(),
        }

    def _clean_jalali_datetime(self, name):
        value = self.cleaned_data.get(name) or self.data.get(name)
        if not value:
            return None

        if isinstance(value, timezone.datetime):
            return value

        fa_ar_digits = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
        en_digits = "01234567890123456789"
        trans_table = str.maketrans(fa_ar_digits, en_digits)
        digits = str(value).translate(trans_table).replace("،", " ").replace("٬", " ").replace("\u200c", " ").strip()

        pattern = r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T]+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?"
        match = re.fullmatch(pattern, digits)

        if not match:
            raise forms.ValidationError("فرمت تاریخ یا ساعت وارد شده نامعتبر است.")

        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            hour = int(match.group(4)) if match.group(4) else 0
            minute = int(match.group(5)) if match.group(5) else 0
            second = int(match.group(6)) if match.group(6) else 0

            local_dt = jdatetime.datetime(year, month, day, hour, minute, second).togregorian()
            return timezone.make_aware(local_dt, timezone.get_current_timezone())
        except (ValueError, TypeError, OverflowError):
            raise forms.ValidationError("تاریخ یا ساعت وارد شده در تقویم وجود ندارد.")

    def clean_start_date(self):
        value = self._clean_jalali_datetime("start_date")
        if not value and not (self.user and (self.user.is_superuser or self.user.role == UserRole.ADMIN)):
            raise forms.ValidationError("تاریخ شروع دوره را وارد کنید.")
        return value or timezone.now()

    def clean_end_date(self):
        return self._clean_jalali_datetime("end_date")

    def clean(self):
        cleaned = super().clean()
        grades = cleaned.get('allowed_grades')
        fields = cleaned.get('allowed_fields')
        subjects = cleaned.get('subjects')
        if grades:
            general_fields = self.fields['allowed_fields'].queryset.filter(grade__in=grades, grade__title__in=GENERAL_GRADES)
            field_ids = set(fields.values_list('pk', flat=True)) if fields is not None else set()
            field_ids.update(general_fields.values_list('pk', flat=True))
            cleaned['allowed_fields'] = fields = self.fields['allowed_fields'].queryset.filter(pk__in=field_ids)
        if grades and fields and fields.exclude(grade__in=grades).exists():
            self.add_error('allowed_fields', 'رشته‌ها باید متعلق به پایه‌های انتخاب‌شده باشند.')
        if subjects:
            if grades and subjects.exclude(field__grade__in=grades).exists():
                self.add_error('subjects', 'کتاب‌ها باید متعلق به پایه‌های انتخاب‌شده باشند.')
            if fields and subjects.exclude(field__in=fields).exists():
                self.add_error('subjects', 'کتاب‌ها باید متعلق به رشته‌های انتخاب‌شده باشند.')
        if self.user and not self.user.is_superuser:
            if not grades:
                self.add_error('allowed_grades', 'برای محدودماندن دوره به پایه‌های مجاز، حداقل یک پایه انتخاب کنید.')
            if not fields:
                self.add_error('allowed_fields', 'حداقل یک رشته انتخاب کنید.')
            if not subjects:
                self.add_error('subjects', 'حداقل یک کتاب انتخاب کنید.')
        provinces = cleaned.get("allowed_provinces")
        cities = cleaned.get("allowed_cities")
        if provinces and cities and cities.exclude(province__in=provinces).exists():
            self.add_error("allowed_cities", "شهرهای انتخاب‌شده باید در استان‌های انتخاب‌شده باشند.")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")

        if start and end and end <= start:
            self.add_error("end_date", "زمان پایان باید بعد از زمان شروع باشد.")

        is_admin_or_super = False
        if self.user:
            user_role = getattr(self.user, "role", None)
            if self.user.is_superuser or self.user.is_staff or user_role in [UserRole.ADMIN, "ADMIN"]:
                is_admin_or_super = True

        if not is_admin_or_super:
            for name, message in (
                ("allowed_grades", "حداقل یک پایه را انتخاب کنید."),
                ("allowed_fields", "حداقل یک رشته را انتخاب کنید."),
                ("subjects", "حداقل یک درس را انتخاب کنید."),
            ):
                if not cleaned.get(name):
                    self.add_error(name, message)

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        provinces = self.cleaned_data.get("allowed_provinces")
        cities = self.cleaned_data.get("allowed_cities")
        instance.all_provinces_allowed = not bool(provinces or cities)
        instance.all_grades_allowed = not bool(self.cleaned_data.get("allowed_grades"))
        instance.all_fields_allowed = not bool(self.cleaned_data.get("allowed_fields"))

        if commit:
            instance.save()
            self.save_m2m()
            if cities and not provinces:
                instance.allowed_provinces.set(Province.objects.filter(schools__in=cities).distinct())
        return instance


class CourseResourceForm(forms.ModelForm):
    class Meta:
        model = CourseResource
        fields = ["title", "resource_type", "file", "url", "order", "is_active"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "glass-input"}),
            "resource_type": forms.Select(attrs={"class": "glass-input"}),
            "url": forms.URLInput(attrs={"class": "glass-input", "dir": "ltr", "placeholder": "https://..."}),
            "order": forms.NumberInput(attrs={"class": "glass-input", "min": 1}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-slate-300 text-blue-600"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "file" in self.fields:
            self.fields["file"].required = False
            self.fields["file"].widget.attrs.update({"class": "glass-input", "accept": "video/*,image/*,application/pdf"})
            self.fields["file"].help_text = "فایل PDF، ویدیو یا تصویر را انتخاب کنید؛ یا به‌جای آن لینک را وارد کنید."
        if "url" in self.fields:
            self.fields["url"].required = False
            self.fields["url"].help_text = "لینک مستقیم فایل یا لینک ویدیوی YouTube/Aparat قابل استفاده است."
        if "order" in self.fields:
            self.fields["order"].required = False

    def clean(self):
        cleaned = super().clean()
        uploaded_file = cleaned.get("file")
        url = cleaned.get("url")
        resource_type = cleaned.get("resource_type")

        existing_file = getattr(self.instance, "file", None) if self.instance and self.instance.pk else None
        has_file = bool(uploaded_file or existing_file)

        if not has_file and not url:
            raise forms.ValidationError("یک فایل آپلود کنید یا لینک محتوا را وارد کنید.")
        if uploaded_file and url:
            raise forms.ValidationError("فقط یکی از فایل یا لینک را انتخاب کنید.")

        if uploaded_file:
            content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
            allowed_prefixes = {
                CourseResource.ResourceType.VIDEO: ("video/",),
                CourseResource.ResourceType.IMAGE: ("image/",),
                CourseResource.ResourceType.PDF: ("application/pdf",),
            }
            if not any(content_type.startswith(item) for item in allowed_prefixes.get(resource_type, ())):
                self.add_error("file", "نوع فایل آپلودشده با نوع محتوای انتخابی مطابقت ندارد.")
            if uploaded_file.size > 50 * 1024 * 1024:
                self.add_error("file", "حجم فایل نباید بیشتر از ۵۰ مگابایت باشد.")
        return cleaned


# -------------------------------------------------------------------
# فرم‌های مدیریت سرفصل‌ها و جلسات
# -------------------------------------------------------------------

class CourseSectionForm(forms.ModelForm):
    class Meta:
        model = CourseSection
        fields = ["title", "order", "is_active"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "glass-input"}),
            "order": forms.NumberInput(attrs={"class": "glass-input", "min": 1}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-slate-300 text-blue-600"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "order" in self.fields:
            self.fields["order"].required = False

    def clean_order(self):
        order = self.cleaned_data.get("order")
        if order is not None and order < 1:
            raise forms.ValidationError("ترتیب باید عددی بزرگ‌تر یا مساوی ۱ باشد.")
        return order


class CourseEpisodeForm(forms.ModelForm):
    class Meta:
        model = CourseEpisode
        fields = [
            "title",
            "file_type",
            "duration_or_pages",
            "file",
            "url",
            "description",
            "order",
            "is_active",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "glass-input"}),
            "file_type": forms.Select(attrs={"class": "glass-input"}),
            "duration_or_pages": forms.TextInput(attrs={"class": "glass-input", "placeholder": "مثال: ۱۵ دقیقه یا ۲۰ صفحه"}),
            "description": forms.Textarea(attrs={"class": "glass-input", "rows": 3}),
            "url": forms.URLInput(attrs={"class": "glass-input", "dir": "ltr", "placeholder": "https://..."}),
            "order": forms.NumberInput(attrs={"class": "glass-input", "min": 1}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-slate-300 text-blue-600"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # اختیاری کردن فیلدهایی که در قالب یا به صورت اختیاری هستند
        if "duration_or_pages" in self.fields:
            self.fields["duration_or_pages"].required = False

        if "description" in self.fields:
            self.fields["description"].required = False

        if "order" in self.fields:
            self.fields["order"].required = False

        if "file" in self.fields:
            self.fields["file"].required = False
            self.fields["file"].widget.attrs.update(
                {"class": "glass-input", "accept": "video/*,application/pdf,audio/*,image/*"}
            )

        if "url" in self.fields:
            self.fields["url"].required = False
            self.fields["url"].help_text = "اگر فایل آپلود نمی‌کنید، لینک مستقیم (یا آپارات/یوتیوب) را وارد کنید."

    def clean(self):
        cleaned = super().clean()

        uploaded_file = cleaned.get("file")
        url = cleaned.get("url")

        # بررسی هوشمند فایل یا لینک قبلی در دیتابیس (برای حفظ داده‌ها در حالت ویرایش)
        existing_file = getattr(self.instance, "file", None) if self.instance and self.instance.pk else None
        has_file = bool(uploaded_file or existing_file)

        if "file" in self.fields and "url" in self.fields:
            if not has_file and not url:
                raise forms.ValidationError("لطفاً یک فایل انتخاب کرده یا لینک جلسه را وارد کنید.")
            if uploaded_file and url:
                raise forms.ValidationError("فقط یکی از گزینه‌های «آپلود فایل» یا «لینک خارجی» را وارد کنید.")

        if uploaded_file and getattr(uploaded_file, "size", 0) > 200 * 1024 * 1024:
            self.add_error("file", "حجم فایل نباید بیشتر از ۲۰۰ مگابایت باشد.")

        file_type = cleaned.get("file_type")
        if uploaded_file and file_type:
            content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
            allowed = {
                "video": ("video/",),
                "pdf": ("application/pdf",),
                "audio": ("audio/",),
                "image": ("image/",),
            }
            prefixes = allowed.get(str(file_type).lower(), ())
            if prefixes and not any(content_type.startswith(p) for p in prefixes):
                # در صورتی که mimetype از سمت سیستم کاربر به درستی ارسال نشد، خطا نگیرد
                pass

        order = cleaned.get("order")
        if order is not None and order < 1:
            self.add_error("order", "ترتیب باید عددی بزرگ‌تر یا مساوی ۱ باشد.")

        return cleaned
