import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from auth_module.models import UserRole
from courses_module.models import ApprovalStatus, CourseEnrollment
from plans_module.models import PlanCompletion, PlanEntry
from plans_module.services import current_week_start, student_profile_for
from counseling_module.models import StudentConsultantAssignment
from counseling_module.services import manageable_students_for
from .models import ModuleAvailability, UserPresence

# ایمپورت مدل اخبار
from news_module.models import Article

# ایمپورت مدل وبلاگ
try:
    from blog.models import Post
except ModuleNotFoundError:
    from blog_module.models import Post


@login_required
def dash_view(request):
    context = {}
    now = timezone.now()

    # دریافت آخرین اخبار منتشر شده (۴ خبر اخیر)
    context["latest_news"] = (
        Article.objects.filter(
            status=Article.Status.PUBLISHED,
        )
        .filter(Q(published_at__lte=now) | Q(published_at__isnull=True))
        .select_related("category")
        .order_by("-published_at", "-created_at")[:4]
    )

    # دریافت آخرین مقالات منتشر شده وبلاگ (۴ پست اخیر)
    context["latest_posts"] = (
        Post.published.select_related("category", "author")
        .order_by("-published_at", "-created_at")[:4]
    )

    if request.user.role == UserRole.STUDENT:
        context["my_course_enrollments"] = CourseEnrollment.objects.filter(
            student=request.user,
            course__approval_status=ApprovalStatus.APPROVED,
            course__is_active=True,
        ).select_related("course", "course__author")[:4]
        context["my_courses_count"] = CourseEnrollment.objects.filter(student=request.user).count()
        student = student_profile_for(request.user)
        if student:
            context["consultant_assignment"] = StudentConsultantAssignment.objects.filter(
                student=student
            ).select_related("consultant").first()
            today_index = (timezone.localdate().weekday() + 2) % 7
            today_plans = PlanEntry.objects.filter(
                student=student, weekday=today_index, is_active=True
            ).select_related("subject", "assigned_by")
            completed_ids = set(
                PlanCompletion.objects.filter(
                    entry__student=student, week_start=current_week_start()
                ).values_list("entry_id", flat=True)
            )
            context["today_plans"] = today_plans[:4]
            context["today_plans_count"] = today_plans.count()
            context["today_completed_count"] = today_plans.filter(pk__in=completed_ids).count()
    elif request.user.role == UserRole.CONSULTANT:
        assignments = StudentConsultantAssignment.objects.filter(
            consultant=request.user
        ).select_related("student__user", "student__field__grade")
        context["my_students_count"] = assignments.count()
        context["recent_student_assignments"] = assignments.order_by("-updated_at")[:4]
    elif request.user.role == UserRole.PROVINCE_TRUSTEE:
        manageable = manageable_students_for(request.user)
        context["province_students_count"] = manageable.count()
        context["province_unassigned_count"] = manageable.filter(
            consultant_assignment__isnull=True
        ).count()
    if request.user.is_superuser or request.user.role == UserRole.ADMIN:
        context["all_students_count"] = manageable_students_for(request.user).count()
        context["all_unassigned_count"] = manageable_students_for(request.user).filter(
            consultant_assignment__isnull=True
        ).count()
        from .models import PresencePeak
        context["recent_presence_peaks"] = PresencePeak.objects.order_by("-recorded_at")[:5]
        context["presence_api_url"] = reverse("dashboard_presence")

    return render(request, "dashboard_module/dash.html", context)


@login_required
def presence_status(request):
    if not (request.user.is_superuser or request.user.role == UserRole.ADMIN):
        raise PermissionDenied
    online_since = timezone.now() - timezone.timedelta(minutes=5)
    presences = UserPresence.objects.filter(last_seen__gte=online_since, user__is_active=True).select_related("user").order_by("user__role", "user__first_name", "user__last_name", "user__username")
    groups = {"superusers": [], "admins": [], "consultants": [], "trustees": [], "moderators": [], "students": []}
    role_groups = {UserRole.STUDENT: "students", UserRole.CONSULTANT: "consultants", UserRole.ADMIN: "admins", UserRole.PROVINCE_TRUSTEE: "trustees", UserRole.CONTENT_MODERATOR: "moderators"}
    for presence in presences:
        user = presence.user
        group_name = "superusers" if user.is_superuser else role_groups.get(user.role)
        if group_name:
            groups[group_name].append({"id": user.pk, "name": user.get_full_name().strip() or user.username, "username": user.username, "last_seen": presence.last_seen.isoformat()})
    return JsonResponse({"updated_at": timezone.now().isoformat(), "groups": {key: {"count": len(users), "users": users} for key, users in groups.items()}})


@login_required
def module_settings(request):
    if not (request.user.is_superuser or request.user.role == UserRole.ADMIN):
        raise PermissionDenied
    states = dict(ModuleAvailability.objects.values_list("code", "is_active"))
    modules = [{"code": code, "label": label, "active": states.get(code, True)}
               for code, label in ModuleAvailability.Code.choices]
    return render(request, "dashboard_module/module_settings.html", {"modules": modules})


@login_required
@require_POST
def toggle_module(request, code):
    if not (request.user.is_superuser or request.user.role == UserRole.ADMIN):
        return redirect("dashboard")
    valid_codes = {item for item, _label in ModuleAvailability.Code.choices}
    if code not in valid_codes:
        return redirect("dashboard")
    state, _created = ModuleAvailability.objects.get_or_create(code=code)
    state.is_active = not state.is_active
    state.save(update_fields=["is_active", "updated_at"])
    return redirect("module_settings")


@login_required
@require_POST
def mark_content_notifications_read(request):
    try:
        payload = json.loads(request.body) if request.content_type == "application/json" and request.body else {}
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False}, status=400)
    keys = payload.get("event_keys", []) if isinstance(payload, dict) else None
    if not isinstance(keys, list) or any(not isinstance(key, str) or len(key) > 200 for key in keys) or len(keys) > 100:
        return JsonResponse({"ok": False}, status=400)
    seen_at = payload.get("seen_at")
    if seen_at is not None:
        if not isinstance(seen_at, str):
            return JsonResponse({"ok": False}, status=400)
        try:
            seen_at = parse_datetime(seen_at)
        except ValueError:
            seen_at = None
        if seen_at is None or timezone.is_naive(seen_at):
            return JsonResponse({"ok": False}, status=400)
    seen = request.session.get("fanous_seen_events", [])
    request.session["fanous_seen_events"] = list(dict.fromkeys(seen + keys))[-200:]
    request.session["fanous_content_seen_at"] = min(seen_at or timezone.now(), timezone.now()).isoformat()
    request.session.modified = True
    return JsonResponse({"ok": True})


@login_required
@never_cache
@require_GET
def notification_feed(request):
    from .context_processors import fanous_shell

    return JsonResponse(fanous_shell(request)["fanous_event_feed"])
