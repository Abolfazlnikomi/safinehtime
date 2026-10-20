from urllib.parse import unquote

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Exists, FloatField, OuterRef, Prefetch, Q, Value
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from auth_module.models import Student, UserRole
from dashboard_module.location_filters import location_options
from users_module.curriculum_access import subjects_for_user

from .forms import (
    CourseCreateForm,
    CourseEpisodeForm,
    CourseSectionForm,
)
from .models import (
    ApprovalStatus,
    Course,
    CourseEnrollment,
    CourseEpisode,
    CourseRating,
    CourseSection,
    CourseView,
)


def course_cards(queryset, user):
    enrollments = CourseEnrollment.objects.filter(course=OuterRef("pk"), student=user)
    return queryset.select_related("author").prefetch_related(Prefetch('subjects', queryset=subjects_for_user(user, 'course'))).annotate(
        rating_average=Coalesce(Avg("ratings__value"), Value(0.0), output_field=FloatField()),
        rating_count=Count("ratings", distinct=True),
        is_enrolled=Exists(enrollments),
    ).order_by("-created_at")


def courses_for_region(queryset, province_id=None, school_id=None):
    if province_id:
        queryset = queryset.filter(Q(all_provinces_allowed=True) | Q(allowed_provinces__id=province_id))
    if school_id:
        queryset = queryset.filter(Q(allowed_cities__isnull=True) | Q(allowed_cities__id=school_id))
    return queryset.distinct()


def courses_for_student(queryset, user):
    student = Student.objects.filter(user=user).select_related("field__grade__school").first()
    if not student:
        return queryset.none()
    queryset = courses_for_region(queryset, student.field.grade.school.province_id, student.field.grade.school_id)
    return queryset.filter(Q(all_fields_allowed=True) | Q(allowed_fields=student.field)).filter(
        Q(all_grades_allowed=True) | Q(allowed_grades=student.field.grade)
    ).filter(Q(subjects__isnull=True) | Q(subjects__field=student.field)).distinct()


class RoleRequiredMixin(LoginRequiredMixin):
    allowed_roles = ()
    allow_superuser = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user_role = getattr(request.user, "role", None)
        if user_role not in self.allowed_roles and not (self.allow_superuser and request.user.is_superuser):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def user_can_manage_course(user, course):
    """بررسی اینکه آیا کاربر دسترسی ویرایش و مدیریت این دوره را دارد یا خیر."""
    if not user.is_authenticated:
        return False
    # ادمین و سوپریوزر دسترسی کامل دارند
    user_role = getattr(user, "role", None)
    if user.is_superuser or user_role in (UserRole.ADMIN, "ADMIN"):
        return True
    # اگر کاربر خود نویسنده دوره باشد
    if course.author_id == user.id:
        return True
    return False


class CourseListView(LoginRequiredMixin, ListView):
    model = Course
    template_name = "courses_module/course_list.html"
    context_object_name = "courses"
    paginate_by = 9

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser or self.request.user.role in (UserRole.ADMIN, UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE):
            context.update(location_options(self.request))
            context["show_location_filters"] = True
        return context

    def get_queryset(self):
        user = self.request.user
        now = timezone.now()
        published = Q(approval_status=ApprovalStatus.APPROVED, is_active=True, start_date__lte=now) & (
            Q(end_date__isnull=True) | Q(end_date__gte=now)
        )
        user_role = getattr(user, "role", None)
        if user_role == UserRole.ADMIN or user.is_superuser:
            queryset = Course.objects.all()
        elif user_role == UserRole.CONSULTANT:
            queryset = Course.objects.filter(published | Q(author=user))
        elif user_role == UserRole.PROVINCE_TRUSTEE:
            queryset = Course.objects.filter(published)
        else:
            queryset = Course.objects.filter(published)
            queryset = courses_for_student(queryset, user)
        if user.is_superuser or user_role in (UserRole.ADMIN, UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE):
            location = location_options(self.request)
            queryset = courses_for_region(queryset, location["selected_province"], location["selected_school"])
        return course_cards(queryset.distinct(), user)


