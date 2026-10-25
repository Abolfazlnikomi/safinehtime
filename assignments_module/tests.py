from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

import jdatetime
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auth_module.models import ProvinceTrustee, Student, User, UserRole
from counseling_module.models import StudentConsultantAssignment
from users_module.models import Access, FieldOfStudy, Grade, Province, School, Subject
from .forms import AssignmentForm, GradeForm, SubmissionForm
from .models import Assignment, Review, Submission
from .permissions import can_create, reviewable
from .services import dashboard_assignments, remaining_time


class AssignmentFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='hw-admin', role=UserRole.ADMIN)
        cls.consultant = User.objects.create_user(username='hw-consultant', role=UserRole.CONSULTANT)
        cls.other_consultant = User.objects.create_user(username='hw-other-consultant', role=UserRole.CONSULTANT)
        cls.trustee = User.objects.create_user(username='hw-trustee', role=UserRole.PROVINCE_TRUSTEE)
        cls.moderator = User.objects.create_user(username='hw-moderator', role=UserRole.CONTENT_MODERATOR)
        cls.student_user = User.objects.create_user(username='hw-student', role=UserRole.STUDENT)
        cls.other_user = User.objects.create_user(username='hw-other-student', role=UserRole.STUDENT)
        cls.province = Province.objects.create(name='kerman')
        cls.other_province = Province.objects.create(name='hormozgan')
        school = School.objects.create(name='Homework school', province=cls.province)
        other_school = School.objects.create(name='Other homework school', province=cls.other_province)
        grade = Grade.objects.create(title='Homework grade', school=school)
        other_grade = Grade.objects.create(title='Other grade', school=other_school)
        field = FieldOfStudy.objects.create(title='Homework field', grade=grade)
        other_field = FieldOfStudy.objects.create(title='Other field', grade=other_grade)
        cls.student = Student.objects.create(user=cls.student_user, field=field)
        cls.other_student = Student.objects.create(user=cls.other_user, field=other_field)
        StudentConsultantAssignment.objects.create(student=cls.student, consultant=cls.consultant, assigned_by=cls.admin)
        ProvinceTrustee.objects.create(user=cls.trustee, province=cls.province)
        cls.access, _created = Access.objects.get_or_create(name='assignment', subject=None)
        for user in (cls.consultant, cls.trustee, cls.other_consultant):
            user.accesses.add(cls.access)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        storage = FileSystemStorage(location=self.directory.name)
        for model in (Assignment, Submission):
            field = model._meta.get_field('attachment')
            original = field.storage
            field.storage = storage
            self.addCleanup(setattr, field, 'storage', original)

    def upload(self, name='homework.anything'):
        return SimpleUploadedFile(name, b'Homework file', content_type='application/octet-stream')

    def assignment(self, creator=None, status='published', students=None):
        item = Assignment.objects.create(
            title='تمرین جدید', description='راهنمای تمرین', creator=creator or self.admin,
            attachment=self.upload(), original_filename='homework.anything',
            deadline=timezone.now() + timedelta(days=2), max_score=20, status=status,
        )
        item.students.set(students if students is not None else [self.student])
        return item

    def data(self, students=None):
        tomorrow = timezone.localdate() + timedelta(days=2)
        return {
            'title': 'تکلیف آزمایشی', 'description': 'توضیح تکلیف', 'attachment': self.upload(),
            'students': students or [self.student.pk], 'max_score': '20',
            'due_date': jdatetime.date.fromgregorian(date=tomorrow).strftime('%Y/%m/%d'), 'due_time': '23:59',
        }

    def login(self, user):
        self.client.force_login(user)

    def test_permission_requires_admin_grant(self):
        self.consultant.accesses.clear()
        self.assertFalse(can_create(self.consultant))
        self.login(self.consultant)
        for name in ('list', 'create', 'access'):
            self.assertEqual(self.client.get(reverse(f'assignments:{name}')).status_code, 403)
        self.login(self.admin)
        self.client.post(reverse('assignments:access'), {'user_id': self.consultant.pk, 'enabled': '1'})
        self.assertTrue(can_create(self.consultant))
        self.client.post(reverse('assignments:access'), {'user_id': self.consultant.pk, 'enabled': '0'})
        self.assertFalse(can_create(self.consultant))

    def test_admin_and_trustee_publish_directly(self):
        for user in (self.admin, self.trustee):
            with self.subTest(role=user.role):
                self.login(user)
                response = self.client.post(reverse('assignments:create'), self.data())
                self.assertEqual(response.status_code, 302)
                self.assertEqual(Assignment.objects.filter(creator=user).latest('pk').status, 'published')

    def test_consultant_requires_review_then_student_can_view(self):
        self.login(self.consultant)
        self.assertEqual(self.client.post(reverse('assignments:create'), self.data()).status_code, 302)
        item = Assignment.objects.get(creator=self.consultant)
        self.assertEqual(item.status, 'pending')
        url = reverse('assignments:detail', args=[item.pk])
        self.login(self.student_user)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse('assignments:file', args=[item.pk])).status_code, 404)
        self.login(self.moderator)
        self.assertContains(self.client.get(reverse('assignments:list') + '?tab=review'), item.title)
        self.assertEqual(self.client.post(reverse('assignments:review', args=[item.pk]), {'decision': 'published'}).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.reviewed_by, self.moderator)
        self.assertEqual(item.status, 'published')
        self.assertEqual(Review.objects.filter(assignment=item).count(), 1)
        self.login(self.student_user)
        self.assertContains(self.client.get(url), item.title)

    def test_creator_cannot_approve_own_pending_assignment(self):
        item = self.assignment(creator=self.consultant, status='pending')
        self.login(self.consultant)
        self.assertEqual(self.client.post(reverse('assignments:review', args=[item.pk]), {'decision': 'published'}).status_code, 403)

    def test_trustee_review_scope_rejects_mixed_and_outside_audiences(self):
        local = self.assignment(creator=self.consultant, status='pending')
        outside = self.assignment(creator=self.consultant, status='pending', students=[self.other_student])
        mixed = self.assignment(creator=self.consultant, status='pending', students=[self.student, self.other_student])
        self.assertEqual(list(reviewable(self.trustee, Assignment.objects.all())), [local])
        self.login(self.trustee)
        for item in (outside, mixed):
            self.assertEqual(self.client.post(reverse('assignments:review', args=[item.pk]), {'decision': 'published'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('assignments:review', args=[local.pk]), {'decision': 'published'}).status_code, 302)

    def test_consultant_and_trustee_cannot_forge_audience(self):
        for user in (self.consultant, self.trustee):
            data = self.data(students=[self.other_student.pk])
            form = AssignmentForm(data, {'attachment': data.pop('attachment')}, user=user)
            self.assertFalse(form.is_valid())
            self.assertIn('students', form.errors)

    def test_student_only_sees_selected_assignments(self):
        item = self.assignment()
        self.login(self.other_user)
        self.assertNotContains(self.client.get(reverse('assignments:list')), item.title)
        self.assertEqual(self.client.get(reverse('assignments:detail', args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('assignments:submit', args=[item.pk]), {'attachment': self.upload()}).status_code, 404)

    def test_reject_requires_note_and_resubmission_requires_review(self):
        item = self.assignment(creator=self.consultant, status='pending')
        self.login(self.moderator)
        url = reverse('assignments:review', args=[item.pk])
        self.assertEqual(self.client.post(url, {'decision': 'rejected'}).status_code, 400)
        self.client.post(url, {'decision': 'rejected', 'note': 'اصلاح راهنما'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'rejected')
        self.login(self.consultant)
        data = self.data()
        del data['attachment']
        self.assertEqual(self.client.post(reverse('assignments:edit', args=[item.pk]), data).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertEqual(item.reviews.count(), 1)

    def test_student_submission_routed_to_creator_and_graded(self):
        item = self.assignment(creator=self.consultant)
        self.login(self.student_user)
        url = reverse('assignments:submit', args=[item.pk])
        self.assertEqual(self.client.post(url, {'attachment': self.upload('answer.zip'), 'note': 'پاسخ من'}).status_code, 302)
        response = Submission.objects.get(assignment=item)
        self.assertEqual(self.client.post(url, {'attachment': self.upload()}).status_code, 302)
        self.assertEqual(item.submissions.count(), 1)
        self.login(self.consultant)
        self.assertContains(self.client.get(reverse('assignments:detail', args=[item.pk])), self.student_user.username)
        grade_url = reverse('assignments:grade', args=[item.pk, response.pk])
        self.assertEqual(self.client.post(grade_url, {'score': '0', 'feedback': 'بیشتر تمرین کن'}).status_code, 302)
        response.refresh_from_db()
        self.assertEqual(response.score, Decimal('0'))
        self.assertEqual(response.graded_by, self.consultant)
        self.login(self.student_user)
        self.assertContains(self.client.get(reverse('assignments:detail', args=[item.pk])), 'بیشتر تمرین کن')

    def test_moderator_trustee_and_other_consultant_cannot_read_or_grade_responses(self):
        item = self.assignment(creator=self.consultant)
        response = Submission.objects.create(assignment=item, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        for user in (self.moderator, self.trustee, self.other_consultant, self.other_user):
            self.login(user)
            self.assertEqual(self.client.post(reverse('assignments:grade', args=[item.pk, response.pk]), {'score': 20}).status_code, 403)
            self.assertIn(self.client.get(reverse('assignments:response_file', args=[item.pk, response.pk])).status_code, [403, 404])

    def test_download_is_private_attachment_not_executable(self):
        item = self.assignment()
        self.login(self.student_user)
        response = self.client.get(reverse('assignments:file', args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(b''.join(response.streaming_content), b'Homework file')
        self.assertFalse(item.attachment.name.startswith('/media/'))

    def test_expired_and_unpublished_assignments_do_not_accept_submissions(self):
        item = self.assignment()
        item.deadline = timezone.now() - timedelta(seconds=1)
        item.save()
        self.login(self.student_user)
        self.client.post(reverse('assignments:submit', args=[item.pk]), {'attachment': self.upload()})
        self.assertFalse(item.submissions.exists())
        item.status = 'pending'
        item.save()
        self.assertEqual(self.client.post(reverse('assignments:submit', args=[item.pk]), {'attachment': self.upload()}).status_code, 404)

    def test_no_edit_after_first_submission(self):
        item = self.assignment(creator=self.consultant)
        Submission.objects.create(assignment=item, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        self.login(self.consultant)
        self.assertEqual(self.client.post(reverse('assignments:edit', args=[item.pk]), self.data()).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, 'تمرین جدید')

    def test_score_bounds_and_deadline_validation(self):
        item = self.assignment()
        for score in ('-1', '20.01', 'NaN', 'Infinity'):
            self.assertFalse(GradeForm({'score': score}, assignment=item).is_valid())
        for score in ('0', '19.75', '20'):
            self.assertTrue(GradeForm({'score': score}, assignment=item).is_valid())
        data = self.data()
        data['due_date'] = '۱۴۰۰/۰۱/۰۱'
        form = AssignmentForm(data, {'attachment': data.pop('attachment')}, user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('due_date', form.errors)

    def test_empty_and_oversized_uploads_rejected(self):
        for size in (0, 50 * 1024 * 1024 + 1):
            upload = self.upload()
            upload.size = size
            form = SubmissionForm({}, {'attachment': upload})
            self.assertFalse(form.is_valid())
            self.assertIn('attachment', form.errors)

    def test_smallest_positive_max_score_is_accepted(self):
        data = self.data()
        data['max_score'] = '0.01'
        form = AssignmentForm(data, {'attachment': data.pop('attachment')}, user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_can_review_and_grade_any_assignment(self):
        item = self.assignment(creator=self.consultant, status='pending')
        self.login(self.admin)
        self.client.post(reverse('assignments:review', args=[item.pk]), {'decision': 'published'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'published')
        response = Submission.objects.create(assignment=item, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        self.assertEqual(self.client.post(reverse('assignments:grade', args=[item.pk, response.pk]), {'score': '20'}).status_code, 302)
        response.refresh_from_db()
        self.assertEqual(response.score, 20)

    def test_review_cannot_be_overwritten_by_stale_form(self):
        item = self.assignment(creator=self.consultant, status='pending')
        self.login(self.moderator)
        url = reverse('assignments:review', args=[item.pk])
        self.client.post(url, {'decision': 'published'})
        self.client.post(url, {'decision': 'rejected', 'note': 'stale'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'published')
        self.assertEqual(item.reviews.count(), 1)

    def test_all_pages_render(self):
        item = self.assignment(creator=self.consultant)
        response = Submission.objects.create(assignment=item, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        for user in (self.admin, self.consultant, self.moderator, self.trustee, self.student_user):
            self.login(user)
            for name, args in [('list', []), ('detail', [item.pk])]:
                self.assertEqual(self.client.get(reverse(f'assignments:{name}', args=args)).status_code, 200)
        self.login(self.admin)
        for name, args in [('access', []), ('create', []), ('grade', [item.pk, response.pk])]:
            self.assertEqual(self.client.get(reverse(f'assignments:{name}', args=args)).status_code, 200)

    def test_mutations_are_post_only_and_grants_are_admin_only(self):
        item = self.assignment(creator=self.consultant, status='pending')
        self.login(self.moderator)
        self.assertEqual(self.client.get(reverse('assignments:review', args=[item.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse('assignments:access'), {'user_id': self.consultant.pk, 'enabled': '1'}).status_code, 403)
        self.login(self.student_user)
        self.assertEqual(self.client.get(reverse('assignments:submit', args=[item.pk])).status_code, 405)

    def test_anonymous_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse('assignments:list')).status_code, 302)

    def audience_form(self, user, **filters):
        data = self.data()
        data.update(filters)
        if filters.get('send_to_all'):
            data.pop('students', None)
        return AssignmentForm(data, {'attachment': data.pop('attachment')}, user=user)

    def test_audience_filters_render_once_and_catalog_is_scoped(self):
        self.login(self.consultant)
        response = self.client.get(reverse('assignments:create'))
        for name in ('province', 'grade', 'field_of_study', 'subject', 'student_search', 'send_to_all'):
            self.assertContains(response, f'name="{name}"', count=1)
        self.assertContains(response, 'ارسال برای همه')
        form = response.context['form']
        self.assertEqual([row['id'] for row in form.audience_catalog['students']], [str(self.student.pk)])
        self.assertEqual([row['value'] for row in form.audience_catalog['provinces']], [str(self.province.pk)])

    def test_send_to_all_admin_includes_every_allowed_active_student(self):
        form = self.audience_form(self.admin, send_to_all=True)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.cleaned_data['students']), {self.student, self.other_student})
        self.login(self.admin)
        data = self.data()
        data.pop('students')
        data['send_to_all'] = 'on'
        self.assertEqual(self.client.post(reverse('assignments:create'), data).status_code, 302)
        item = Assignment.objects.get(creator=self.admin)
        self.assertEqual(set(item.students.all()), {self.student, self.other_student})

    def test_send_to_all_never_expands_consultant_or_trustee_scope(self):
        for user in (self.consultant, self.trustee):
            with self.subTest(role=user.role):
                form = self.audience_form(user, send_to_all=True)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(list(form.cleaned_data['students']), [self.student])

    def test_combined_audience_filters_and_subject(self):
        Subject.objects.create(title='ریاضی', field=self.student.field)
        Subject.objects.create(title='ادبیات', field=self.other_student.field)
        form = self.audience_form(
            self.admin, send_to_all=True, province=str(self.province.pk),
            grade=self.student.field.grade.title, field_of_study=self.student.field.title, subject='ریاضی',
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.cleaned_data['students']), [self.student])

    def test_grade_and_field_filters_cover_matching_schools_not_one_id(self):
        Grade.objects.filter(pk=self.student.field.grade_id).update(title='دهم')
        Grade.objects.filter(pk=self.other_student.field.grade_id).update(title='پایه دهم')
        FieldOfStudy.objects.filter(pk__in=[self.student.field_id, self.other_student.field_id]).update(title='تجربی')
        form = self.audience_form(self.admin, send_to_all=True, grade='دهم', field_of_study='تجربی')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.cleaned_data['students']), {self.student, self.other_student})
        self.assertEqual(form.audience_catalog['grades'], ['دهم'])

    def test_subject_filter_matches_same_subject_in_different_fields(self):
        Subject.objects.create(title='ریاضی', field=self.student.field)
        Subject.objects.create(title='ریاضی', field=self.other_student.field)
        form = self.audience_form(self.admin, send_to_all=True, subject='ریاضی')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.cleaned_data['students']), {self.student, self.other_student})

    def test_inactive_subject_does_not_match_student(self):
        Subject.objects.create(title='ریاضی', field=self.student.field, is_active=False)
        Subject.objects.create(title='ریاضی', field=self.other_student.field)
        form = self.audience_form(self.admin, send_to_all=True, subject='ریاضی')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.cleaned_data['students']), [self.other_student])

    def test_outside_scope_or_unknown_filters_are_rejected(self):
        for filters, error in [
            ({'province': str(self.other_province.pk)}, 'province'),
            ({'grade': self.other_student.field.grade.title}, 'grade'),
            ({'field_of_study': self.other_student.field.title}, 'field_of_study'),
            ({'subject': 'nonexistent'}, 'subject'),
        ]:
            form = self.audience_form(self.consultant, send_to_all=True, **filters)
            self.assertFalse(form.is_valid())
            self.assertIn(error, form.errors)

    def test_send_to_all_with_empty_intersection_does_not_broaden_audience(self):
        form = self.audience_form(self.admin, send_to_all=True, province=str(self.province.pk), field_of_study=self.other_student.field.title)
        self.assertFalse(form.is_valid())
        self.assertIn('students', form.errors)

    def test_manual_recipient_must_match_filters(self):
        form = self.audience_form(self.admin, province=str(self.other_province.pk))
        self.assertFalse(form.is_valid())
        self.assertIn('students', form.errors)

    def test_empty_manual_selection_requires_recipient_or_send_all(self):
        form = self.audience_form(self.admin, students=[])
        self.assertFalse(form.is_valid())
        self.assertIn('students', form.errors)

    def test_send_to_all_respects_search_and_persian_arabic_normalization(self):
        User.objects.filter(pk=self.student_user.pk).update(first_name='علي', last_name='كريمي')
        form = self.audience_form(self.admin, send_to_all=True, student_search='علی کریمی')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.cleaned_data['students']), [self.student])

    def test_send_all_excludes_inactive_students(self):
        User.objects.filter(pk=self.other_user.pk).update(is_active=False)
        form = self.audience_form(self.admin, send_to_all=True)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.cleaned_data['students']), [self.student])

    def test_other_assignment_forms_still_render_their_inputs(self):
        item = self.assignment()
        response = Submission.objects.create(assignment=item, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        self.login(self.admin)
        page = self.client.get(reverse('assignments:grade', args=[item.pk, response.pk]))
        self.assertContains(page, 'name="score"')
        self.assertContains(page, 'name="feedback"')

    def test_dashboard_only_shows_visible_active_unanswered_assignments(self):
        now = timezone.now()
        active = self.assignment()
        pending = self.assignment(status='pending')
        rejected = self.assignment(status='rejected')
        outside = self.assignment(students=[self.other_student])
        expired = self.assignment()
        expired.deadline = now - timedelta(seconds=1)
        expired.save()
        submitted = self.assignment()
        Submission.objects.create(assignment=submitted, student=self.student, attachment=self.upload(), original_filename='answer.txt')
        context = dashboard_assignments(self.student_user, now)
        self.assertEqual(context['active_assignments_count'], 1)
        self.assertEqual([item.pk for item in context['active_assignments']], [active.pk])
        self.assertTrue(context['show_active_assignments'])

    def test_dashboard_assignments_ordered_by_deadline_and_limited(self):
        now = timezone.now()
        for index in range(6):
            item = self.assignment()
            item.deadline = now + timedelta(hours=index + 1)
            item.save()
        context = dashboard_assignments(self.student_user, now)
        self.assertEqual(context['active_assignments_count'], 6)
        self.assertEqual(len(context['active_assignments']), 4)
        deadlines = [item.deadline for item in context['active_assignments']]
        self.assertEqual(deadlines, sorted(deadlines))
        self.assertTrue(all(item.is_urgent for item in context['active_assignments']))

    def test_dashboard_respects_consultant_scope_and_revoked_access(self):
        own = self.assignment(creator=self.consultant)
        self.assignment(creator=self.other_consultant)
        context = dashboard_assignments(self.consultant)
        self.assertEqual([item.pk for item in context['active_assignments']], [own.pk])
        self.consultant.accesses.clear()
        self.assertFalse(dashboard_assignments(self.consultant)['show_active_assignments'])

    def test_dashboard_renders_active_assignments_deadline_and_countdown(self):
        item = self.assignment()
        self.login(self.student_user)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'تکالیف فعال')
        self.assertContains(response, item.title)
        self.assertContains(response, 'زمان باقی‌مانده برای ارسال')
        self.assertContains(response, 'data-assignment-deadline=')
        self.assertContains(response, reverse('assignments:detail', args=[item.pk]))

    def test_dashboard_empty_state_and_no_access(self):
        self.login(self.student_user)
        self.assertContains(self.client.get(reverse('dashboard')), 'فعلاً تکلیف فعالی نداری!')
        self.consultant.accesses.clear()
        self.login(self.consultant)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'id="active-homework-heading"')

    def test_remaining_time_handles_days_minutes_and_expiration(self):
        now = timezone.now()
        for delta, expected in [
            (timedelta(days=2, hours=3), '۲ روز و ۳ ساعت'),
            (timedelta(hours=2, minutes=15), '۲ ساعت و ۱۵ دقیقه'),
            (timedelta(minutes=5), '۵ دقیقه'),
            (timedelta(seconds=20), 'کمتر از یک دقیقه'),
            (timedelta(seconds=-1), 'مهلت ارسال تمام شد'),
        ]:
            with self.subTest(delta=delta):
                self.assertEqual(remaining_time(now + delta, now), expected)
