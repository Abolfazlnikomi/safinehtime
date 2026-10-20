from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from ckeditor_uploader.fields import RichTextUploadingField
from users_module.models import Subject


def validate_question_image_size(image):
    """Keep question-bank uploads reasonably small."""
    if image.size > 5 * 1024 * 1024:
        raise ValidationError('حجم تصویر نباید بیشتر از ۵ مگابایت باشد.')


QUESTION_IMAGE_VALIDATORS = [
    FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp']),
    validate_question_image_size,
]


# ==============================================================================
# ۱. ثابت‌ها (Enums)
# ==============================================================================
class Difficulty(models.TextChoices):
    """سطح دشواری سوال"""
    EASY = 'E', 'آسان'
    MEDIUM = 'M', 'متوسط'
    HARD = 'H', 'دشوار'


# ==============================================================================
# ۲. ساختار آموزشی (درس و فصل) و دسته‌بندی
# ==============================================================================
class Category(models.Model):
    """دسته‌بندی درختی عمومی (اختیاری)"""
    name = models.CharField('نام دسته', max_length=100)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='children',
        verbose_name='دسته مادر',
    )

    class Meta:
        verbose_name = 'دسته'
        verbose_name_plural = 'دسته‌ها'
        ordering = ['name']

    def __str__(self):
        return self.name


class Course(models.Model):
    """درس (مثال: ریاضی، فیزیک، ادبیات و ...)"""
    name = models.CharField('نام درس', max_length=150, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'درس'
        verbose_name_plural = 'دروس'
        ordering = ['name']

    def __str__(self):
        return self.name


class Chapter(models.Model):
    """فصل متعلق به یک درس"""
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='chapters',
        verbose_name='درس',
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='question_chapters',
        verbose_name='درس ثبت‌شده',
        null=True,
        blank=True,
    )
    name = models.CharField('نام فصل', max_length=200)

    class Meta:
        verbose_name = 'فصل'
        verbose_name_plural = 'فصل‌ها'
        ordering = ['subject__title', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'name'],
                name='unique_chapter_per_subject',
            )
        ]

    def __str__(self):
        subject_name = self.subject.title if self.subject_id else (self.course.name if self.course_id else 'بدون درس')
        return f'{subject_name} | {self.name}'

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.subject_id:
            raise ValidationError({'subject': 'انتخاب درس ثبت‌شده الزامی است.'})


class Lesson(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='lessons', verbose_name='فصل')
    name = models.CharField('نام درس', max_length=200)

    class Meta:
        verbose_name = 'درس کتاب'
        verbose_name_plural = 'درس‌های کتاب'
        ordering = ['chapter_id', 'name']
        constraints = [models.UniqueConstraint(fields=['chapter', 'name'], name='unique_lesson_per_chapter')]

    def __str__(self):
        return f'{self.chapter} | {self.name}'


class Topic(models.Model):
    """مبحث آموزشی متعلق به یک فصل."""
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.PROTECT,
        related_name='topics',
        verbose_name='فصل',
    )
    name = models.CharField('نام مبحث', max_length=200)
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name='topics',
                               verbose_name='درس کتاب', null=True, blank=True)
    is_active = models.BooleanField('فعال', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'مبحث'
        verbose_name_plural = 'مباحث'
        ordering = ['chapter__subject__title', 'chapter__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['chapter', 'name'], condition=models.Q(lesson__isnull=True),
                name='unique_topic_per_chapter',
            ),
            models.UniqueConstraint(fields=['lesson', 'name'], name='unique_topic_per_lesson'),
        ]

    def __str__(self):
        return f'{self.lesson or self.chapter} | {self.name}'

    def clean(self):
        super().clean()
        if self.lesson_id and self.lesson.chapter_id != self.chapter_id:
            raise ValidationError({'lesson': 'درس انتخاب‌شده متعلق به این فصل نیست.'})