class MyCoursesView(RoleRequiredMixin, ListView):
    allowed_roles = (UserRole.STUDENT, UserRole.PROVINCE_TRUSTEE, UserRole.ADMIN)
    allow_superuser = True
    model = Course
    template_name = "courses_module/my_courses.html"
    context_object_name = "courses"

    def get_queryset(self):
        user_role = getattr(self.request.user, "role", None)
        if user_role == UserRole.ADMIN or self.request.user.is_superuser:
            queryset = Course.objects.all().select_related("author").prefetch_related(
                "subjects", "allowed_grades", "allowed_fields", "allowed_provinces", "allowed_cities", "sections"
            ).annotate(
                admin_enrollment_count=Count("enrollments", distinct=True),
                admin_viewer_count=Count("views", distinct=True),
                admin_section_count=Count("sections", distinct=True),
            )
            location = location_options(self.request)
            return courses_for_region(queryset, location["selected_province"], location["selected_school"])
        if user_role == UserRole.PROVINCE_TRUSTEE:
            return Course.objects.filter(approval_status=ApprovalStatus.PENDING).select_related(
                "author").prefetch_related(
                "subjects", "allowed_grades", "allowed_fields"
            )
        queryset = courses_for_student(Course.published.filter(enrollments__student=self.request.user), self.request.user)
        return course_cards(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_role = getattr(self.request.user, "role", None)
        context["is_trustee_queue"] = user_role == UserRole.PROVINCE_TRUSTEE
        context["is_admin_catalog"] = user_role == UserRole.ADMIN or self.request.user.is_superuser
        if context["is_admin_catalog"]:
            context.update(location_options(self.request))
            context["show_location_filters"] = True
            context["admin_total_courses"] = self.get_queryset().count()
            context["admin_published_courses"] = self.get_queryset().filter(approval_status=ApprovalStatus.APPROVED,
                                                                            is_active=True).count()
            context["admin_pending_courses"] = self.get_queryset().filter(
                approval_status=ApprovalStatus.PENDING).count()
        return context


class CourseDetailView(LoginRequiredMixin, DetailView):
    model = Course
    template_name = "courses_module/course_detail.html"
    context_object_name = "course"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_object(self, queryset=None):
        slug = unquote(self.kwargs.get(self.slug_url_kwarg))
        if queryset is None:
            queryset = self.get_queryset()
        return get_object_or_404(queryset, slug=slug)

    def get_queryset(self):
        user = self.request.user
        user_role = getattr(user, "role", None)
        if user.is_superuser or user_role in (UserRole.PROVINCE_TRUSTEE, UserRole.ADMIN):
            queryset = Course.objects.all()
        elif user_role == UserRole.CONSULTANT:
            queryset = Course.objects.filter(Q(author=user) | Q(pk__in=Course.published.all()))
        else:
            queryset = courses_for_student(Course.published.all(), user)
        return queryset.prefetch_related(Prefetch('subjects', queryset=subjects_for_user(user, 'course')))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        course = self.object
        if user.role == UserRole.STUDENT and not user.is_superuser:
            CourseView.objects.get_or_create(course=course, user=user)

        can_manage = user_can_manage_course(user, course)
        sections_qs = course.sections.all()
        if not can_manage:
            sections_qs = sections_qs.filter(is_active=True)

        context["sections"] = sections_qs.prefetch_related("episodes").order_by("order", "id")
        context["rating_average"] = course.average_rating
        context["rating_count"] = course.ratings.count()
        enrollment = course.enrollments.filter(student=user).first()
        context["is_enrolled"] = enrollment is not None
        if enrollment and getattr(user, "role", None) == UserRole.STUDENT:
            viewed_at = timezone.now()
            if enrollment.first_viewed_at is None:
                enrollment.first_viewed_at = viewed_at
            enrollment.last_viewed_at = viewed_at
            enrollment.view_count += 1
            enrollment.save(update_fields=["first_viewed_at", "last_viewed_at", "view_count"])
        rating = course.ratings.filter(student=user).first()
        context["user_rating"] = rating.value if rating else 0

        context["can_manage"] = can_manage
        context["can_delete"] = can_manage
        if user.is_superuser or getattr(user, "role", None) == UserRole.ADMIN:
            context["view_status_enrollments"] = course.enrollments.select_related("student").order_by("student__last_name", "student__first_name")
        elif getattr(user, "role", None) == UserRole.CONSULTANT:
            context["view_status_enrollments"] = course.enrollments.filter(
                student__student_profiles__consultant_assignment__consultant=user
            ).select_related("student").distinct().order_by("student__last_name", "student__first_name")
        return context


class CourseCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserRole.CONSULTANT, UserRole.ADMIN)
    allow_superuser = True
    model = Course
    form_class = CourseCreateForm
    template_name = "courses_module/course_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.author = self.request.user
        is_admin_or_super = self.request.user.is_superuser or getattr(self.request.user, "role", None) == UserRole.ADMIN

        if is_admin_or_super:
            form.instance.approval_status = ApprovalStatus.APPROVED
            form.instance.is_active = True
            if not form.instance.start_date:
                form.instance.start_date = timezone.now()
        else:
            form.instance.approval_status = ApprovalStatus.PENDING

        base_slug = slugify(form.cleaned_data["title"], allow_unicode=True) or "course"
        slug = base_slug
        counter = 2
        while Course.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        form.instance.slug = slug

        response = super().form_valid(form)
        subjects = form.cleaned_data.get("subjects")
        if subjects:
            self.object.topic = "، ".join(subjects.values_list("title", flat=True))[:255]
            self.object.save(update_fields=["topic"])

        if is_admin_or_super:
            messages.success(self.request, "دوره با موفقیت ایجاد و مستقیماً تایید و منتشر شد.")
        else:
            messages.success(self.request, "درخواست ساخت دوره ثبت شد و برای تأیید به معتمد استان ارسال گردید.")
        return response

    def get_success_url(self):
        return reverse_lazy("courses_module:course_detail", kwargs={"slug": self.object.slug})


class CourseUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserRole.CONSULTANT, UserRole.PROVINCE_TRUSTEE, UserRole.ADMIN)
    allow_superuser = True
    model = Course
    form_class = CourseCreateForm
    template_name = "courses_module/course_form.html"

    def get_queryset(self):
        user = self.request.user
        user_role = getattr(user, "role", None)
        if user.is_superuser or user_role == UserRole.ADMIN:
            return Course.objects.all()
        if user_role == UserRole.CONSULTANT:
            return Course.objects.filter(author=user)
        if user_role == UserRole.PROVINCE_TRUSTEE:
            return Course.objects.all()
        return Course.objects.none()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse_lazy("courses_module:course_detail", kwargs={"slug": self.object.slug})

    def form_valid(self, form):
        user_role = getattr(self.request.user, "role", None)
        if user_role == UserRole.CONSULTANT and self.object.approval_status != ApprovalStatus.PENDING:
            form.instance.approval_status = ApprovalStatus.PENDING
            form.instance.reviewed_by = None
            form.instance.reviewed_at = None
        elif user_role == UserRole.ADMIN or self.request.user.is_superuser:
            form.instance.approval_status = ApprovalStatus.APPROVED

        response = super().form_valid(form)
        subjects = form.cleaned_data.get("subjects")
        if subjects is not None:
            self.object.topic = "، ".join(subjects.values_list("title", flat=True))[:255]
            self.object.save(update_fields=["topic"])
        messages.success(self.request, "تغییرات دوره با موفقیت ذخیره شد.")
        return response


class CourseDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        if not user_can_manage_course(request.user, course):
            raise PermissionDenied
        title = course.title
        course.delete()
        messages.success(request, f"دوره «{title}» با موفقیت حذف شد.")
        return redirect("courses_module:course_list")


