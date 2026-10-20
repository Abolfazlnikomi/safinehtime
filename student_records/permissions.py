from auth_module.models import Student, UserRole


def can_access(user):
    if not user.is_authenticated or not user.is_active:
        return False
    return user.is_superuser or user.role == UserRole.ADMIN or (
        user.role in (UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE)
        and user.accesses.filter(name='student_record', subject__isnull=True).exists()
    )


def students_for(user):
    students = Student.objects.select_related('user', 'field__grade__school__province')
    if not can_access(user):
        return students.none()
    if user.is_superuser or user.role == UserRole.ADMIN:
        return students
    if user.role == UserRole.PROVINCE_TRUSTEE:
        return students.filter(field__grade__school__province__trustees__user=user).distinct()
    return students.filter(consultant_assignment__consultant=user)


def navigation(request):
    return {'can_view_student_records': can_access(request.user)}
