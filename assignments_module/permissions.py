from auth_module.models import Student, UserRole
from django.db.models import Q


def is_admin(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.role == UserRole.ADMIN)


def can_create(user):
    return is_admin(user) or (
        user.is_authenticated and user.is_active
        and user.role in (UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE)
        and user.accesses.filter(name='assignment', subject__isnull=True).exists()
    )


def can_review(user):
    return is_admin(user) or (
        user.is_authenticated and user.is_active
        and (user.role == UserRole.CONTENT_MODERATOR
             or (user.role == UserRole.PROVINCE_TRUSTEE and can_create(user)))
    )


def can_enter(user):
    return user.is_authenticated and user.is_active and (
        user.role == UserRole.STUDENT or can_create(user) or can_review(user)
    )


def students_for(user):
    students = Student.objects.filter(user__is_active=True, user__role=UserRole.STUDENT).select_related('user', 'field__grade__school__province')
    if is_admin(user):
        return students
    if not can_create(user):
        return students.none()
    if user.role == UserRole.PROVINCE_TRUSTEE:
        return students.filter(field__grade__school__province__trustees__user=user).distinct()
    return students.filter(consultant_assignment__consultant=user)


def reviewable(user, queryset):
    if not can_review(user):
        return queryset.none()
    if is_admin(user) or user.role == UserRole.CONTENT_MODERATOR:
        return queryset
    provinces = user.trustee_provinces.values_list('province_id', flat=True)
    outside = Student.objects.exclude(field__grade__school__province_id__in=provinces)
    return queryset.filter(students__isnull=False).exclude(students__in=outside).distinct()


def visible_to(user, queryset):
    if not can_enter(user):
        return queryset.none()
    if is_admin(user):
        return queryset
    if user.role == UserRole.STUDENT:
        return queryset.filter(status='published', students__user=user).distinct()
    if can_review(user):
        return queryset.filter(Q(pk__in=reviewable(user, queryset).values('pk')) | Q(creator=user))
    return queryset.filter(creator=user)


def can_grade(user, assignment):
    return is_admin(user) or (can_create(user) and assignment.creator_id == user.pk)


def navigation(request):
    return {'can_view_assignments': can_enter(request.user)}
