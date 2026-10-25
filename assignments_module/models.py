from pathlib import Path
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def private_storage():
    return FileSystemStorage(location=settings.BASE_DIR / 'private_uploads' / 'assignments')


def attachment_path(instance, filename):
    return f'{uuid4().hex}{Path(filename).suffix.lower()[:20]}'


class Assignment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'در انتظار تأیید'
        PUBLISHED = 'published', 'منتشرشده'
        REJECTED = 'rejected', 'نیازمند اصلاح'

    title = models.CharField('عنوان تکلیف', max_length=200)
    description = models.TextField('راهنمای انجام تکلیف')
    attachment = models.FileField('فایل تکلیف', storage=private_storage, upload_to=attachment_path)
    original_filename = models.CharField(max_length=255)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_assignments')
    students = models.ManyToManyField('auth_module.Student', related_name='assignments', verbose_name='دانش‌آموزان دریافت‌کننده')
    deadline = models.DateTimeField('مهلت ارسال')
    max_score = models.DecimalField('نمره کل', max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    review_note = models.TextField('توضیح ناظر', blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_assignments')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(max_score__gt=0), name='assignment_positive_max_score')]

    @property
    def is_open(self):
        return self.status == self.Status.PUBLISHED and self.deadline > timezone.now()

    def __str__(self):
        return self.title


class Submission(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey('auth_module.Student', on_delete=models.PROTECT, related_name='assignment_submissions')
    attachment = models.FileField('فایل پاسخ', storage=private_storage, upload_to=attachment_path)
    original_filename = models.CharField(max_length=255)
    note = models.TextField('توضیح پاسخ', blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    score = models.DecimalField('نمره', max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    feedback = models.TextField('بازخورد', blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='graded_assignment_submissions')
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(fields=['assignment', 'student'], name='unique_assignment_student_submission'),
            models.CheckConstraint(condition=models.Q(score__isnull=True) | models.Q(score__gte=0), name='assignment_nonnegative_score'),
        ]


class Review(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    decision = models.CharField(max_length=12, choices=Assignment.Status.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