class EnrollCourseView(RoleRequiredMixin, View):
    allowed_roles = (UserRole.STUDENT, UserRole.ADMIN)
    allow_superuser = True

    def post(self, request, slug):
        decoded_slug = unquote(slug)
        is_admin_or_super = request.user.is_superuser or getattr(request.user, "role", None) == UserRole.ADMIN

        with transaction.atomic():
            if is_admin_or_super:
                course = get_object_or_404(Course.objects.select_for_update(), slug=decoded_slug)
            else:
                course = get_object_or_404(
                    courses_for_student(Course.published.select_for_update(), request.user), slug=decoded_slug
                )

            if CourseEnrollment.objects.filter(course=course, student=request.user).exists():
                messages.info(request, "این دوره از قبل در دوره‌های من قرار دارد.")
                return redirect("courses_module:course_detail", slug=course.slug)

            if not is_admin_or_super and course.capacity is not None and course.enrollments.count() >= course.capacity:
                messages.error(request, "ظرفیت این دوره تکمیل شده است.")
                return redirect("courses_module:course_detail", slug=course.slug)

            CourseEnrollment.objects.create(course=course, student=request.user)

        messages.success(request, "دوره با موفقیت به «دوره‌های من» اضافه شد.")
        return redirect("courses_module:course_detail", slug=course.slug)


class RateCourseView(RoleRequiredMixin, View):
    allowed_roles = (UserRole.STUDENT, UserRole.ADMIN)
    allow_superuser = True

    def post(self, request, slug):
        decoded_slug = unquote(slug)
        course = get_object_or_404(Course.objects.all(), slug=decoded_slug)
        if not CourseEnrollment.objects.filter(course=course, student=request.user).exists():
            messages.error(request, "برای امتیاز دادن ابتدا در دوره شرکت کنید.")
            return redirect("courses_module:course_detail", slug=course.slug)
        try:
            value = int(request.POST.get("rating", ""))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("امتیاز نامعتبر است.")
        if value not in range(1, 6):
            return HttpResponseBadRequest("امتیاز باید بین ۱ تا ۵ باشد.")
        CourseRating.objects.update_or_create(course=course, student=request.user, defaults={"value": value})
        messages.success(request, "امتیاز شما ثبت شد.")
        return redirect("courses_module:course_detail", slug=course.slug)


class CourseReviewListView(RoleRequiredMixin, ListView):
    allowed_roles = (UserRole.PROVINCE_TRUSTEE, UserRole.ADMIN)
    allow_superuser = True
    model = Course
    template_name = "courses_module/review_list.html"
    context_object_name = "courses"

    def get_queryset(self):
        return Course.objects.filter(approval_status=ApprovalStatus.PENDING).select_related("author").prefetch_related(
            "subjects", "allowed_grades", "allowed_fields"
        )


class CourseReviewView(RoleRequiredMixin, View):
    allowed_roles = (UserRole.PROVINCE_TRUSTEE, UserRole.ADMIN)
    allow_superuser = True

    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk, approval_status=ApprovalStatus.PENDING)
        action = request.POST.get("action")
        if action == "approve":
            course.approval_status = ApprovalStatus.APPROVED
            course.rejection_reason = ""
            message = "دوره تأیید و منتشر شد."
        elif action == "reject":
            course.approval_status = ApprovalStatus.REJECTED
            course.rejection_reason = request.POST.get("reason", "").strip()
            message = "دوره رد شد و نتیجه برای مشاور ثبت گردید."
        else:
            return HttpResponseBadRequest("عملیات نامعتبر است.")
        course.reviewed_by = request.user
        course.reviewed_at = timezone.now()
        course.save(update_fields=["approval_status", "rejection_reason", "reviewed_by", "reviewed_at"])
        messages.success(request, message)
        return redirect("courses_module:review_list")


class AdminCourseCloseView(RoleRequiredMixin, View):
    allowed_roles = (UserRole.ADMIN,)
    allow_superuser = True

    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        course.is_active = not course.is_active
        course.save(update_fields=["is_active", "updated_at"])
        status_msg = "فعال و بازگشایی شد." if course.is_active else "بسته شد و از دید دانش‌آموزان خارج گردید."
        messages.success(request, f"دوره با موفقیت {status_msg}")
        return redirect("courses_module:course_detail", slug=course.slug)


