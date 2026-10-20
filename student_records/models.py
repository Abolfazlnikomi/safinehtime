from uuid import uuid4
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def private_storage():
    return FileSystemStorage(location=settings.BASE_DIR / 'private_uploads')


def report_path(instance, filename):
    return f'report-cards/{uuid4().hex}{Path(filename).suffix.lower()}'


class ReportCard(models.Model):
    student = models.ForeignKey('auth_module.Student', on_delete=models.CASCADE, related_name='report_cards')
    title = models.CharField('عنوان کارنامه / دوره', max_length=150)
    date = models.DateField('تاریخ', default=timezone.localdate)
    average = models.DecimalField('معدل از ۲۰', max_digits=4, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(20)])
    image = models.ImageField('تصویر کارنامه', storage=private_storage, upload_to=report_path, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-date', '-pk']


class SubjectGrade(models.Model):
    report = models.ForeignKey(ReportCard, on_delete=models.CASCADE, related_name='grades')
    subject = models.ForeignKey('users_module.Subject', on_delete=models.PROTECT)
    score = models.DecimalField('نمره از ۲۰', max_digits=4, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(20)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=['report', 'subject'], name='unique_report_subject')]


class CounselingNote(models.Model):
    student = models.ForeignKey('auth_module.Student', on_delete=models.CASCADE, related_name='record_notes')
    text = models.TextField('نکته مشاوره‌ای')
    status = models.CharField('وضعیت', max_length=20, choices=[('normal', 'عادی'), ('followup', 'نیازمند پیگیری'), ('urgent', 'پیگیری فوری')], default='normal')
    follow_up = models.DateField('تاریخ پیگیری', null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
