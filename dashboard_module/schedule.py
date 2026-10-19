from django.urls import reverse
from django.utils import timezone

from auth_module.models import UserRole
from classroom_module.models import OnlineClass
from quiz_module.models import Quiz
from quiz_module.permissions import quizzes_for_user

from .models import ModuleAvailability


def scheduled_events(user, now=None):
    now = now or timezone.now()
    events = []
    if user.is_superuser or user.role in (UserRole.ADMIN, UserRole.STUDENT, UserRole.CONSULTANT):
        classes = OnlineClass.objects.filter(is_active=True, ends_at__gt=now).order_by("starts_at")[:8]
        for item in classes:
            events.append({
                "id": f"classroom-{item.pk}", "kind": "classroom", "title": item.title,
                "starts_at": item.starts_at.isoformat(), "ends_at": item.ends_at.isoformat(),
                "url": reverse("classroom_module:join", args=[item.pk]),
                "live": item.starts_at <= now,
            })
    quizzes_enabled = not ModuleAvailability.objects.filter(
        code=ModuleAvailability.Code.QUIZZES, is_active=False
    ).exists()
    if quizzes_enabled or user.is_superuser or user.role == UserRole.ADMIN:
        quizzes = quizzes_for_user(user).filter(
            status=Quiz.Status.APPROVED, is_paused=False, closes_at__gt=now
        ).order_by("opens_at")[:8]
        for item in quizzes:
            events.append({
                "id": f"quiz-{item.pk}", "kind": "quiz", "title": item.title,
                "starts_at": item.opens_at.isoformat(), "ends_at": item.closes_at.isoformat(),
                "url": reverse("quiz_module:quiz_list"),
                "live": item.opens_at <= now,
            })
    return sorted(events, key=lambda event: event["starts_at"])


def event_notification(event):
    started = event["live"]
    label = "کلاس" if event["kind"] == "classroom" else "آزمون"
    return {
        "key": f'{event["id"]}:{event["starts_at"]}:{"live" if started else "upcoming"}',
        "kind": event["kind"],
        "title": f'{label} {"شروع شد" if started else "پیش رو"}: {event["title"]}',
        "published_at": event["starts_at"], "url": event["url"],
        "description": f'{label} {"شروع شد؛ اکنون می‌توانی وارد شوی" if started else "به‌زودی شروع می‌شود"}',
    }
