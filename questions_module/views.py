from datetime import timedelta
import random
import jdatetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from auth_module.models import UserRole
from auth_module.models import ProvinceTrustee
from users_module.models import Access, FieldOfStudy, Grade, Subject
from users_module.curriculum_access import subjects_for_user

from .models import (
    Chapter,
    Choice,
    Difficulty,
    ExamAnswer,
    ExamSession,
    PracticeAnswer,
    PracticeSession,
    Question,
    Topic,
)
from .forms import ChapterForm, ChoiceFormSet, FieldForm, GradeForm, QuestionForm, SubjectForm, TopicForm

# هماهنگ با app_name در urls.py و پوشه‌بندی پروژه
APP_NS = "questions_module"
VALID_MODES = ("practice", "exam")
MINUTES_PER_EXAM_QUESTION = 3

SESSION_MODE_KEY = "qb_mode"
SESSION_QUESTION_TYPE_KEY = "qb_question_type"
SESSION_COURSES_KEY = "qb_course_ids"
SESSION_CHAPTERS_KEY = "qb_chapter_ids"
SESSION_TOPICS_KEY = "qb_topic_ids"
SESSION_DIFFICULTY_KEY = "qb_difficulty"
SESSION_QUESTION_COUNT_KEY = "qb_question_count"
SESSION_DIFFICULTY_COUNTS_KEY = "qb_difficulty_counts"
MAX_QUESTIONS_PER_SESSION = 100


# ============================================================
# توابع کمکی (Helper Functions)
# ============================================================

def _save_session(request):
    """ذخیره قطعی تغییرات در سشن جنگو."""
    request.session.modified = True


def _reset_wizard(request):
    """پاک کردن اطلاعات انتخاب‌های مرحله‌ای کاربر از سشن."""
    request.session.pop(SESSION_MODE_KEY, None)
    request.session.pop(SESSION_QUESTION_TYPE_KEY, None)
    request.session.pop(SESSION_COURSES_KEY, None)
    request.session.pop(SESSION_CHAPTERS_KEY, None)
    request.session.pop(SESSION_TOPICS_KEY, None)
    request.session.pop(SESSION_DIFFICULTY_KEY, None)
    request.session.pop(SESSION_QUESTION_COUNT_KEY, None)
    request.session.pop(SESSION_DIFFICULTY_COUNTS_KEY, None)
    _save_session(request)


def _parse_integer_list(values):
    """تبدیل امن ورودی‌های فرم یا سشن به لیستی از شناسه‌های عددی معتبر."""
    if not values:
        return []
    result = []
    for val in values:
        try:
            num = int(val)
            if num > 0 and num not in result:
                result.append(num)
        except (TypeError, ValueError):
            continue
    return result


def _usable_question_qs(question_type=Question.Type.MCQ, user=None):
    """سؤال‌های تأییدشده و قابل استفاده برای نوع انتخابی."""
    qs = Question.objects.filter(
        is_active=True, question_type=question_type,
        approval_status=Question.ApprovalStatus.APPROVED,
        topic__is_active=True,
    )
    if user is not None:
        qs = qs.filter(topic__chapter__subject__in=subjects_for_user(user))
    if question_type == Question.Type.MCQ:
        qs = qs.annotate(
            choice_count=Count("choices", distinct=True),
            correct_choice_count=Count("choices", filter=Q(choices__is_correct=True), distinct=True),
        ).filter(choice_count=4, correct_choice_count=1)
    else:
        qs = qs.exclude(explanation="")
    return qs


def _usable_chapters(course_ids=None, question_type=Question.Type.MCQ, user=None):
    """فصل‌هایی که حداقل یک سؤال معتبر از نوع انتخابی دارند."""
    valid_chapter_ids = (
        _usable_question_qs(question_type, user=user)
        .values_list("chapter_id", flat=True)
        .distinct()
    )
    chapters = Chapter.objects.filter(id__in=valid_chapter_ids)

    course_ids = _parse_integer_list(course_ids)
    if course_ids:
        chapters = chapters.filter(subject_id__in=course_ids)

    return chapters.filter(subject__is_active=True).select_related("subject").order_by("subject__title", "name", "id")


def _usable_topics(course_ids=None, question_type=Question.Type.MCQ, user=None):
    valid_topic_ids = _usable_question_qs(question_type, user=user).values_list("topic_id", flat=True).distinct()
    topics = Topic.objects.filter(id__in=valid_topic_ids, is_active=True, chapter__subject__is_active=True)
    course_ids = _parse_integer_list(course_ids)
    if course_ids:
        topics = topics.filter(chapter__subject_id__in=course_ids)
    return topics.select_related("chapter__subject", "lesson").order_by("chapter__subject__title", "chapter__name", "lesson__name", "name")


def _usable_courses(question_type=Question.Type.MCQ, user=None):
    """درس‌هایی که فصل دارای سوال معتبر دارند."""
    valid_course_ids = (
        _usable_chapters(question_type=question_type, user=user)
        .values_list("subject_id", flat=True)
        .distinct()
    )
    return Subject.objects.filter(id__in=valid_course_ids, is_active=True).select_related('field__grade__school').order_by("title", "id")


def _get_wizard_mode(request):
    mode = request.session.get(SESSION_MODE_KEY)
    return mode if mode in VALID_MODES else None


def _get_wizard_question_type(request):
    value = request.session.get(SESSION_QUESTION_TYPE_KEY)
    return value if value in Question.Type.values else None


def _get_wizard_course_ids(request):
    return _parse_integer_list(request.session.get(SESSION_COURSES_KEY, []))


def _get_wizard_chapter_ids(request):
    return _parse_integer_list(request.session.get(SESSION_CHAPTERS_KEY, []))


def _get_wizard_topic_ids(request):
    return _parse_integer_list(request.session.get(SESSION_TOPICS_KEY, []))


def _get_difficulty_counts(request):
    valid = {value for value, _label in Difficulty.choices}
    stored = request.session.get(SESSION_DIFFICULTY_COUNTS_KEY, {})
    result = {}
    if isinstance(stored, dict):
        for value, count in stored.items():
            try:
                count = int(count)
            except (TypeError, ValueError):
                continue
            if value in valid and count > 0:
                result[value] = count
    if result:
        return result

    # سازگاری با جلسه‌هایی که قبل از انتخاب چندسطحی در سشن مانده‌اند.
    old_difficulty = request.session.get(SESSION_DIFFICULTY_KEY)
    old_count = request.session.get(SESSION_QUESTION_COUNT_KEY)
    if old_difficulty in valid:
        try:
            old_count = int(old_count)
        except (TypeError, ValueError):
            old_count = 0
        if old_count > 0:
            return {old_difficulty: old_count}
    return {}


