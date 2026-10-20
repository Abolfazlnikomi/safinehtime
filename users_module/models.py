from django.db import models


class Province(models.Model):
    name = models.CharField(max_length=100, choices=(
        ('hormozgan', 'هرمزگان'),
        ('kerman', 'کرمان'),
        ('sistan', 'سیستان و بلوچستان')
    ))

    def __str__(self):
        return self.get_name_display()


class School(models.Model):
    city = models.CharField('شهر', max_length=100, blank=True)
    province = models.ForeignKey(Province, models.CASCADE, 'schools')
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "مدرسه"
        verbose_name_plural = "مدارس"
        constraints = [
            models.UniqueConstraint(
                fields=["province", "name"],
                name="unique_city_per_province",
            )
        ]

    def __str__(self):
        return f"{self.name}"


class Grade(models.Model):
    title = models.CharField(max_length=100)
    school = models.ForeignKey(School, models.CASCADE, 'grades')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class FieldOfStudy(models.Model):
    title = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    grade = models.ForeignKey(Grade, models.CASCADE, 'fields')

    def __str__(self):
        return self.title


class Subject(models.Model):
    title = models.CharField(max_length=100)
    field = models.ForeignKey(FieldOfStudy, models.CASCADE, 'subjects')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title
class Access(models.Model):
    class Code(models.TextChoices):
        STUDENT_RECORD = 'student_record', 'پرونده دانش‌آموزی'
        BANK = 'bank', 'بانک سوال'
        TICKET = 'ticket', 'تیکت'
        QUIZ = 'quiz', 'آزمون'
        COURSE = 'course', 'دوره'
        # مواردی که مربوط به سوالات هستند:
        CREATE_QUESTION = 'create_question', 'ثبت سوال'
        EDIT_QUESTION = 'edit_question', 'ویرایش سوال'
        DELETE_QUESTION = 'delete_question', 'حذف سوال'
        # موارد جدید اضافه شده 👇
        CREATE_CHAPTER = 'create_chapter', 'ایجاد فصل'
        CREATE_SUBJECT = 'create_subject', 'ایجاد درس'

    name = models.CharField(max_length=200, choices=Code.choices)
    # اگر دسترسی ایجاد درس عمومی است و مربوط به درس خاصی نیست، subject باید null پذیر باشد:
    subject = models.ForeignKey(Subject, models.CASCADE, null=True, blank=True)

    def __str__(self):
        label = self.get_name_display()
        return f'{label} · {self.subject}' if self.subject_id else label