# ==============================================================================
# ۳. سوالات و گزینه‌ها
# ==============================================================================
class Question(models.Model):
    """سؤال تستی یا تشریحی متصل به مبحث آموزشی."""

    class Type(models.TextChoices):
        MCQ = 'MCQ', 'چهارگزینه‌ای'
        DESCRIPTIVE = 'DES', 'تشریحی'

    question_type = models.CharField(
        'نوع سوال',
        max_length=3,
        choices=Type.choices,
        default=Type.MCQ,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='questions',
        verbose_name='دسته',
    )
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.PROTECT,
        related_name='questions',
        verbose_name='فصل',
        null=True,
        blank=True,
    )
    topic = models.ForeignKey(
        Topic,
        on_delete=models.PROTECT,
        related_name='questions',
        verbose_name='مبحث',
        null=True,
        blank=True,
    )
    class ApprovalStatus(models.TextChoices):
        PENDING = 'pending', 'در انتظار تأیید'
        APPROVED = 'approved', 'تأیید شده'
        REJECTED = 'rejected', 'رد شده'

    text = RichTextUploadingField('متن سوال', blank=True)
    image = models.ImageField(
        'تصویر سوال',
        upload_to='questions/question_images/%Y/%m/',
        blank=True,
        validators=QUESTION_IMAGE_VALIDATORS,
    )
    difficulty = models.CharField(
        'سطح دشواری',
        max_length=2,
        choices=Difficulty.choices,
        default=Difficulty.MEDIUM,
    )
    explanation = RichTextUploadingField('پاسخ تشریحی', blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name='طراح سوال',
    )
    is_active = models.BooleanField('فعال', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approval_status = models.CharField(
        'وضعیت تأیید', max_length=20, choices=ApprovalStatus.choices,
        default=ApprovalStatus.APPROVED,
    )
    approval_note = models.TextField('یادداشت تأیید/رد', blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='approved_questions',
        verbose_name='تأییدکننده',
    )
    approved_at = models.DateTimeField('زمان بررسی', null=True, blank=True)

    class Meta:
        verbose_name = 'سوال'
        verbose_name_plural = 'سوالات'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active', 'question_type']),
        ]

    def __str__(self):
        return f'Q{self.pk}: {self.text[:50] or "سؤال تصویری"}'

    def clean(self):
        from django.core.exceptions import ValidationError

        if not (self.text or '').strip() and not self.image:
            raise ValidationError('برای سؤال، متن یا تصویر وارد کنید.')
        if self.topic_id:
            self.chapter = self.topic.chapter
        if self.question_type == self.Type.DESCRIPTIVE and not (self.explanation or '').strip():
            raise ValidationError({'explanation': 'ثبت پاسخ تشریحی الزامی است.'})

    def save(self, *args, **kwargs):
        if self.topic_id:
            self.chapter_id = self.topic.chapter_id
        super().save(*args, **kwargs)


class Choice(models.Model):
    """گزینه‌های سوال"""
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='choices',
        verbose_name='سوال',
    )
    text = RichTextUploadingField('متن گزینه', blank=True)
    image = models.ImageField(
        'تصویر گزینه',
        upload_to='questions/choice_images/%Y/%m/',
        blank=True,
        validators=QUESTION_IMAGE_VALIDATORS,
    )
    is_correct = models.BooleanField('گزینه درست', default=False)

    class Meta:
        verbose_name = 'گزینه'
        verbose_name_plural = 'گزینه‌ها'
        ordering = ['pk']

    def __str__(self):
        return self.text or 'گزینه تصویری'

    def clean(self):
        if not (self.text or '').strip() and not self.image:
            raise ValidationError('برای گزینه، متن یا تصویر وارد کنید.')


# ==============================================================================
# ۴. مدیریت جلسات تمرین (Practice System)
# ==============================================================================
class PracticeSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'در حال انجام'
        DONE = 'done', 'پایان یافته'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='practice_sessions',
        verbose_name='کاربر',
    )
    chapters = models.ManyToManyField(Chapter, related_name='practice_sessions', verbose_name='فصل‌ها')
    topics = models.ManyToManyField(Topic, related_name='practice_sessions', verbose_name='مباحث', blank=True)
    questions = models.ManyToManyField(Question, related_name='practice_sessions', verbose_name='سوالات')
    question_type = models.CharField('نوع سؤال', max_length=3, choices=Question.Type.choices, default=Question.Type.MCQ)
    started_at = models.DateTimeField('زمان شروع', auto_now_add=True)
    finished_at = models.DateTimeField('زمان پایان', null=True, blank=True)
    total_questions = models.PositiveIntegerField('تعداد کل سوالات', default=0)
    correct_count = models.PositiveIntegerField('تعداد درست', default=0)
    wrong_count = models.PositiveIntegerField('تعداد نادرست', default=0)
    skipped_count = models.PositiveIntegerField('تعداد نزده', default=0)
    percent = models.FloatField('درصد', default=0.0)
    requested_difficulty = models.CharField(
        'سطح انتخابی', max_length=2, choices=Difficulty.choices, blank=True
    )
    difficulty_breakdown = models.JSONField('ترکیب سطح سؤال‌ها', default=dict, blank=True)
    status = models.CharField('وضعیت', max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)

    class Meta:
        verbose_name = 'جلسه تمرین'
        verbose_name_plural = 'جلسات تمرین'
        ordering = ['-started_at']

    def __str__(self):
        return f'تمرین #{self.pk} - {self.user}'