class AdminCourseDeleteView(RoleRequiredMixin, View):
    allowed_roles = (UserRole.ADMIN,)
    allow_superuser = True

    def post(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        title = course.title
        course.delete()
        messages.success(request, f"دوره «{title}» حذف شد.")
        return redirect("courses_module:course_list")


# ==============================================================================
# مدیریت سرفصل‌ها و جلسات (CourseSection & Episode)
# ==============================================================================

class SectionCreateView(LoginRequiredMixin, CreateView):
    model = CourseSection
    form_class = CourseSectionForm
    template_name = "courses_module/section_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.course = get_object_or_404(Course, pk=kwargs["course_pk"])
        if not user_can_manage_course(request.user, self.course):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.course = self.course
        messages.success(self.request, "سرفصل جدید با موفقیت ایجاد شد.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["course"] = self.course
        return context

    def get_success_url(self):
        return reverse("courses_module:course_detail", kwargs={"slug": self.course.slug})


class SectionUpdateView(LoginRequiredMixin, UpdateView):
    model = CourseSection
    form_class = CourseSectionForm
    template_name = "courses_module/section_form.html"
    pk_url_kwarg = "pk"

    def dispatch(self, request, *args, **kwargs):
        self.section = self.get_object()
        if not user_can_manage_course(request.user, self.section.course):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["course"] = self.section.course
        return context

    def form_valid(self, form):
        messages.success(self.request, "سرفصل با موفقیت ویرایش شد.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("courses_module:course_detail", kwargs={"slug": self.object.course.slug})


class SectionDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        section = get_object_or_404(CourseSection, pk=pk)
        course = section.course
        if not user_can_manage_course(request.user, course):
            raise PermissionDenied
        title = section.title
        section.delete()
        messages.success(request, f"سرفصل «{title}» حذف شد.")
        return redirect("courses_module:course_detail", slug=course.slug)


class EpisodeCreateView(LoginRequiredMixin, CreateView):
    model = CourseEpisode
    form_class = CourseEpisodeForm
    template_name = "courses_module/episode_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.section = get_object_or_404(CourseSection.objects.select_related("course"), pk=kwargs["section_pk"])
        if not user_can_manage_course(request.user, self.section.course):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.section = self.section
        messages.success(self.request, "محتوا / جلسه جدید با موفقیت افزوده شد.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section"] = self.section
        context["course"] = self.section.course
        return context

    def get_success_url(self):
        return reverse("courses_module:course_detail", kwargs={"slug": self.section.course.slug})


class EpisodeUpdateView(LoginRequiredMixin, UpdateView):
    model = CourseEpisode
    form_class = CourseEpisodeForm
    template_name = "courses_module/episode_form.html"
    pk_url_kwarg = "pk"

    def dispatch(self, request, *args, **kwargs):
        self.episode = self.get_object()
        if not user_can_manage_course(request.user, self.episode.section.course):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["section"] = self.episode.section
        context["course"] = self.episode.section.course
        return context

    def form_valid(self, form):
        messages.success(self.request, "محتوای جلسه بروزرسانی شد.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("courses_module:course_detail", kwargs={"slug": self.object.section.course.slug})


class EpisodeDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        episode = get_object_or_404(CourseEpisode.objects.select_related("section__course"), pk=pk)
        course = episode.section.course
        if not user_can_manage_course(request.user, course):
            raise PermissionDenied
        title = episode.title
        episode.delete()
        messages.success(request, f"جلسه «{title}» با موفقیت حذف شد.")
        return redirect("courses_module:course_detail", slug=course.slug)


class EpisodeDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        episode = get_object_or_404(CourseEpisode.objects.select_related("section__course"), pk=pk)
        course = episode.section.course

        is_enrolled = course.enrollments.filter(student=request.user).exists()
        can_manage = user_can_manage_course(request.user, course)

        if not (is_enrolled or can_manage):
            messages.error(request, "برای دانلود فایل ابتدا باید در این دوره ثبت‌نام کنید.")
            return redirect("courses_module:course_detail", slug=course.slug)

        if getattr(episode, "file", None) and episode.file:
            try:
                return FileResponse(episode.file.open("rb"), as_attachment=True,
                                    filename=episode.file.name.split("/")[-1])
            except FileNotFoundError:
                raise Http404("فایل مورد نظر یافت نشد.")

        if getattr(episode, "url", None) and episode.url:
            return redirect(episode.url)

        raise Http404("هیچ فایلی برای این جلسه موجود نیست.")
