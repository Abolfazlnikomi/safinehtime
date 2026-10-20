from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from users_module.models import Access, FieldOfStudy, Province


class UserRole(models.TextChoices):
    STUDENT = "STUDENT", "دانش‌آموز"
    CONSULTANT = "CONSULTANT", "مشاور"
    PROVINCE_TRUSTEE = "PROVINCE_TRUSTEE", "مسئول منطقه"
    CONTENT_MODERATOR = "CONTENT_MODERATOR", "ناظر محتوا"
    ADMIN = "ADMIN", "مدیر سیستم"


class User(AbstractUser):
    class ModerationStudentGender(models.TextChoices):
        BOTH = "BOTH", "دختران و پسران"
        FEMALE = "FEMALE", "فقط دختران"
        MALE = "MALE", "فقط پسران"

    role = models.CharField(
        max_length=20, choices=UserRole.choices, default=UserRole.STUDENT
    )
    phone_number = models.CharField(max_length=11, unique=True, null=True, blank=True)
    gender = models.CharField(
        max_length=10,
        choices=[("MALE", "پسر"), ("FEMALE", "دختر")],
        null=True,
        blank=True,
    )
    accesses = models.ManyToManyField(
        Access,
        blank=True,
        related_name='users',
        verbose_name='دسترسی‌ها',
        help_text='برای کاربران غیر دانش‌آموز، دسترسی‌های مجاز را انتخاب کنید.',
    )
    allowed_grades = models.ManyToManyField(
        'users_module.Grade', blank=True, related_name='authorized_users',
        verbose_name='پایه‌های مجاز مدیریت',
    )

    moderation_student_gender = models.CharField(
        "جنسیت دانش‌آموزان مجاز برای نظارت بر تیکت",
        max_length=10,
        choices=ModerationStudentGender.choices,
        default=ModerationStudentGender.BOTH,
        help_text="فقط برای ناظر محتوا اعمال می‌شود و سایر دسترسی‌ها را تغییر نمی‌دهد.",
    )

    def matches_moderation_gender(self, student):
        return (
            self.moderation_student_gender == self.ModerationStudentGender.BOTH
            or self.moderation_student_gender == student.user.gender
        )

    @property
    def mobile(self):
        """نام سازگار با کد قدیمی بازیابی رمز عبور."""
        return self.phone_number

    def has_project_access(self, access_code, subject=None):
        """سوپریوزر همیشه مجاز، دانش‌آموز همیشه غیرمجاز و سایرین تابع تیک دسترسی‌اند."""
        if self.is_superuser:
            return True
        if self.role == UserRole.STUDENT or not self.is_active:
            return False
        accesses = self.accesses.filter(name=access_code)
        if subject is None:
            return accesses.exists()
        subject_id = getattr(subject, 'pk', subject)
        return accesses.filter(subject__isnull=True).exists() or accesses.filter(
            subject_id=subject_id
        ).exists()


class Consultant(models.Model):
    """پروفایل مشاور و مجموعه دسترسی‌های موضوعی او."""

    class TicketStudentGender(models.TextChoices):
        BOTH = "BOTH", "دختران و پسران"
        FEMALE = "FEMALE", "فقط دختران"
        MALE = "MALE", "فقط پسران"

    consultant = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="scopes"
    )
    accesses = models.ManyToManyField(Access, blank=True)
    can_answer_psychology = models.BooleanField(default=False)
    ticket_student_gender = models.CharField(
        "جنسیت دانش‌آموزان مجاز برای پاسخ‌گویی به تیکت",
        max_length=10,
        choices=TicketStudentGender.choices,
        default=TicketStudentGender.BOTH,
        help_text="این محدودیت فقط برای تیکت‌ها است و سایر دسترسی‌ها را تغییر نمی‌دهد.",
    )

    class Meta:
        verbose_name = "محدوده دسترسی مشاور"
        verbose_name_plural = "محدوده‌های دسترسی مشاوران"
        indexes = [models.Index(fields=["consultant"])]

    def __str__(self):
        name = self.consultant.get_full_name().strip() or self.consultant.username
        return f"مشاور {name}"

    def clean(self):
        super().clean()
        if self.consultant_id and self.consultant.role != UserRole.CONSULTANT:
            raise ValidationError(
                {"consultant": "کاربر انتخاب‌شده باید نقش مشاور داشته باشد."}
            )

    def has_access(self, access_name, student=None, subject=None):
        """بررسی مجوز با استفاده از Access به‌عنوان منبع یگانهٔ محدوده."""
        if not subject:
            return False
        if student and subject.field_id != student.field_id:
            return False
        return self.accesses.filter(name=access_name, subject=subject).exists()

    def matches_student(self, student, subject=None):
        return self.matches_ticket_gender(student) and self.has_access(
            "ticket", student=student, subject=subject
        )

    def matches_ticket_gender(self, student):
        return (
            self.ticket_student_gender == self.TicketStudentGender.BOTH
            or self.ticket_student_gender == student.user.gender
        )


class Student(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="student_profiles")
    field = models.ForeignKey(FieldOfStudy, on_delete=models.CASCADE)

    def __str__(self):
        return str(self.user)

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != UserRole.STUDENT:
            raise ValidationError(
                {"user": "کاربر انتخاب‌شده باید نقش دانش‌آموز داشته باشد."}
            )

    @property
    def province(self):
        return self.field.grade.school.province


class ProvinceTrustee(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="trustee_provinces"
    )
    province = models.ForeignKey(
        Province, on_delete=models.CASCADE, related_name="trustees"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "province"], name="unique_trustee_per_province"
            )
        ]
        verbose_name = "مسئول منطقه"
        verbose_name_plural = "مسئولان مناطق"

    def __str__(self):
        return f"{self.user} - {self.province}"

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != UserRole.PROVINCE_TRUSTEE:
            raise ValidationError(
                {"user": "کاربر انتخاب‌شده باید نقش مسئول منطقه داشته باشد."}
            )