def _difficulty_options(selected_counts):
    return [
        {
            "value": value,
            "label": label,
            "selected": value in selected_counts,
            "count": selected_counts.get(value, 1),
        }
        for value, label in Difficulty.choices
    ]


def _select_questions(topic_ids, difficulty_counts, question_type, user=None):
    questions = []
    for difficulty, requested_count in difficulty_counts.items():
        selected = list(
            _usable_question_qs(question_type, user=user)
            .filter(topic_id__in=topic_ids, difficulty=difficulty)
            .prefetch_related("choices")
            .order_by("?")[:requested_count]
        )
        if len(selected) != requested_count:
            return [], difficulty
        questions.extend(selected)
    random.shuffle(questions)
    return questions, None


def _validate_selected_ids(selected_ids, queryset):
    """بررسی اینکه شناسه‌های ارسالی حتماً در لیست مجاز موجود باشند."""
    selected_ids = _parse_integer_list(selected_ids)
    valid_ids = set(queryset.values_list("id", flat=True))
    return [pk for pk in selected_ids if pk in valid_ids]


def _can_manage_questions(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role == UserRole.ADMIN:
        return True
    return user.role in {UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE} and user.has_project_access(Access.Code.BANK)


def _can_manage_subject(user, subject_id):
    return subjects_for_user(user).filter(pk=subject_id).exists()


def _is_curriculum_admin(user):
    return user.is_authenticated and user.is_superuser


def _can_create_chapter(user, subject_id=None):
    return _is_curriculum_admin(user)


def _can_create_subject(user):
    return _is_curriculum_admin(user)



def _can_view_question_overview(user):
    return user.is_authenticated and (
        user.is_superuser or user.role in {
            UserRole.ADMIN, UserRole.CONTENT_MODERATOR, UserRole.CONSULTANT,
        }
    )


def _question_overview(user=None):
    """گزارش وضعیت سؤال‌ها؛ برای مشاور فقط سؤال‌های خودش را برمی‌گرداند."""
    base_qs = Question.objects.all()
    if user is not None and not user.is_superuser:
        base_qs = base_qs.filter(chapter__subject__in=subjects_for_user(user))
    if user is not None and user.role == UserRole.CONSULTANT and not user.is_superuser:
        base_qs = base_qs.filter(creator=user)
    questions = base_qs.select_related(
        'creator', 'chapter__subject', 'topic', 'approved_by'
    ).order_by('-created_at', '-pk')
    counts = {
        status: base_qs.filter(approval_status=status).count()
        for status, _label in Question.ApprovalStatus.choices
    }
    return {
        'questions': questions[:100],
        'total': sum(counts.values()),
        'pending': counts.get(Question.ApprovalStatus.PENDING, 0),
        'approved': counts.get(Question.ApprovalStatus.APPROVED, 0),
        'rejected': counts.get(Question.ApprovalStatus.REJECTED, 0),
    }


def _question_requires_approval(user):
    return user.role == UserRole.CONSULTANT and not user.is_superuser


def _trustee_can_review(user, question):
    if user.is_superuser:
        return True
    if user.role == UserRole.ADMIN:
        return bool(question.chapter_id and _can_manage_subject(user, question.chapter.subject_id))
    if user.role != UserRole.PROVINCE_TRUSTEE or not question.chapter_id:
        return False
    province_id = question.chapter.subject.field.grade.school.province_id
    return ProvinceTrustee.objects.filter(user=user, province_id=province_id).exists()


def _pending_questions_for_trustee(user):
    qs = Question.objects.filter(
        approval_status=Question.ApprovalStatus.PENDING,
    ).select_related('chapter__subject__field__grade__school').prefetch_related('choices')
    if user.is_superuser:
        return qs
    if user.role == UserRole.ADMIN:
        return qs.filter(chapter__subject__in=subjects_for_user(user))
    province_ids = ProvinceTrustee.objects.filter(user=user).values_list('province_id', flat=True)
    return qs.filter(chapter__subject__field__grade__school__province_id__in=province_ids)


def _performance_data(user, course_ids=None):
    """خلاصه عملکرد ذخیره‌شده کاربر به تفکیک درس."""
    courses = subjects_for_user(user)
    if course_ids:
        courses = courses.filter(id__in=course_ids)

    result = []
    for course in courses.order_by("title"):
        practice = PracticeAnswer.objects.filter(
            session__user=user,
            session__status=PracticeSession.Status.DONE,
            question__chapter__subject=course,
        ).aggregate(total=Count("id"), correct=Sum("is_correct"))
        exam = ExamAnswer.objects.filter(
            session__user=user,
            session__status=ExamSession.Status.DONE,
            question__chapter__subject=course,
        ).aggregate(total=Count("id"), correct=Sum("is_correct"))
        total = (practice["total"] or 0) + (exam["total"] or 0)
        correct = (practice["correct"] or 0) + (exam["correct"] or 0)
        if total:
            result.append({"name": course.title, "percent": round(correct * 100 / total, 1), "total": total})
    return result


def _persian_digits(value):
    return str(value).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def _jalali_date_text(value, fmt='%Y/%m/%d'):
    if not value:
        return ''
    value = timezone.localtime(value) if timezone.is_aware(value) else value
    return _persian_digits(jdatetime.datetime.fromgregorian(datetime=value).strftime(fmt))


def _exam_progress_data(user, course_ids=None):
    """روند درصد آزمون‌های تکمیل‌شده، به ترتیب زمان انجام."""
    exams = ExamSession.objects.filter(
        user=user,
        status=ExamSession.Status.DONE,
    )
    if course_ids:
        exams = exams.filter(chapters__subject_id__in=course_ids)
    exams = list(exams.distinct().order_by('-finished_at', '-id')[:20])
    exams.reverse()
    return [
        {
            "label": f"آزمون {_persian_digits(exam.pk)}",
            "percent": exam.percent,
            "date": _persian_digits(
                _jalali_date_text(exam.finished_at or exam.started_at)
            ),
            "correct": exam.correct_count,
            "wrong": exam.wrong_count,
            "unanswered": exam.unanswered_count,
            "total": exam.total_questions,
        }
        for exam in exams
    ]


def _previous_answer_map(user, question_ids, *, practice_session_id=None, exam_session_id=None):
    """آخرین نتیجه قبلی کاربر برای سؤال‌های تکراری."""
    attempts = []
    practice_answers = PracticeAnswer.objects.filter(
        session__user=user,
        question_id__in=question_ids,
    )
    exam_answers = ExamAnswer.objects.filter(
        session__user=user,
        question_id__in=question_ids,
    )
    if practice_session_id:
        practice_answers = practice_answers.exclude(session_id=practice_session_id)
    if exam_session_id:
        exam_answers = exam_answers.exclude(session_id=exam_session_id)
    attempts.extend(practice_answers.values('question_id', 'selected_choice_id', 'text_answer', 'is_correct', 'answered_at'))
    attempts.extend(exam_answers.values('question_id', 'selected_choice_id', 'text_answer', 'is_correct', 'answered_at'))

    latest = {}
    for attempt in attempts:
        old = latest.get(attempt['question_id'])
        if old is None or attempt['answered_at'] > old['answered_at']:
            latest[attempt['question_id']] = attempt

    result = {}
    for question_id, attempt in latest.items():
        if attempt.get('text_answer'):
            result[question_id] = {"label": "قبلاً پاسخ داده‌اید", "class": "correct"}
        elif attempt['selected_choice_id'] is None:
            result[question_id] = {"label": "قبلاً نزده‌اید", "class": "skipped"}
        elif attempt['is_correct']:
            result[question_id] = {"label": "قبلاً درست زده‌اید", "class": "correct"}
        else:
            result[question_id] = {"label": "قبلاً غلط زده‌اید", "class": "wrong"}
    return result


def _question_form_context(form, formset, editing, question):
    """داده‌های لازم برای فیلتر زنجیره‌ای ساختار آموزشی در فرم سؤال."""
    hierarchy = {
        "fields": [{"id": item.pk, "title": item.title, "grade": item.grade_id} for item in form.fields["field"].queryset],
        "subjects": [{"id": item.pk, "title": item.title, "field": item.field_id} for item in form.fields["subject"].queryset],
        "chapters": [{"id": item.pk, "title": item.name, "subject": item.subject_id} for item in form.fields["chapter"].queryset],
        "topics": [{"id": item.pk, "title": item.name, "chapter": item.chapter_id} for item in form.fields["topic"].queryset],
    }
    return {
        "form": form, "formset": formset, "editing": editing, "question": question,
        "is_descriptive": question.question_type == Question.Type.DESCRIPTIVE,
        "hierarchy": hierarchy,
    }


# ============================================================
# مدیریت سوالات (CRUD پایه)
# ============================================================

@login_required
def question_list(request):
    """ورودی بانک سؤال؛ کاربر را وارد ویزارد انتخاب می‌کند."""
    return redirect(f"{APP_NS}:choose_mode")


@login_required
def question_create(request):
    """انتخاب نوع و ثبت سؤال تستی یا تشریحی توسط کاربران مجاز."""
    if not _can_manage_questions(request.user):
        messages.error(request, "شما اجازه ایجاد سؤال ندارید.")
        return redirect(f"{APP_NS}:choose_mode")

    requested_type = request.POST.get("question_type") or request.GET.get("type")
    if requested_type not in Question.Type.values:
        return render(request, "questions_module/choose_question_type.html")
    question = Question(creator=request.user, question_type=requested_type)
    form = QuestionForm(request.POST or None, request.FILES or None, instance=question, user=request.user)
    question.question_type = requested_type
    formset = ChoiceFormSet(request.POST or None, request.FILES or None, instance=question)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        if not _can_manage_subject(request.user, form.cleaned_data["chapter"].subject_id):
            messages.error(request, "برای درس انتخاب‌شده دسترسی بانک سؤال ندارید.")
            return render(
                request, "questions_module/question_form.html",
                _question_form_context(form, formset, False, question), status=403,
            )
        with transaction.atomic():
            question = form.save(commit=False)
            question.creator = request.user
            question.question_type = requested_type
            if _question_requires_approval(request.user):
                question.approval_status = Question.ApprovalStatus.PENDING
                question.approved_by = None
                question.approved_at = None
                question.approval_note = ''
            else:
                question.approval_status = Question.ApprovalStatus.APPROVED
            question.save()
            formset.instance = question
            if question.question_type == Question.Type.MCQ:
                formset.save()
            else:
                question.choices.all().delete()
        messages.success(
            request,
            "سؤال ثبت شد و برای تأیید مسئول منطقه ارسال گردید."
            if _question_requires_approval(request.user)
            else "سؤال با موفقیت وارد بانک سؤال شد.",
        )
        return redirect(f"{APP_NS}:question_create")
    return render(request, "questions_module/question_form.html", _question_form_context(form, formset, False, question))


@login_required
def question_edit(request, pk):
    if not _can_manage_questions(request.user):
        messages.error(request, "شما اجازه ویرایش سؤال ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    question = get_object_or_404(Question, pk=pk)
    if (
        request.user.role == UserRole.CONSULTANT
        and (question.creator_id != request.user.pk
             or question.approval_status != Question.ApprovalStatus.PENDING)
    ):
        messages.error(request, "فقط سؤال‌های خودتان که هنوز بررسی نشده‌اند قابل ویرایش هستند.")
        return redirect(f"{APP_NS}:choose_mode")
    if not question.chapter_id or not _can_manage_subject(request.user, question.chapter.subject_id):
        messages.error(request, "برای ویرایش سؤال این درس دسترسی ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    form = QuestionForm(request.POST or None, request.FILES or None, instance=question, user=request.user)
    question.question_type = question.question_type
    formset = ChoiceFormSet(request.POST or None, request.FILES or None, instance=question)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            question = form.save(commit=False)
            if _question_requires_approval(request.user):
                question.approval_status = Question.ApprovalStatus.PENDING
                question.approved_by = None
                question.approved_at = None
            question.save()
            if question.question_type == Question.Type.MCQ:
                formset.save()
            else:
                question.choices.all().delete()
        messages.success(request, "تغییرات سؤال ذخیره شد.")
        return redirect(f"{APP_NS}:question_edit", pk=question.pk)
    return render(request, "questions_module/question_form.html", _question_form_context(form, formset, True, question))


@login_required
@require_POST
def question_delete(request, pk):
    question = get_object_or_404(Question, pk=pk)
    if request.user.role == UserRole.CONSULTANT:
        allowed = (
            question.creator_id == request.user.pk
            and question.approval_status == Question.ApprovalStatus.PENDING
        )
    else:
        allowed = _can_manage_questions(request.user) and (
            not question.chapter_id
            or _can_manage_subject(request.user, question.chapter.subject_id)
        )
    if not allowed:
        messages.error(request, "این سؤال قابل حذف نیست یا دسترسی لازم را ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    question.delete()
    messages.success(request, "سؤال در انتظار تأیید با موفقیت حذف شد.")
    return redirect(f"{APP_NS}:choose_mode")


@login_required
def approval_queue(request):
    if not (request.user.is_superuser or request.user.role in {UserRole.ADMIN, UserRole.PROVINCE_TRUSTEE}):
        messages.error(request, "شما دسترسی بررسی سؤال‌ها را ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    return render(request, "questions_module/approval_queue.html", {
        "questions": _pending_questions_for_trustee(request.user),
    })


@login_required
@require_POST
def review_question(request, pk):
    question = get_object_or_404(Question.objects.select_related('chapter__subject__field__grade__school'), pk=pk)
    if not _trustee_can_review(request.user, question):
        messages.error(request, "برای بررسی این سؤال دسترسی ندارید.")
        return redirect(f"{APP_NS}:approval_queue")
    decision = request.POST.get('decision')
    if decision not in {Question.ApprovalStatus.APPROVED, Question.ApprovalStatus.REJECTED}:
        messages.error(request, "وضعیت بررسی نامعتبر است.")
        return redirect(f"{APP_NS}:approval_queue")
    question.approval_status = decision
    question.approved_by = request.user
    question.approved_at = timezone.now()
    question.approval_note = request.POST.get('note', '').strip()
    question.is_active = decision == Question.ApprovalStatus.APPROVED
    question.save(update_fields=['approval_status', 'approved_by', 'approved_at', 'approval_note', 'is_active', 'updated_at'])
    messages.success(request, 'سؤال تأیید شد و وارد بانک سؤال شد.' if decision == Question.ApprovalStatus.APPROVED else 'سؤال رد شد.')
    return redirect(f"{APP_NS}:approval_queue")


@login_required
@require_http_methods(["GET", "POST"])
def chapter_create(request):
    if not _can_create_chapter(request.user):
        messages.error(request, "شما اجازه ایجاد فصل ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    form = ChapterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        subject = form.cleaned_data["subject"]
        form.save()
        messages.success(request, "فصل جدید با موفقیت ایجاد شد.")
        return redirect(f"{APP_NS}:curriculum_manage")
    return render(request, "questions_module/entity_form.html", {"form": form, "entity_title": "ایجاد فصل", "entity_help": "فصل را برای یکی از درس‌های مجاز خود ثبت کنید."})


@login_required
@require_http_methods(["GET", "POST"])
def subject_create(request):
    if not _can_create_subject(request.user):
        messages.error(request, "شما اجازه ایجاد درس ندارید.")
        return redirect(f"{APP_NS}:choose_mode")
    form = SubjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "درس جدید با موفقیت ایجاد شد.")
        return redirect(f"{APP_NS}:curriculum_manage")
    return render(request, "questions_module/entity_form.html", {"form": form, "entity_title": "ایجاد درس", "entity_help": "درس به ساختار پایه، رشته و مدرسه موجود در سامانه متصل می‌شود."})


CURRICULUM_ENTITIES = {
    "grade": (Grade, GradeForm, "پایه"),
    "field": (FieldOfStudy, FieldForm, "رشته"),
    "subject": (Subject, SubjectForm, "درس"),
    "chapter": (Chapter, ChapterForm, "فصل"),
    "topic": (Topic, TopicForm, "مبحث"),
}


@login_required
def curriculum_manage(request):
    if not _is_curriculum_admin(request.user):
        messages.error(request, "مدیریت ساختار آموزشی فقط برای مدیر سیستم مجاز است.")
        return redirect(f"{APP_NS}:choose_mode")
    return render(request, "questions_module/curriculum_manage.html", {
        "grades": Grade.objects.select_related("school").order_by("school__name", "title"),
        "fields": FieldOfStudy.objects.select_related("grade__school").order_by("grade__title", "title"),
        "subjects": Subject.objects.select_related("field__grade").order_by("field__grade__title", "field__title", "title"),
        "chapters": Chapter.objects.select_related("subject__field__grade").order_by("subject__title", "name"),
        "topics": Topic.objects.select_related("chapter__subject").order_by("chapter__subject__title", "chapter__name", "name"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def curriculum_entity_create(request, kind):
    if not _is_curriculum_admin(request.user) or kind not in CURRICULUM_ENTITIES:
        messages.error(request, "دسترسی مجاز نیست.")
        return redirect(f"{APP_NS}:choose_mode")
    _model, form_class, label = CURRICULUM_ENTITIES[kind]
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{label} با موفقیت ایجاد شد.")
        return redirect(f"{APP_NS}:curriculum_manage")
    return render(request, "questions_module/entity_form.html", {"form": form, "entity_title": f"ایجاد {label}"})


@login_required
@require_http_methods(["GET", "POST"])
def curriculum_entity_edit(request, kind, pk):
    if not _is_curriculum_admin(request.user) or kind not in CURRICULUM_ENTITIES:
        messages.error(request, "دسترسی مجاز نیست.")
        return redirect(f"{APP_NS}:choose_mode")
    model, form_class, label = CURRICULUM_ENTITIES[kind]
    instance = get_object_or_404(model, pk=pk)
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{label} با موفقیت ویرایش شد.")
        return redirect(f"{APP_NS}:curriculum_manage")
    return render(request, "questions_module/entity_form.html", {"form": form, "entity_title": f"ویرایش {label}"})


@login_required
@require_POST
def curriculum_entity_delete(request, kind, pk):
    if not _is_curriculum_admin(request.user) or kind not in CURRICULUM_ENTITIES:
        messages.error(request, "دسترسی مجاز نیست.")
        return redirect(f"{APP_NS}:choose_mode")
    model, _form_class, label = CURRICULUM_ENTITIES[kind]
    instance = get_object_or_404(model, pk=pk)
    try:
        instance.delete()
        messages.success(request, f"{label} حذف شد.")
    except ProtectedError:
        messages.error(request, f"این {label} استفاده شده است و تا زمانی که وابستگی‌هایش حذف نشوند قابل حذف نیست.")
    return redirect(f"{APP_NS}:curriculum_manage")


# ============================================================
# ویزارد ۳ مرحله‌ای (Wizard)
# ============================================================

@login_required
@require_http_methods(["GET", "POST"])
def choose_mode(request):
    """مرحله ۱: انتخاب حالت جلسه و نوع سؤال."""
    if request.method == "POST":
        mode = request.POST.get("mode", "").strip()
        question_type = request.POST.get("question_type", "").strip()

        if mode not in VALID_MODES or question_type not in Question.Type.values:
            messages.error(request, "لطفاً نوع جلسه و نوع سؤال را انتخاب کنید.")
            return render(
                request,
                "questions_module/question_list.html",
                {"selected_mode": mode},
                status=400,
            )

        request.session[SESSION_MODE_KEY] = mode
        request.session[SESSION_QUESTION_TYPE_KEY] = question_type
        request.session.pop(SESSION_COURSES_KEY, None)
        request.session.pop(SESSION_CHAPTERS_KEY, None)
        request.session.pop(SESSION_TOPICS_KEY, None)
        request.session.pop(SESSION_DIFFICULTY_KEY, None)
        request.session.pop(SESSION_QUESTION_COUNT_KEY, None)
        request.session.pop(SESSION_DIFFICULTY_COUNTS_KEY, None)
        _save_session(request)

        return redirect(f"{APP_NS}:select_courses")

    return render(
        request,
        "questions_module/question_list.html",
        {
            "selected_mode": _get_wizard_mode(request),
            "selected_question_type": _get_wizard_question_type(request),
            "can_create_question": _can_manage_questions(request.user),
            "can_manage_curriculum": _is_curriculum_admin(request.user),
            "can_review_questions": request.user.is_superuser or request.user.role in {UserRole.ADMIN, UserRole.PROVINCE_TRUSTEE},
            "can_view_question_overview": _can_view_question_overview(request.user),
            "question_overview": _question_overview(request.user) if _can_view_question_overview(request.user) else None,
            "performance": _performance_data(request.user),
            "exam_progress": _exam_progress_data(request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def select_courses(request):
    """مرحله ۲: انتخاب درس‌ها."""
    mode = _get_wizard_mode(request)
    question_type = _get_wizard_question_type(request)
    if not mode or not question_type:
        messages.warning(request, "ابتدا حالت تمرین یا آزمون را انتخاب کنید.")
        return redirect(f"{APP_NS}:choose_mode")

    courses = _usable_courses(question_type, user=request.user)
    selected_course_ids = _get_wizard_course_ids(request)

    if request.method == "POST":
        posted_course_ids = request.POST.getlist("courses")
        valid_selected_ids = _validate_selected_ids(posted_course_ids, courses)

        if not posted_course_ids:
            messages.error(request, "لطفاً حداقل یک درس را انتخاب کنید.")
        elif not valid_selected_ids:
            messages.error(request, "درس انتخابی معتبر نیست یا سؤال فعالی برای آن وجود ندارد.")
        else:
            request.session[SESSION_COURSES_KEY] = valid_selected_ids
            request.session.pop(SESSION_CHAPTERS_KEY, None)
            request.session.pop(SESSION_TOPICS_KEY, None)
            _save_session(request)
            return redirect(f"{APP_NS}:select_chapters")

    return render(
        request,
        "questions_module/select_courses.html",
        {
            "mode": mode,
            "question_type": question_type,
            "courses": courses,
            "selected_course_ids": selected_course_ids,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def select_chapters(request):
    """مرحله ۳: نمایش فصل‌ها و انتخاب مباحث."""
    mode = _get_wizard_mode(request)
    question_type = _get_wizard_question_type(request)
    course_ids = _get_wizard_course_ids(request)

    if not mode or not question_type:
        messages.warning(request, "ابتدا حالت تمرین یا آزمون را مشخص کنید.")
        return redirect(f"{APP_NS}:choose_mode")

    if not course_ids:
        messages.warning(request, "ابتدا باید درس(های) مورد نظرتان را انتخاب کنید.")
        return redirect(f"{APP_NS}:select_courses")

    chapters = _usable_chapters(course_ids, question_type, user=request.user)
    topics = _usable_topics(course_ids, question_type, user=request.user)
    selected_topic_ids = _get_wizard_topic_ids(request)
    topics_by_chapter = {}
    for topic in topics:
        topics_by_chapter.setdefault(topic.chapter_id, []).append(topic)
    chapter_items = [{"chapter": chapter, "topics": topics_by_chapter.get(chapter.pk, [])} for chapter in chapters]
    selected_counts = _get_difficulty_counts(request) or {Difficulty.MEDIUM: 10}

    if request.method == "POST":
        posted_topic_ids = request.POST.getlist("topics")
        valid_topic_ids = _validate_selected_ids(posted_topic_ids, topics)
        valid_chapter_ids = list(topics.filter(pk__in=valid_topic_ids).values_list("chapter_id", flat=True).distinct())

        valid_difficulties = {value for value, _label in Difficulty.choices}
        selected_difficulties = request.POST.getlist("difficulties")

        # پشتیبانی از فرم قدیمی برای جلوگیری از شکستن درخواست‌های ذخیره‌شده یا تست‌ها.
        if not selected_difficulties and request.POST.get("difficulty"):
            selected_difficulties = [request.POST.get("difficulty")]

        selected_counts = {}
        invalid_count = False
        for difficulty in selected_difficulties:
            if difficulty not in valid_difficulties or difficulty in selected_counts:
                continue
            raw_count = request.POST.get(f"count_{difficulty}")
            if raw_count is None and len(selected_difficulties) == 1:
                raw_count = request.POST.get("question_count")
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                count = 0
            if count < 1:
                invalid_count = True
            selected_counts[difficulty] = count

        total_count = sum(selected_counts.values())
        shortage = None
        for difficulty, count in selected_counts.items():
            available = _usable_question_qs(question_type).filter(
                topic_id__in=valid_topic_ids,
                difficulty=difficulty,
            ).count()
            if count > available:
                shortage = (dict(Difficulty.choices)[difficulty], available)
                break

        if not posted_topic_ids:
            messages.error(request, "لطفاً حداقل یک مبحث را انتخاب کنید.")
        elif not valid_topic_ids:
            messages.error(request, "مبحث انتخابی فاقد سؤال فعال از نوع انتخاب‌شده است.")
        elif not selected_counts:
            messages.error(request, "لطفاً حداقل یک سطح سؤال را انتخاب کنید.")
        elif invalid_count:
            messages.error(request, "تعداد هر سطح انتخاب‌شده باید حداقل یک سؤال باشد.")
        elif total_count > MAX_QUESTIONS_PER_SESSION:
            messages.error(request, "مجموع تعداد سؤال‌ها نمی‌تواند بیشتر از ۱۰۰ باشد.")
        elif shortage:
            messages.error(request, f"برای سطح {shortage[0]} فقط {shortage[1]} سؤال موجود است.")
        else:
            request.session[SESSION_CHAPTERS_KEY] = valid_chapter_ids
            request.session[SESSION_TOPICS_KEY] = valid_topic_ids
            request.session[SESSION_DIFFICULTY_COUNTS_KEY] = selected_counts
            request.session[SESSION_DIFFICULTY_KEY] = next(iter(selected_counts)) if len(selected_counts) == 1 else ""
            request.session[SESSION_QUESTION_COUNT_KEY] = total_count
            _save_session(request)

            if mode == "practice":
                return redirect(f"{APP_NS}:start_practice")
            return redirect(f"{APP_NS}:start_exam")

    return render(
        request,
        "questions_module/select_chapters.html",
        {
            "mode": mode,
            "question_type": question_type,
            "chapter_items": chapter_items,
            "selected_topic_ids": selected_topic_ids,
            "difficulty_options": _difficulty_options(selected_counts),
        },
    )


@login_required
@require_POST
def reset_wizard(request):
    """ریست کردن مراحل و بازگشت به شروع."""
    _reset_wizard(request)
    messages.info(request, "مراحل انتخاب مجدداً تنظیم شد.")
    return redirect(f"{APP_NS}:choose_mode")


# ============================================================
# بخش تمرین (Practice)
# ============================================================

@login_required
@require_http_methods(["GET"])
def start_practice(request):
    """ایجاد جلسه تمرین جدید و هدایت به صفحه پاسخگویی."""
    mode = _get_wizard_mode(request)
    chapter_ids = _get_wizard_chapter_ids(request)
    topic_ids = _get_wizard_topic_ids(request)
    question_type = _get_wizard_question_type(request)
    difficulty_counts = _get_difficulty_counts(request)

    if mode != "practice" or not topic_ids or not difficulty_counts or not question_type:
        messages.warning(request, "لطفاً فرآیند انتخاب را کامل کنید.")
        return redirect(f"{APP_NS}:choose_mode")

    allowed_topics = _usable_topics(question_type=question_type, user=request.user)
    if set(topic_ids) != set(allowed_topics.filter(pk__in=topic_ids).values_list('pk', flat=True)):
        _reset_wizard(request)
        return redirect(f"{APP_NS}:select_courses")
    chapter_ids = list(allowed_topics.filter(pk__in=topic_ids).values_list('chapter_id', flat=True).distinct())
    questions, missing_difficulty = _select_questions(topic_ids, difficulty_counts, question_type, user=request.user)

    if not questions:
        label = dict(Difficulty.choices).get(missing_difficulty, "انتخاب‌شده")
        messages.error(request, f"تعداد سؤال کافی برای سطح {label} موجود نیست.")
        return redirect(f"{APP_NS}:select_chapters")

    with transaction.atomic():
        practice_session = PracticeSession.objects.create(
            user=request.user,
            total_questions=len(questions),
            requested_difficulty=next(iter(difficulty_counts)) if len(difficulty_counts) == 1 else "",
            difficulty_breakdown=difficulty_counts,
            question_type=question_type,
        )
        practice_session.chapters.set(Chapter.objects.filter(id__in=chapter_ids))
        practice_session.topics.set(Topic.objects.filter(id__in=topic_ids))
        practice_session.questions.set(questions)

    _reset_wizard(request)
    return redirect(f"{APP_NS}:practice_session", pk=practice_session.pk)


@login_required
@require_http_methods(["GET"])
def practice_session_view(request, pk):
    """صفحه مشاهده و حل سوالات تمرین."""
    practice_session = get_object_or_404(
        PracticeSession.objects.prefetch_related("questions__choices"),
        pk=pk,
        user=request.user,
    )

    if practice_session.status == PracticeSession.Status.DONE:
        return redirect(f"{APP_NS}:practice_result", pk=practice_session.pk)

    answers = {
        ans.question_id: ans
        for ans in practice_session.answers.select_related("selected_choice")
    }
    questions = list(practice_session.questions.prefetch_related("choices").all())
    previous_answers = _previous_answer_map(
        request.user,
        [question.pk for question in questions],
        practice_session_id=practice_session.pk,
    )
    items = [
        {
            "question": question,
            "answer": answers.get(question.pk),
            "previous": previous_answers.get(question.pk),
        }
        for question in questions
    ]

    return render(
        request,
        "questions_module/practice_session.html",
        {
            "session": practice_session,
            "items": items,
            "answers": answers,
        },
    )


@login_required
@require_POST
def practice_answer(request, pk, question_id):
    """ثبت پاسخ همان لحظه و برگرداندن بازخورد رنگی."""
    practice_session = get_object_or_404(PracticeSession, pk=pk, user=request.user, status=PracticeSession.Status.IN_PROGRESS)
    question = get_object_or_404(practice_session.questions.prefetch_related("choices"), pk=question_id)
    if question.question_type != Question.Type.MCQ:
        return JsonResponse({"error": "پاسخ تشریحی همراه پایان تمرین ثبت می‌شود."}, status=400)
    existing = PracticeAnswer.objects.filter(session=practice_session, question=question).first()
    if existing and (existing.revealed or existing.selected_choice_id):
        return JsonResponse({"error": "پاسخ این سؤال قبلاً ثبت یا مشاهده شده است."}, status=409)
    choice = get_object_or_404(question.choices, pk=request.POST.get("choice"))
    answer, _ = PracticeAnswer.objects.update_or_create(
        session=practice_session,
        question=question,
        defaults={"selected_choice": choice, "is_correct": choice.is_correct, "revealed": False},
    )
    correct_choice = question.choices.get(is_correct=True)
    return JsonResponse({
        "is_correct": bool(answer.is_correct),
        "selected_choice_id": choice.pk,
        "correct_choice_id": correct_choice.pk,
        "explanation": question.explanation,
    })


@login_required
@require_POST
def practice_reveal(request, pk, question_id):
    practice_session = get_object_or_404(PracticeSession, pk=pk, user=request.user, status=PracticeSession.Status.IN_PROGRESS)
    question = get_object_or_404(practice_session.questions.prefetch_related("choices"), pk=question_id)
    answer, _ = PracticeAnswer.objects.get_or_create(session=practice_session, question=question)
    if question.question_type == Question.Type.DESCRIPTIVE:
        answer.selected_choice = None
        answer.text_answer = ""
        answer.revealed = True
        answer.is_correct = None
        answer.save(update_fields=["selected_choice", "text_answer", "revealed", "is_correct", "answered_at"])
        return JsonResponse({"descriptive": True, "explanation": question.explanation})
    if answer.selected_choice_id:
        return JsonResponse({"error": "این سؤال قبلاً پاسخ داده شده است."}, status=409)
    answer.revealed = True
    answer.is_correct = None
    answer.save(update_fields=["revealed", "is_correct", "answered_at"])
    correct_choice = question.choices.get(is_correct=True)
    return JsonResponse({"correct_choice_id": correct_choice.pk, "explanation": question.explanation})


@login_required
@require_POST
def practice_submit(request, pk):
    """ثبت پاسخ‌های تمرین و محاسبه نتایج."""
    practice_session = get_object_or_404(PracticeSession, pk=pk, user=request.user)

    if practice_session.status == PracticeSession.Status.DONE:
        return redirect(f"{APP_NS}:practice_result", pk=practice_session.pk)

    questions = practice_session.questions.prefetch_related("choices").all()
    correct_count = 0
    wrong_count = 0
    skipped_count = 0

    with transaction.atomic():
        for q in questions:
            if q.question_type == Question.Type.DESCRIPTIVE:
                PracticeAnswer.objects.update_or_create(
                    session=practice_session, question=q,
                    defaults={"selected_choice": None, "text_answer": "", "is_correct": None},
                )
                continue
            selected_val = request.POST.get(f"question_{q.pk}")
            existing = PracticeAnswer.objects.filter(session=practice_session, question=q).first()
            selected_choice = existing.selected_choice if existing else None

            if selected_val and selected_val.isdigit():
                selected_choice = q.choices.filter(pk=int(selected_val)).first()

            is_correct = bool(selected_choice and selected_choice.is_correct)

            PracticeAnswer.objects.update_or_create(
                session=practice_session,
                question=q,
                defaults={
                    "selected_choice": selected_choice,
                    "text_answer": "",
                    "is_correct": is_correct,
                    "revealed": existing.revealed if existing else False,
                },
            )

            if selected_choice is None:
                skipped_count += 1
            elif is_correct:
                correct_count += 1
            else:
                wrong_count += 1

        total = practice_session.total_questions
        percent = round((correct_count / total) * 100, 1) if total and practice_session.question_type == Question.Type.MCQ else 0

        practice_session.correct_count = correct_count
        practice_session.wrong_count = wrong_count
        practice_session.skipped_count = skipped_count
        practice_session.percent = percent
        practice_session.status = PracticeSession.Status.DONE
        practice_session.finished_at = timezone.now()
        practice_session.save(
            update_fields=[
                "correct_count",
                "wrong_count",
                "skipped_count",
                "percent",
                "status",
                "finished_at",
            ]
        )

    return redirect(f"{APP_NS}:practice_result", pk=practice_session.pk)


@login_required
@require_http_methods(["GET"])
def practice_result(request, pk):
    """کارنامه تمرین."""
    practice_session = get_object_or_404(PracticeSession, pk=pk, user=request.user)
    answers = {
        ans.question_id: ans
        for ans in practice_session.answers.select_related("selected_choice")
    }

    review = []
    for q in practice_session.questions.prefetch_related("choices").all():
        ans = answers.get(q.pk)
        review.append({
            "question": q,
            "answer": ans,
            "selected_choice": ans.selected_choice if ans else None,
            "is_correct": ans.is_correct if ans else False,
            "text_answer": ans.text_answer if ans else "",
        })

    return render(
        request,
        "questions_module/practice_result.html",
        {
            "session": practice_session,
            "review": review,
            "performance": _performance_data(request.user, practice_session.chapters.values_list("subject_id", flat=True)),
            "exam_progress": _exam_progress_data(request.user, practice_session.chapters.values_list("subject_id", flat=True)),
        },
    )


# ============================================================
# بخش آزمون (Exam)
# ============================================================

@login_required
@require_http_methods(["GET"])
def start_exam(request):
    """شروع جلسه آزمون با محاسبه تایمر زمانی."""
    mode = _get_wizard_mode(request)
    chapter_ids = _get_wizard_chapter_ids(request)
    topic_ids = _get_wizard_topic_ids(request)
    question_type = _get_wizard_question_type(request)
    difficulty_counts = _get_difficulty_counts(request)

    if mode != "exam" or not topic_ids or not difficulty_counts or not question_type:
        messages.warning(request, "لطفاً ابتدا تنظیمات آزمون را تکمیل کنید.")
        return redirect(f"{APP_NS}:choose_mode")

    allowed_topics = _usable_topics(question_type=question_type, user=request.user)
    if set(topic_ids) != set(allowed_topics.filter(pk__in=topic_ids).values_list('pk', flat=True)):
        _reset_wizard(request)
        return redirect(f"{APP_NS}:select_courses")
    chapter_ids = list(allowed_topics.filter(pk__in=topic_ids).values_list('chapter_id', flat=True).distinct())
    questions, missing_difficulty = _select_questions(topic_ids, difficulty_counts, question_type, user=request.user)

    if not questions:
        label = dict(Difficulty.choices).get(missing_difficulty, "انتخاب‌شده")
        messages.error(request, f"تعداد سؤال کافی برای سطح {label} موجود نیست.")
        return redirect(f"{APP_NS}:select_chapters")

    duration = timedelta(minutes=MINUTES_PER_EXAM_QUESTION * len(questions))

    with transaction.atomic():
        exam_session = ExamSession.objects.create(
            user=request.user,
            total_questions=len(questions),
            ends_at=timezone.now() + duration,
            requested_difficulty=next(iter(difficulty_counts)) if len(difficulty_counts) == 1 else "",
            difficulty_breakdown=difficulty_counts,
            question_type=question_type,
        )
        exam_session.chapters.set(Chapter.objects.filter(id__in=chapter_ids))
        exam_session.topics.set(Topic.objects.filter(id__in=topic_ids))
        exam_session.questions.set(questions)

    _reset_wizard(request)
    return redirect(f"{APP_NS}:exam_session", pk=exam_session.pk)


@login_required
@require_http_methods(["GET"])
def exam_session_view(request, pk):
    """صفحه پاسخگویی به آزمون به همراه زمان باقی‌مانده."""
    exam_session = get_object_or_404(
        ExamSession.objects.prefetch_related("questions__choices"),
        pk=pk,
        user=request.user,
    )

    if exam_session.status == ExamSession.Status.DONE:
        return redirect(f"{APP_NS}:exam_result", pk=exam_session.pk)

    # پایان خودکار در صورت اتمام وقت
    if not exam_session.ends_at or timezone.now() >= exam_session.ends_at:
        _finish_exam(exam_session, {}, auto_submitted=True)
        return redirect(f"{APP_NS}:exam_result", pk=exam_session.pk)

    remaining_seconds = max(
        int((exam_session.ends_at - timezone.now()).total_seconds()),
        0,
    )
    saved_answers = {
        answer.question_id: answer
        for answer in exam_session.answers.select_related("selected_choice")
    }
    questions = list(exam_session.questions.prefetch_related("choices").all())
    previous_answers = _previous_answer_map(
        request.user,
        [question.pk for question in questions],
        exam_session_id=exam_session.pk,
    )
    items = [
        {
            "question": question,
            "answer": saved_answers.get(question.pk),
            "previous": previous_answers.get(question.pk),
        }
        for question in questions
    ]

    return render(
        request,
        "questions_module/exam_session.html",
        {
            "session": exam_session,
            "items": items,
            "remaining_seconds": remaining_seconds,
        },
    )


def _finish_exam(exam_session, submitted_data, auto_submitted=False):
    """تابع کمکی جهت ثبت و نهایی‌کردن نتایج آزمون."""
    with transaction.atomic():
        locked_session = ExamSession.objects.select_for_update().get(pk=exam_session.pk)

        if locked_session.status == ExamSession.Status.DONE:
            return locked_session

        questions = locked_session.questions.prefetch_related("choices").all()
        correct_count = 0
        wrong_count = 0
        unanswered_count = 0

        for q in questions:
            key = f"question_{q.pk}"
            existing = ExamAnswer.objects.filter(session=locked_session, question=q).first()
            if q.question_type == Question.Type.DESCRIPTIVE:
                ExamAnswer.objects.update_or_create(
                    session=locked_session, question=q,
                    defaults={"selected_choice": None, "text_answer": "", "is_correct": None},
                )
                continue
            selected_val = submitted_data.get(key) if key in submitted_data else None
            selected_choice = existing.selected_choice if existing else None

            if selected_val and str(selected_val).isdigit():
                selected_choice = q.choices.filter(pk=int(selected_val)).first()

            is_correct = bool(selected_choice and selected_choice.is_correct)

            ExamAnswer.objects.update_or_create(
                session=locked_session,
                question=q,
                defaults={
                    "selected_choice": selected_choice,
                    "text_answer": "",
                    "is_correct": is_correct,
                },
            )

            if selected_choice is None:
                unanswered_count += 1
            elif is_correct:
                correct_count += 1
            else:
                wrong_count += 1

        total = locked_session.total_questions
        percent = round((correct_count / total) * 100, 1) if total and locked_session.question_type == Question.Type.MCQ else 0

        locked_session.correct_count = correct_count
        locked_session.wrong_count = wrong_count
        locked_session.unanswered_count = unanswered_count
        locked_session.percent = percent
        locked_session.status = ExamSession.Status.DONE
        locked_session.finished_at = timezone.now()
        locked_session.auto_submitted = auto_submitted
        locked_session.save(
            update_fields=[
                "correct_count",
                "wrong_count",
                "unanswered_count",
                "percent",
                "status",
                "finished_at",
                "auto_submitted",
            ]
        )

    return locked_session


@login_required
@require_POST
def exam_save_answer(request, pk, question_id):
    """ذخیره خودکار پاسخ آزمون برای جلوگیری از اتلاف پاسخ‌ها در پایان زمان."""
    exam_session = get_object_or_404(ExamSession, pk=pk, user=request.user, status=ExamSession.Status.IN_PROGRESS)
    if not exam_session.ends_at or timezone.now() >= exam_session.ends_at:
        _finish_exam(exam_session, {}, auto_submitted=True)
        return JsonResponse({"expired": True}, status=409)
    question = get_object_or_404(exam_session.questions.all(), pk=question_id)
    if question.question_type == Question.Type.DESCRIPTIVE:
        return JsonResponse({"error": "سؤال تشریحی در این حالت نیاز به ثبت پاسخ ندارد."}, status=400)
    choice = get_object_or_404(question.choices, pk=request.POST.get("choice"))
    ExamAnswer.objects.update_or_create(
        session=exam_session,
        question=question,
        defaults={"selected_choice": choice, "text_answer": "", "is_correct": choice.is_correct},
    )
    return JsonResponse({"saved": True})


@login_required
@require_POST
def exam_submit(request, pk):
    """ثبت دستی فرم آزمون."""
    exam_session = get_object_or_404(ExamSession, pk=pk, user=request.user)

    if exam_session.status == ExamSession.Status.DONE:
        return redirect(f"{APP_NS}:exam_result", pk=exam_session.pk)

    auto_submitted = not exam_session.ends_at or timezone.now() >= exam_session.ends_at
    _finish_exam(exam_session, request.POST, auto_submitted=auto_submitted)

    return redirect(f"{APP_NS}:exam_result", pk=exam_session.pk)


@login_required
@require_http_methods(["GET"])
def exam_result(request, pk):
    """کارنامه نتایج آزمون."""
    exam_session = get_object_or_404(ExamSession, pk=pk, user=request.user)

    if exam_session.status != ExamSession.Status.DONE:
        return redirect(f"{APP_NS}:exam_session", pk=exam_session.pk)

    answers = {
        ans.question_id: ans
        for ans in exam_session.answers.select_related("selected_choice")
    }

    review = []
    for q in exam_session.questions.prefetch_related("choices").all():
        ans = answers.get(q.pk)
        review.append({
            "question": q,
            "answer": ans,
            "selected_choice": ans.selected_choice if ans else None,
            "is_correct": ans.is_correct if ans else False,
            "text_answer": ans.text_answer if ans else "",
        })

    return render(
        request,
        "questions_module/exam_result.html",
        {
            "session": exam_session,
            "review": review,
            "performance": _performance_data(request.user, exam_session.chapters.values_list("subject_id", flat=True)),
            "exam_progress": _exam_progress_data(request.user, exam_session.chapters.values_list("subject_id", flat=True)),
        },
    )