class PracticeAnswer(models.Model):
    session = models.ForeignKey(
        PracticeSession,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name='جلسه تمرین',
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='+', verbose_name='سوال')
    selected_choice = models.ForeignKey(
        Choice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='گزینه انتخابی',
    )
    text_answer = models.TextField('پاسخ تشریحی کاربر', blank=True)
    is_correct = models.BooleanField('پاسخ درست', null=True, blank=True)
    revealed = models.BooleanField('مشاهده جواب', default=False)
    answered_at = models.DateTimeField('زمان پاسخ', auto_now=True)

    class Meta:
        verbose_name = 'پاسخ تمرین'
        verbose_name_plural = 'پاسخ‌های تمرین'
        unique_together = ('session', 'question')


# ==============================================================================
# ۵. مدیریت جلسات آزمون (Exam System)
# ==============================================================================
class ExamSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'در حال انجام'
        DONE = 'done', 'پایان یافته'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='exam_sessions',
        verbose_name='کاربر',
    )
    chapters = models.ManyToManyField(Chapter, related_name='exam_sessions', verbose_name='فصل‌ها')
    topics = models.ManyToManyField(Topic, related_name='exam_sessions', verbose_name='مباحث', blank=True)
    questions = models.ManyToManyField(Question, related_name='exam_sessions', verbose_name='سوالات')
    question_type = models.CharField('نوع سؤال', max_length=3, choices=Question.Type.choices, default=Question.Type.MCQ)
    started_at = models.DateTimeField('زمان شروع', auto_now_add=True)
    ends_at = models.DateTimeField('مهلت زمانی', null=True, blank=True)
    finished_at = models.DateTimeField('زمان پایان', null=True, blank=True)
    total_questions = models.PositiveIntegerField('تعداد کل سوالات', default=0)
    correct_count = models.PositiveIntegerField('تعداد درست', default=0)
    wrong_count = models.PositiveIntegerField('تعداد نادرست', default=0)
    unanswered_count = models.PositiveIntegerField('تعداد نزده', default=0)
    percent = models.FloatField('درصد', default=0.0)
    requested_difficulty = models.CharField(
        'سطح انتخابی', max_length=2, choices=Difficulty.choices, blank=True
    )
    difficulty_breakdown = models.JSONField('ترکیب سطح سؤال‌ها', default=dict, blank=True)
    status = models.CharField('وضعیت', max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    auto_submitted = models.BooleanField('ثبت خودکار (اتمام زمان)', default=False)

    class Meta:
        verbose_name = 'جلسه آزمون'
        verbose_name_plural = 'جلسات آزمون'
        ordering = ['-started_at']

    def __str__(self):
        return f'آزمون #{self.pk} - {self.user}'


class ExamAnswer(models.Model):
    session = models.ForeignKey(
        ExamSession,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name='جلسه آزمون',
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='+', verbose_name='سوال')
    selected_choice = models.ForeignKey(
        Choice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='گزینه انتخابی',
    )
    text_answer = models.TextField('پاسخ تشریحی کاربر', blank=True)
    is_correct = models.BooleanField('پاسخ درست', null=True, blank=True)
    answered_at = models.DateTimeField('زمان پاسخ', auto_now=True)

    class Meta:
        verbose_name = 'پاسخ آزمون'
        verbose_name_plural = 'پاسخ‌های آزمون'
        unique_together = ('session', 'question')
