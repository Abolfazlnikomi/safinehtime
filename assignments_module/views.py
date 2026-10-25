from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from auth_module.models import User, UserRole
from users_module.models import Access
from .forms import AssignmentForm, GradeForm, ReviewForm, SubmissionForm
from .models import Assignment, Review, Submission
from .permissions import can_create, can_enter, can_grade, can_review, is_admin, reviewable, visible_to


def assignment_for(user, pk):
    return get_object_or_404(visible_to(user, Assignment.objects.select_related('creator', 'reviewed_by')), pk=pk)


@login_required
def assignment_list(request):
    user = request.user
    if not can_enter(user):
        raise PermissionDenied
    assignments = visible_to(user, Assignment.objects.all())
    summary = assignments.aggregate(
        total=Count('pk', distinct=True),
        active=Count('pk', filter=Q(status='published', deadline__gt=timezone.now()), distinct=True),
        published=Count('pk', filter=Q(status='published'), distinct=True),
    )
    tab = request.GET.get('tab', 'all')
    pending_count = reviewable(user, Assignment.objects.filter(status='pending')).count()
    if tab == 'review':
        assignments = reviewable(user, Assignment.objects.filter(status='pending'))
    elif tab == 'mine':
        assignments = assignments.filter(creator=user)
    elif tab == 'open':
        assignments = assignments.filter(status='published', deadline__gt=timezone.now())
    query = request.GET.get('q', '').strip()[:200]
    if query:
        assignments = assignments.filter(title__icontains=query)
    assignments = assignments.select_related('creator').annotate(response_count=Count('submissions', distinct=True), audience_count=Count('students', distinct=True))
    page = Paginator(assignments.order_by('-created_at'), 12).get_page(request.GET.get('page'))
    return render(request, 'assignments_module/list.html', {
        'page': page, 'tab': tab, 'query': query, 'pending_count': pending_count, 'summary': summary,
        'can_create': can_create(user), 'can_review': can_review(user), 'is_admin': is_admin(user),
        'is_student': user.role == UserRole.STUDENT and not is_admin(user),
    })


@login_required
def assignment_form(request, pk=None):
    user = request.user
    if not can_create(user):
        raise PermissionDenied
    with transaction.atomic():
        instance = get_object_or_404(Assignment.objects.select_for_update(), pk=pk) if pk else None
        if instance and not can_grade(user, instance):
            raise PermissionDenied
        if instance and instance.submissions.exists():
            messages.warning(request, 'پس از دریافت اولین پاسخ، امکان تغییر تکلیف وجود ندارد.')
            return redirect('assignments:detail', pk=instance.pk)
        form = AssignmentForm(request.POST if request.method == 'POST' else None, request.FILES or None, user=user, instance=instance)
        if request.method == 'POST' and form.is_valid():
            item = form.save(commit=False)
            if not item.pk:
                item.creator = user
            if 'attachment' in request.FILES:
                item.original_filename = Path(request.FILES['attachment'].name).name[:255]
            item.status = Assignment.Status.PENDING if item.creator.role == UserRole.CONSULTANT and not is_admin(user) else Assignment.Status.PUBLISHED
            item.review_note = ''
            item.reviewed_by = None
            item.reviewed_at = None
            item.save()
            form.save_m2m()
            messages.success(request, 'تکلیف برای بررسی ارسال شد.' if item.status == 'pending' else 'تکلیف منتشر شد.')
            return redirect('assignments:detail', pk=item.pk)
    return render(request, 'assignments_module/form.html', {'form': form, 'item': instance})


@login_required
def detail(request, pk):
    item = assignment_for(request.user, pk)
    student = item.students.filter(user=request.user).order_by('pk').first() if request.user.role == UserRole.STUDENT and not is_admin(request.user) else None
    submission = item.submissions.filter(student=student).first() if student else None
    can_manage = can_grade(request.user, item)
    responses = item.submissions.select_related('student__user', 'graded_by') if can_manage else Submission.objects.none()
    can_moderate = item.status == 'pending' and reviewable(request.user, Assignment.objects.filter(pk=pk)).exists()
    return render(request, 'assignments_module/detail.html', {
        'item': item, 'student': student, 'submission': submission, 'responses': responses,
        'submission_form': SubmissionForm(), 'review_form': ReviewForm(),
        'can_manage': can_manage, 'can_moderate': can_moderate,
        'audience_count': item.students.count(), 'response_count': item.submissions.count(),
        'reviews': item.reviews.select_related('reviewer') if not student else [],
    })


