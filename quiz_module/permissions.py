from django.db.models import Count, Q

from auth_module.models import ProvinceTrustee, UserRole
from users_module.models import Access

from .models import Quiz


def is_quiz_admin(user):
    return user.is_authenticated and (user.is_superuser or user.role == UserRole.ADMIN)


def can_create_quiz(user):
    if is_quiz_admin(user):
        return True
    return user.is_authenticated and user.role == UserRole.CONSULTANT and user.has_project_access(Access.Code.QUIZ)


def trustee_province_ids(user):
    if not user.is_authenticated or user.role != UserRole.PROVINCE_TRUSTEE:
        return []
    return list(ProvinceTrustee.objects.filter(user=user).values_list("province_id", flat=True))


def can_manage_quiz(user, quiz):
    if is_quiz_admin(user):
        return True
    return can_create_quiz(user) and quiz.creator_id == user.id


def can_edit_quiz_questions(user, quiz):
    """Question editing is also available to the trustee of an approved quiz."""
    if can_manage_quiz(user, quiz):
        return True
    return (
        user.is_authenticated
        and user.role == UserRole.PROVINCE_TRUSTEE
        and quiz.status == quiz.Status.APPROVED
        and quiz.province_id in trustee_province_ids(user)
    )


def can_review_quiz(user, quiz):
    if is_quiz_admin(user):
        return True
    return quiz.province_id in trustee_province_ids(user)


def can_view_results(user, quiz):
    return is_quiz_admin(user) or quiz.creator_id == user.id or can_review_quiz(user, quiz)


def quizzes_for_user(user):
    queryset = Quiz.objects.select_related("creator", "province", "school", "grade", "field").annotate(attempt_count=Count("attempts", distinct=True))
    if is_quiz_admin(user):
        return queryset
    if user.role == UserRole.CONSULTANT:
        return queryset.filter(creator=user)
    if user.role == UserRole.PROVINCE_TRUSTEE:
        return queryset.filter(province_id__in=trustee_province_ids(user))
    if user.role == UserRole.STUDENT:
        profile_fields = user.student_profiles.values_list("field_id", flat=True)
        profile_grades = user.student_profiles.values_list("field__grade_id", flat=True)
        profile_schools = user.student_profiles.values_list("field__grade__school_id", flat=True)
        profile_provinces = user.student_profiles.values_list("field__grade__school__province_id", flat=True)
        return queryset.filter(status=Quiz.Status.APPROVED).filter(
            Q(all_students=True) | Q(assigned_students=user) |
            (Q(assigned_students__isnull=True) &
             (Q(field__isnull=True) | Q(field_id__in=profile_fields)) &
             (Q(grade__isnull=True) | Q(grade_id__in=profile_grades)) &
             (Q(school__isnull=True) | Q(school_id__in=profile_schools)) &
             (Q(province__isnull=True) | Q(province_id__in=profile_provinces)))
        ).distinct()
    return queryset.none()
