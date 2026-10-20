from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from quiz_module.models import QuizAttempt
from users_module.models import Province, Subject
from .forms import GradeFormSet, NoteForm, ReportForm, SubjectForm
from .models import ReportCard
from .permissions import can_access, students_for
from .conversations import conversation_context


def require_access(user):
    if not can_access(user):
        raise PermissionDenied


def level(score):
    if score is None:
        return 'داده کافی نیست'
    if score >= 17:
        return 'عالی'
    if score >= 14:
        return 'خوب'
    if score >= 10:
        return 'متوسط'
    return 'نیازمند تقویت'


@login_required
def student_list(request):
    require_access(request.user)
    scoped = students_for(request.user).filter(user__role='STUDENT')
    students = scoped
    query = request.GET.get('q', '').strip()
    for term in query.split():
        students = students.filter(Q(user__first_name__icontains=term) | Q(user__last_name__icontains=term) | Q(user__username__icontains=term) | Q(user__phone_number__icontains=term))
    paths = {'province': 'field__grade__school__province_id', 'school': 'field__grade__school_id', 'grade': 'field__grade_id', 'field': 'field_id'}
    filters = []
    for name, path in paths.items():
        value = request.GET.get(name, '')
        if value:
            students = students.filter(**{path: int(value)}) if value.isdecimal() and len(value) < 19 else students.none()
        title_path = path.replace('_id', '__name') if name in ('province', 'school') else path.replace('_id', '__title')
        options = list(scoped.values_list(path, title_path).distinct().order_by(title_path))
        if name == 'province':
            labels = dict(Province._meta.get_field('name').choices)
            options = [(key, labels.get(label, label)) for key, label in options]
        filters.append({'name': name, 'label': {'province': 'استان', 'school': 'مدرسه', 'grade': 'پایه', 'field': 'رشته'}[name], 'options': options, 'selected': value})
    city = request.GET.get('city', '').strip()
    if city:
        students = students.filter(field__grade__school__city=city)
    page = Paginator(students.order_by('user__last_name', 'pk'), 24).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'student_records/list.html', {'page': page, 'filters': filters, 'cities': scoped.exclude(field__grade__school__city='').values_list('field__grade__school__city', flat=True).distinct().order_by('field__grade__school__city'), 'pagination_query': params.urlencode()})


@login_required
def detail(request, pk):
    require_access(request.user)
    student = get_object_or_404(students_for(request.user), pk=pk)
    action = request.POST.get('action') if request.method == 'POST' else None
    tabs = {'overview', 'reports', 'exams', 'notes', 'chats'}
    active_tab = {'report': 'reports', 'subject': 'reports', 'note': 'notes'}.get(action, request.GET.get('tab', 'overview'))
    if active_tab not in tabs:
        active_tab = 'overview'
    report_form = ReportForm(request.POST if action == 'report' else None, request.FILES if action == 'report' else None, prefix='report')
    grades = GradeFormSet(request.POST if action == 'report' else None, prefix='grades', form_kwargs={'student': student})
    note_form = NoteForm(request.POST if action == 'note' else None, prefix='note')
    subject_form = SubjectForm(request.POST if action == 'subject' else None, prefix='subject')
    saved = False
    if action == 'report':
        report_valid, grades_valid = report_form.is_valid(), grades.is_valid()
        if report_valid and grades_valid:
            with transaction.atomic():
                report = report_form.save(commit=False)
                report.student, report.created_by = student, request.user
                report.save()
                grades.instance = report
                grades.save()
            saved = True
    elif action == 'note' and note_form.is_valid():
        note = note_form.save(commit=False)
        note.student, note.author = student, request.user
        note.save()
        saved = True
    elif action == 'subject' and subject_form.is_valid():
        Subject.objects.get_or_create(field=student.field, title=subject_form.cleaned_data['title'], defaults={'is_active': True})
        saved = True
    if saved:
        messages.success(request, 'اطلاعات با موفقیت ثبت شد.')
        return redirect(f"{reverse('student_records:detail', args=[student.pk])}?tab={active_tab}")
    reports = student.report_cards.prefetch_related('grades__subject')
    attempts = QuizAttempt.objects.filter(student=student.user).exclude(status='in_progress').select_related('quiz')
    subject_values = defaultdict(lambda: {'grades': [], 'earned': Decimal('0'), 'total': Decimal('0')})
    for report in reports:
        for grade in report.grades.all():
            subject_values[grade.subject.title]['grades'].append(grade.score)
    graded_attempts = attempts.filter(status='graded').prefetch_related('answers', 'quiz__questions__bank_topic__chapter__subject', 'quiz__questions__source_question__chapter__subject')
    for attempt in graded_attempts:
        answers = {answer.question_id: answer for answer in attempt.answers.all()}
        for question in attempt.quiz.questions.all():
            chapter = question.bank_topic.chapter if question.bank_topic_id else (question.source_question.chapter if question.source_question_id else None)
            title = chapter.subject.title if chapter and chapter.subject_id else 'آزمون‌های بدون درس مشخص'
            answer = answers.get(question.pk)
            subject_values[title]['earned'] += answer.points_earned if answer else Decimal('0')
            subject_values[title]['total'] += question.points
    rows = []
    for title, values in subject_values.items():
        grade_average = sum(values['grades']) / len(values['grades']) if values['grades'] else None
        quiz_average = max(values['earned'], Decimal('0')) / values['total'] * 20 if values['total'] else None
        available = [value for value in (grade_average, quiz_average) if value is not None]
        average = sum(available) / len(available) if available else None
        rows.append({'title': title, 'grade': grade_average, 'quiz': quiz_average, 'average': average, 'level': level(average)})
    reported_averages = [report.average for report in reports if report.average is not None]
    report_average = sum(reported_averages) / len(reported_averages) if reported_averages else None
    averages = [row['average'] for row in rows if row['average'] is not None]
    overall = sum(averages) / len(averages) if averages else report_average
    context = {'student': student, 'reports': reports, 'attempts': attempts, 'rows': rows, 'overall': overall, 'report_average': report_average, 'level': level(overall), 'notes': student.record_notes.select_related('author'), 'report_form': report_form, 'grades': grades, 'note_form': note_form, 'subject_form': subject_form, 'active_tab': active_tab, 'active_action': action}
    if active_tab == 'chats':
        context.update(conversation_context(request, student))
    return render(request, 'student_records/detail.html', context)


@login_required
def report_image(request, pk, report_id):
    require_access(request.user)
    student = get_object_or_404(students_for(request.user), pk=pk)
    report = get_object_or_404(ReportCard, student=student, pk=report_id)
    if not report.image:
        raise Http404
    try:
        response = FileResponse(report.image.open('rb'))
    except FileNotFoundError:
        raise Http404
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