@login_required
@require_POST
def submit(request, pk):
    if request.user.role != UserRole.STUDENT or is_admin(request.user):
        raise PermissionDenied
    with transaction.atomic():
        item = get_object_or_404(Assignment.objects.select_for_update(), pk=pk, status='published')
        student = item.students.filter(user=request.user).order_by('pk').first()
        if student is None:
            raise Http404
        if not item.is_open:
            messages.error(request, 'مهلت ارسال این تکلیف به پایان رسیده است.')
            return redirect('assignments:detail', pk=pk)
        if item.submissions.filter(student=student).exists():
            messages.error(request, 'پاسخ شما قبلاً ثبت شده است؛ ارسال مجدد امکان‌پذیر نیست.')
            return redirect('assignments:detail', pk=pk)
        form = SubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            response = form.save(commit=False)
            response.assignment = item
            response.student = student
            response.original_filename = Path(request.FILES['attachment'].name).name[:255]
            response.save()
            messages.success(request, 'پاسخ شما ثبت شد و برای ثبت‌کننده تکلیف ارسال شد.')
            return redirect('assignments:detail', pk=pk)
    return render(request, 'assignments_module/action.html', {'form': form, 'item': item, 'heading': 'ارسال پاسخ تکلیف', 'button': 'ثبت پاسخ', 'upload': True}, status=400)


@login_required
@require_POST
def review(request, pk):
    with transaction.atomic():
        item = get_object_or_404(Assignment.objects.select_for_update(), pk=pk)
        if not reviewable(request.user, Assignment.objects.filter(pk=pk)).exists():
            raise PermissionDenied
        if item.status != 'pending':
            messages.warning(request, 'این تکلیف قبلاً بررسی شده است.')
            return redirect('assignments:detail', pk=pk)
        form = ReviewForm(request.POST)
        if form.is_valid():
            item.status = form.cleaned_data['decision']
            item.review_note = form.cleaned_data['note']
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save(update_fields=['status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
            Review.objects.create(assignment=item, reviewer=request.user, decision=item.status, note=item.review_note)
            messages.success(request, 'نتیجه بررسی ثبت شد.')
            return redirect('assignments:detail', pk=pk)
    return render(request, 'assignments_module/action.html', {'form': form, 'item': item, 'heading': 'بررسی تکلیف', 'button': 'ثبت نتیجه'}, status=400)


@login_required
def grade(request, pk, submission_id):
    with transaction.atomic():
        item = get_object_or_404(Assignment.objects.select_for_update(), pk=pk)
        if not can_grade(request.user, item):
            raise PermissionDenied
        response = get_object_or_404(Submission.objects.select_for_update().select_related('student__user'), pk=submission_id, assignment=item)
        form = GradeForm(request.POST if request.method == 'POST' else None, assignment=item, initial={'score': response.score, 'feedback': response.feedback})
        if request.method == 'POST' and form.is_valid():
            response.score = form.cleaned_data['score']
            response.feedback = form.cleaned_data['feedback']
            response.graded_by = request.user
            response.graded_at = timezone.now()
            response.save(update_fields=['score', 'feedback', 'graded_by', 'graded_at'])
            messages.success(request, 'نمره و بازخورد برای دانش‌آموز ثبت شد.')
            return redirect('assignments:detail', pk=pk)
    return render(request, 'assignments_module/grade.html', {'form': form, 'item': item, 'response': response})


@login_required
def download(request, pk, submission_id=None):
    item = assignment_for(request.user, pk)
    if submission_id:
        response = get_object_or_404(Submission, assignment=item, pk=submission_id)
        if not can_grade(request.user, item) and not (request.user.role == UserRole.STUDENT and response.student.user_id == request.user.pk):
            raise PermissionDenied
        attachment, filename = response.attachment, response.original_filename
    else:
        attachment, filename = item.attachment, item.original_filename
    try:
        result = FileResponse(attachment.open('rb'), as_attachment=True, filename=filename, content_type='application/octet-stream')
    except FileNotFoundError:
        raise Http404('فایل یافت نشد.')
    result['X-Content-Type-Options'] = 'nosniff'
    result['Cache-Control'] = 'private, no-store'
    return result


@login_required
def access_settings(request):
    if not is_admin(request.user):
        raise PermissionDenied
    access, _created = Access.objects.get_or_create(name='assignment', subject=None)
    users = User.objects.filter(role__in=[UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE]).order_by('last_name', 'username')
    if request.method == 'POST':
        user = get_object_or_404(users, pk=request.POST.get('user_id') if request.POST.get('user_id', '').isdigit() else 0)
        if request.POST.get('enabled') == '1':
            user.accesses.add(access)
        else:
            user.accesses.remove(*user.accesses.filter(name='assignment'))
        messages.success(request, 'دسترسی کاربر به‌روزرسانی شد.')
        return redirect('assignments:access')
    users = users.annotate(has_assignment_access=Count('accesses', filter=Q(accesses__name='assignment', accesses__subject__isnull=True)))
    return render(request, 'assignments_module/access.html', {'users': users})
