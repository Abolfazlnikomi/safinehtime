from django.utils import timezone

from auth_module.models import UserRole
from .models import Assignment
from .permissions import can_enter, is_admin, visible_to


def remaining_time(deadline, now=None):
    seconds = max(0, int((deadline - (now or timezone.now())).total_seconds()))
    if seconds == 0:
        return 'مهلت ارسال تمام شد'
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f'{days} روز')
    if hours:
        parts.append(f'{hours} ساعت')
    if not days and minutes:
        parts.append(f'{minutes} دقیقه')
    text = ' و '.join(parts[:2]) if parts else 'کمتر از یک دقیقه'
    return text.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def dashboard_assignments(user, now=None):
    if not can_enter(user):
        return {'show_active_assignments': False}
    now = now or timezone.now()
    assignments = visible_to(user, Assignment.objects.filter(status=Assignment.Status.PUBLISHED, deadline__gt=now))
    if user.role == UserRole.STUDENT and not is_admin(user):
        assignments = assignments.exclude(submissions__student__user=user)
    total = assignments.count()
    items = list(assignments.select_related('creator').order_by('deadline', 'pk')[:4])
    for item in items:
        item.time_remaining = remaining_time(item.deadline, now)
        item.is_urgent = (item.deadline - now).total_seconds() <= 86400
    return {'show_active_assignments': True, 'active_assignments': items, 'active_assignments_count': total}
