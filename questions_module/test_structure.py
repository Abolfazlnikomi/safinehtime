import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from auth_module.models import ProvinceTrustee, Student
from auth_module.user_management import _sheet_xml
from courses_module.forms import CourseCreateForm
from quiz_module.forms import BankImportForm, QuizQuestionForm
from users_module.curriculum_access import grades_for_user, subjects_for_user
from users_module.models import Access, FieldOfStudy, Grade, Province, School, Subject
from .forms import QuestionForm
from .models import Chapter, Choice, Difficulty, Lesson, PracticeSession, Question, Topic
from .structure import StructureUploadForm, import_structure, read_rows, template_xlsx
from .views import _usable_courses, _usable_topics


def workbook(rows, kind='books'):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(template_xlsx(kind))) as source:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = _sheet_xml(rows) if item.filename == 'xl/worksheets/sheet1.xml' else source.read(item.filename)
                target.writestr(item, content)
    return SimpleUploadedFile('structure.xlsx', output.getvalue())


class SchoolStructureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.root = users.create_superuser(username='structure-root', password='test-password')
        cls.admin = users.create_user(username='structure-admin', role='ADMIN')
        cls.student = users.create_user(username='structure-student', role='STUDENT')
        cls.consultant = users.create_user(username='structure-consultant', role='CONSULTANT')
        cls.trustee = users.create_user(username='structure-trustee', role='PROVINCE_TRUSTEE')
        cls.province = Province.objects.create(name='hormozgan')
        cls.other_province = Province.objects.create(name='kerman')
        cls.school = School.objects.create(name='مدرسه ساختار', province=cls.province)
        cls.other_school = School.objects.create(name='مدرسه دیگر', province=cls.other_province)
        cls.grade = Grade.objects.create(school=cls.school, title='دهم')
        cls.other_grade = Grade.objects.create(school=cls.school, title='یازدهم')
        cls.remote_grade = Grade.objects.create(school=cls.other_school, title='دهم')
        cls.field = FieldOfStudy.objects.create(grade=cls.grade, title='تجربی')
        cls.other_field = FieldOfStudy.objects.create(grade=cls.other_grade, title='تجربی')
        cls.parallel_field = FieldOfStudy.objects.create(grade=cls.grade, title='ریاضی')
        cls.remote_field = FieldOfStudy.objects.create(grade=cls.remote_grade, title='تجربی')
        cls.book = Subject.objects.create(title='زیست‌شناسی ۱', field=cls.field)
        cls.other_book = Subject.objects.create(title='زیست‌شناسی ۲', field=cls.other_field)
        cls.parallel_book = Subject.objects.create(title='حسابان', field=cls.parallel_field)
        cls.remote_book = Subject.objects.create(title='زیست‌شناسی ۱', field=cls.remote_field)
        Student.objects.create(user=cls.student, field=cls.field)
        ProvinceTrustee.objects.create(user=cls.trustee, province=cls.province)
        cls.admin.allowed_grades.add(cls.grade)
        for book in (cls.book, cls.other_book, cls.parallel_book, cls.remote_book):
            chapter = Chapter.objects.create(subject=book, name='فصل اول')
            lesson = Lesson.objects.create(chapter=chapter, name='درس اول')
            topic = Topic.objects.create(chapter=chapter, lesson=lesson, name='مبحث اول')
            question = Question.objects.create(topic=topic, text='سؤال آزمایشی', difficulty=Difficulty.MEDIUM)
            for number in range(4):
                Choice.objects.create(question=question, text=f'گزینه {number}', is_correct=number == 0)

    def test_templates_round_trip_with_persian_headers(self):
        for kind in ('books', 'topics'):
            rows = read_rows(SimpleUploadedFile('template.xlsx', template_xlsx(kind)), kind)
            self.assertEqual(rows[0][0], 2)
            self.assertEqual(rows[0][1]['chapter'], 'دنیای زنده')
            if kind == 'topics':
                self.assertNotIn('book', rows[0][1])
                self.assertNotIn('book_id', rows[0][1])

    def test_two_files_import_and_repeat_are_idempotent(self):
        first = [['پایه', 'رشته', 'کتاب', 'فصل'], ['دوازدهم', 'تجربی', 'زیست‌شناسی ۳', 'ژنتیک']]
        second = [['فصل', 'درس', 'مبحث'], ['ژنتیک', 'درس اول', 'وراثت']]
        result = import_structure(self.root, self.school, workbook(first), workbook(second, 'topics'))
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['created'], 6)
        self.assertEqual(result['rows'], 2)
        result = import_structure(self.root, self.school, workbook(first), workbook(second, 'topics'))
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['reused'], 6)
        topic = Topic.objects.get(name='وراثت')
        self.assertEqual(topic.lesson.chapter_id, topic.chapter_id)
        self.assertEqual(topic.chapter.subject.field.grade.title, 'دوازدهم')

    def test_errors_roll_back_both_files(self):
        first = [['پایه', 'رشته', 'کتاب', 'فصل'], ['دوازدهم', 'تجربی', 'کتاب جدید', 'فصل جدید']]
        second = [['فصل', 'درس', 'مبحث'], ['فصل مفقود', 'درس', 'مبحث']]
        result = import_structure(self.root, self.school, workbook(first), workbook(second, 'topics'))
        self.assertEqual(result['errors'][0]['row'], 2)
        self.assertFalse(Subject.objects.filter(title='کتاب جدید').exists())
        self.assertFalse(Grade.objects.filter(school=self.school, title='دوازدهم').exists())

    def test_missing_header_prevents_any_write(self):
        first = [['پایه', 'رشته', 'کتاب', 'فصل'], ['دوازدهم', 'تجربی', 'کتاب جدید', 'فصل جدید']]
        result = import_structure(self.root, self.school, workbook(first), workbook([['کتاب'], ['مفقود']], 'topics'))
        self.assertTrue(result['errors'])
        self.assertFalse(Subject.objects.filter(title='کتاب جدید').exists())

    def test_bad_upload_and_duplicate_headers_are_reported(self):
        for upload in (SimpleUploadedFile('bad.xlsx', b'invalid'), workbook([['پایه', 'رشته', 'کتاب', 'کتاب'], ['دهم', 'تجربی', 'یک', 'دو']])):
            result = import_structure(self.root, self.school, books=upload)
            self.assertTrue(result['errors'])

    def test_ambiguous_chapters_require_grade_and_field(self):
        second = [['فصل', 'درس', 'مبحث'], ['فصل اول', 'درس', 'مبحث']]
        result = import_structure(self.root, self.school, topics=workbook(second, 'topics'))
        self.assertTrue(result['errors'])
        second[0].extend(['پایه', 'رشته'])
        second[1].extend(['دهم', 'تجربی'])
        result = import_structure(self.root, self.school, topics=workbook(second, 'topics'))
        self.assertFalse(result['errors'])
        self.assertTrue(Lesson.objects.filter(chapter__subject=self.book, name='درس').exists())

    def test_admin_cannot_import_other_grade(self):
        rows = [['پایه', 'رشته', 'کتاب', 'فصل'], ['یازدهم', 'تجربی', 'غیرمجاز', 'فصل']]
        result = import_structure(self.admin, self.school, books=workbook(rows))
        self.assertTrue(result['errors'])
        self.assertFalse(Subject.objects.filter(title='غیرمجاز').exists())

    def test_admin_can_import_assigned_grade(self):
        rows = [['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', 'تجربی', 'کتاب جدید مجاز', 'فصل']]
        result = import_structure(self.admin, self.school, books=workbook(rows))
        self.assertFalse(result['errors'])
        self.assertTrue(subjects_for_user(self.student).filter(title='کتاب جدید مجاز').exists())

    def test_trustee_cannot_import_into_other_region(self):
        with self.assertRaises(PermissionDenied):
            import_structure(self.trustee, self.other_school, books=workbook([['پایه', 'رشته', 'کتاب'], ['دهم', 'تجربی', 'غیرمجاز']]))

    def test_student_only_sees_own_field(self):
        self.assertSetEqual(set(subjects_for_user(self.student)), {self.book})
        self.assertSetEqual(set(_usable_courses(user=self.student)), {self.book})
        self.assertSetEqual(set(_usable_topics(user=self.student).values_list('chapter__subject_id', flat=True)), {self.book.pk})

    def test_no_profile_means_no_curriculum(self):
        student = get_user_model().objects.create_user(username='no-profile')
        self.assertFalse(subjects_for_user(student).exists())
        self.assertFalse(grades_for_user(student).exists())

    def test_admin_scope_is_explicit_even_with_global_bank_access(self):
        self.admin.accesses.add(Access.objects.create(name='bank'))
        self.assertSetEqual(set(subjects_for_user(self.admin)), {self.book, self.parallel_book})
        self.admin.allowed_grades.clear()
        self.assertFalse(subjects_for_user(self.admin).exists())

    def test_trustee_sees_own_region_and_root_sees_all(self):
        self.assertSetEqual(set(subjects_for_user(self.trustee)), {self.book, self.other_book, self.parallel_book})
        self.assertSetEqual(set(subjects_for_user(self.root)), {self.book, self.other_book, self.parallel_book, self.remote_book})

    def test_consultant_keeps_subject_permissions(self):
        self.consultant.allowed_grades.add(self.grade)
        self.consultant.accesses.add(Access.objects.create(name='bank', subject=self.book))
        self.assertSetEqual(set(subjects_for_user(self.consultant)), {self.book})
        self.assertFalse(subjects_for_user(self.consultant, 'course').exists())

    def test_inactive_parent_hides_all_descendants(self):
        self.grade.is_active = False
        self.grade.save(update_fields=['is_active'])
        self.assertFalse(subjects_for_user(self.student).exists())
        self.assertFalse(_usable_topics(user=self.student).exists())

    def test_question_and_course_forms_are_scoped(self):
        self.assertSetEqual(set(QuestionForm(user=self.admin).fields['subject'].queryset), {self.book, self.parallel_book})
        form = CourseCreateForm(user=self.admin)
        self.assertSetEqual(set(form.fields['subjects'].queryset), {self.book, self.parallel_book})
        self.assertNotIn(self.other_grade, form.fields['allowed_grades'].queryset)

    def test_quiz_forms_are_scoped(self):
        self.assertSetEqual(set(BankImportForm(user=self.admin).fields['questions'].queryset.values_list('topic__chapter__subject_id', flat=True)), {self.book.pk, self.parallel_book.pk})
        topics = QuizQuestionForm(user=self.admin).fields['bank_topic'].queryset
        self.assertSetEqual(set(topics.values_list('chapter__subject_id', flat=True)), {self.book.pk, self.parallel_book.pk})

    def test_structure_page_renders_tree_and_filters_books(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('school_structure'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ساختار مدرسه')
        self.assertNotContains(response, self.other_book.title)
        self.assertContains(response, 'درس اول')

    def test_student_cannot_open_structure_and_admin_cannot_assign_scope(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('school_structure')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('school_structure'), {'action': 'scope', 'admin': self.admin.pk, 'grades': [self.other_grade.pk]}).status_code, 403)
        self.assertNotIn(self.other_grade, self.admin.allowed_grades.all())

    def test_root_can_assign_consultant_grades(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('school_structure'), {'action': 'scope', 'consultant': self.consultant.pk, 'grades': [self.other_grade.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertSetEqual(set(self.consultant.allowed_grades.all()), {self.other_grade})
        self.assertSetEqual(set(self.admin.allowed_grades.all()), {self.grade})
        self.assertContains(response, 'تخصیص پایه به مشاور')
        self.assertNotContains(response, 'تخصیص پایه به مدیر')

    def test_upload_endpoint_and_download_templates(self):
        self.client.force_login(self.root)
        for kind in ('books', 'topics'):
            response = self.client.get(reverse('school_structure'), {'template': kind})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b'PK'))
        response = self.client.post(reverse('school_structure'), {'action': 'import', 'school': self.school.pk, 'books': workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', 'تجربی', 'ورود از پنل', 'فصل جدید']])})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Subject.objects.filter(title='ورود از پنل').exists())

    def test_oversize_file_validation(self):
        upload = SimpleUploadedFile('big.xlsx', b'0' * (5 * 1024 * 1024 + 1))
        form = StructureUploadForm({'school': self.school.pk}, {'books': upload}, user=self.root)
        self.assertFalse(form.is_valid())
        self.assertIn('books', form.errors)

    def test_stale_session_cannot_start_another_grades_practice(self):
        self.client.force_login(self.student)
        session = self.client.session
        session.update({'qb_mode': 'practice', 'qb_question_type': 'MCQ',
                        'qb_topic_ids': [Topic.objects.get(chapter__subject=self.other_book).pk],
                        'qb_difficulty_counts': {'M': 1}})
        session.save()
        self.client.get(reverse('questions_module:start_practice'))
        self.assertFalse(PracticeSession.objects.filter(user=self.student).exists())

    def test_same_topic_name_is_allowed_in_different_lessons(self):
        chapter = self.book.question_chapters.get()
        lesson = Lesson.objects.create(chapter=chapter, name='درس دوم')
        topic = Topic(chapter=chapter, lesson=lesson, name='مبحث اول')
        topic.full_clean()
        topic.save()
        other_chapter = self.other_book.question_chapters.get()
        topic.chapter = other_chapter
        with self.assertRaises(ValidationError):
            topic.clean()

    def test_planner_and_ticket_use_shared_books(self):
        from plans_module.services import subjects_for_actor
        from ticketing_module.forms import TicketCreateForm

        self.assertSetEqual(set(subjects_for_actor(self.admin)), {self.book, self.parallel_book})
        self.assertSetEqual(set(subjects_for_actor(self.student)), {self.book})
        form = TicketCreateForm(student=Student.objects.get(user=self.student))
        self.assertSetEqual(set(form.fields['subject'].queryset), {self.book})
        self.assertFalse(TicketCreateForm().fields['subject'].queryset.exists())

    def test_forged_course_book_is_rejected(self):
        data = {'title': 'دوره آزمایشی', 'description': 'توضیح', 'level': 'ALL',
                'allowed_grades': [self.grade.pk], 'allowed_fields': [self.field.pk],
                'subjects': [self.other_book.pk]}
        form = CourseCreateForm(data, user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('subjects', form.errors)

    def test_admin_cannot_select_other_grade_in_quiz_form(self):
        from quiz_module.forms import QuizForm

        form = QuizForm(user=self.admin)
        self.assertNotIn(self.other_grade, form.fields['grade'].queryset)
        self.assertNotIn(self.other_field, form.fields['field'].queryset)

    def test_upload_view_rolls_back_and_renders_error_details(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('school_structure'), {
            'action': 'import', 'school': self.school.pk,
            'books': workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', '', 'نامعتبر', '']]),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'هیچ تغییری ذخیره نشد')
        self.assertContains(response, 'مقادیر الزامی خالی‌اند')
        self.assertFalse(Subject.objects.filter(title='نامعتبر').exists())

    def test_native_admin_choices_and_queryset_are_scoped(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from .admin import TopicAdmin

        request = RequestFactory().get('/admin/')
        request.user = self.admin
        model_admin = TopicAdmin(Topic, admin.site)
        self.assertSetEqual(set(model_admin.get_queryset(request).values_list('chapter__subject_id', flat=True)), {self.book.pk, self.parallel_book.pk})
        chapter_field = model_admin.formfield_for_foreignkey(Topic._meta.get_field('chapter'), request)
        self.assertSetEqual(set(chapter_field.queryset.values_list('subject_id', flat=True)), {self.book.pk, self.parallel_book.pk})

    def test_scoped_admin_can_create_course_with_own_book(self):
        from courses_module.models import Course

        self.client.force_login(self.admin)
        response = self.client.post(reverse('courses_module:course_create'), {
            'title': 'دوره پایه مجاز', 'description': 'توضیح', 'level': 'ALL',
            'allowed_grades': [self.grade.pk], 'allowed_fields': [self.field.pk],
            'subjects': [self.book.pk],
        })
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(title='دوره پایه مجاز')
        self.assertFalse(course.all_grades_allowed)
        self.assertFalse(course.all_fields_allowed)
        self.assertSetEqual(set(course.subjects.all()), {self.book})

    def test_student_can_start_practice_in_own_grade(self):
        self.client.force_login(self.student)
        session = self.client.session
        topic = Topic.objects.get(chapter__subject=self.book)
        session.update({'qb_mode': 'practice', 'qb_question_type': 'MCQ',
                        'qb_topic_ids': [topic.pk], 'qb_difficulty_counts': {'M': 1}})
        session.save()
        response = self.client.get(reverse('questions_module:start_practice'))
        self.assertEqual(response.status_code, 302)
        practice = PracticeSession.objects.get(user=self.student)
        self.assertSetEqual(set(practice.topics.all()), {topic})
        self.assertSetEqual(set(practice.questions.values_list('chapter__subject_id', flat=True)), {self.book.pk})

    def test_student_cannot_start_exam_in_other_grade(self):
        from .models import ExamSession

        self.client.force_login(self.student)
        session = self.client.session
        session.update({'qb_mode': 'exam', 'qb_question_type': 'MCQ',
                        'qb_topic_ids': [Topic.objects.get(chapter__subject=self.other_book).pk],
                        'qb_difficulty_counts': {'M': 1}})
        session.save()
        self.client.get(reverse('questions_module:start_exam'))
        self.assertFalse(ExamSession.objects.filter(user=self.student).exists())

    def test_course_cards_only_expose_books_for_student_field(self):
        from courses_module.models import Course
        from courses_module.views import course_cards

        course = Course.objects.create(title='دوره چندپایه', slug='multi-grade', author=self.root)
        course.subjects.set([self.book, self.other_book, self.parallel_book])
        cards = course_cards(Course.objects.filter(pk=course.pk), self.student)
        self.assertSetEqual(set(cards.get().subjects.all()), {self.book})

    def test_all_schools_option_imports_both_files_to_every_school(self):
        first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دوازدهم', 'انسانی', 'تاریخ جدید', 'تمدن']])
        second = workbook([['فصل', 'درس', 'مبحث', 'پایه', 'رشته'], ['تمدن', 'درس', 'مبحث همگانی', 'دوازدهم', 'انسانی']], 'topics')
        result = import_structure(self.root, None, first, second)
        self.assertFalse(result['errors'], result)
        self.assertEqual(Subject.objects.filter(title='تاریخ جدید').count(), School.objects.count())
        self.assertEqual(Topic.objects.filter(name='مبحث همگانی').count(), School.objects.count())

    def test_all_schools_form_accepts_explicit_all_value(self):
        form = StructureUploadForm({'school': 'all'}, {'books': workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', 'تجربی', 'زیست', 'دنیای زنده']])}, user=self.root)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['school'])
        self.assertIn(('all', 'همه موارد'), form.fields['school'].choices)

    def test_all_schools_respects_trustee_region(self):
        first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', 'تجربی', 'فقط منطقه', 'فصل']])
        result = import_structure(self.trustee, None, books=first)
        self.assertFalse(result['errors'], result)
        books = Subject.objects.filter(title='فقط منطقه')
        self.assertSetEqual(set(books.values_list('field__grade__school__province_id', flat=True)), {self.province.pk})
        self.assertFalse(books.filter(field__grade__school=self.other_school).exists())

    def test_any_school_error_rolls_back_every_school(self):
        first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['دهم', 'تجربی', 'بازگشت همگانی', 'فصل خاص']])
        second = workbook([['فصل', 'درس', 'مبحث'], ['فصل مفقود', 'درس', 'مبحث']], 'topics')
        result = import_structure(self.root, None, first, second)
        self.assertTrue(result['errors'])
        self.assertFalse(Subject.objects.filter(title='بازگشت همگانی').exists())

    def test_lower_grade_import_does_not_require_field(self):
        first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['۵', '', 'علوم پنجم', 'علوم زندگی']])
        second = workbook([['فصل', 'درس', 'مبحث', 'پایه'], ['علوم زندگی', 'درس اول', 'گیاه', '۵']], 'topics')
        result = import_structure(self.root, self.school, first, second)
        self.assertFalse(result['errors'], result)
        topic = Topic.objects.get(name='گیاه')
        self.assertEqual(topic.chapter.subject.field.grade.title, 'پنجم')
        self.assertEqual(topic.chapter.subject.field.title, 'عمومی')

    def test_lower_grade_rejects_academic_field_in_excel(self):
        first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], ['نهم', 'ریاضی', 'علوم نهم', 'علوم زندگی']])
        result = import_structure(self.root, self.school, books=first)
        self.assertTrue(result['errors'])
        self.assertFalse(Subject.objects.filter(title='علوم نهم').exists())

    def test_unsupported_grade_and_field_are_rejected(self):
        for grade, field in [('چهارم', ''), ('دهم', 'هنر'), ('دهم', '')]:
            first = workbook([['پایه', 'رشته', 'کتاب', 'فصل'], [grade, field, 'کتاب نامعتبر', 'فصل']])
            self.assertTrue(import_structure(self.root, self.school, books=first)['errors'])
        self.assertFalse(Subject.objects.filter(title='کتاب نامعتبر').exists())

    def test_consultant_scope_cannot_be_bypassed_with_subject_access(self):
        self.consultant.allowed_grades.add(self.grade)
        self.consultant.accesses.add(Access.objects.create(name='bank', subject=self.other_book))
        self.assertFalse(subjects_for_user(self.consultant).exists())
        self.consultant.accesses.add(Access.objects.create(name='bank'))
        self.assertSetEqual(set(subjects_for_user(self.consultant)), {self.book, self.parallel_book})
        self.consultant.allowed_grades.clear()
        self.assertFalse(subjects_for_user(self.consultant).exists())

    def test_consultant_assignment_does_not_accept_admin(self):
        self.client.force_login(self.root)
        self.client.post(reverse('school_structure'), {'action': 'scope', 'consultant': self.admin.pk, 'grades': [self.other_grade.pk]})
        self.assertSetEqual(set(self.admin.allowed_grades.all()), {self.grade})

    def test_question_form_disables_field_for_lower_grade(self):
        grade = Grade.objects.create(school=self.school, title='نهم')
        field = FieldOfStudy.objects.create(grade=grade, title='عمومی')
        book = Subject.objects.create(field=field, title='علوم نهم')
        self.admin.allowed_grades.add(grade)
        form = QuestionForm({'grade': grade.pk, 'field': self.other_field.pk}, user=self.admin)
        self.assertTrue(form.fields['field'].disabled)
        self.assertEqual(form.initial['field'], field.pk)
        self.assertIn(book, form.fields['subject'].queryset)

    def test_lower_grade_course_derives_internal_field(self):
        grade = Grade.objects.create(school=self.school, title='ششم')
        field = FieldOfStudy.objects.create(grade=grade, title='عمومی')
        book = Subject.objects.create(field=field, title='علوم ششم')
        self.admin.allowed_grades.add(grade)
        form = CourseCreateForm({'title': 'دوره ششم', 'description': 'توضیح', 'level': 'ALL',
                                 'allowed_grades': [grade.pk], 'subjects': [book.pk]}, user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertSetEqual(set(form.cleaned_data['allowed_fields']), {field})

    def test_lower_grade_user_creation_derives_internal_field(self):
        from users_module.forms import UserCreateForm

        grade = Grade.objects.create(school=self.school, title='هفتم')
        field = FieldOfStudy.objects.create(grade=grade, title='عمومی')
        form = UserCreateForm({'username': 'lower-student', 'role': 'STUDENT', 'grade': grade.pk,
                               'password': 'A-secure-pass-54321!', 'password_confirm': 'A-secure-pass-54321!'},)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.fields['field'].disabled)
        self.assertEqual(form.cleaned_data['field'], field)

    def test_lower_grade_user_excel_accepts_empty_field(self):
        from auth_module.user_management import import_users

        province = Province.objects.create(name='lower-import-province')
        school = School.objects.create(province=province, name='مدرسه ورود پایه پایین')
        grade = Grade.objects.create(school=school, title='هشتم')
        field = FieldOfStudy.objects.create(grade=grade, title='عمومی')
        upload = workbook([
            ['اسم', 'فامیل', 'استان', 'مدرسه', 'پایه', 'رشته', 'کد ملی', 'رمز عبور', 'نقش'],
            ['کاربر', 'آزمایشی', province.name, school.name, '۸', '', '1234567890', 'Secure-password-432!', 'دانش‌آموز'],
        ])
        result = import_users(upload)
        self.assertTrue(result[0]['ok'], result)
        profile = Student.objects.get(user__username='1234567890')
        self.assertEqual(profile.field_id, field.pk)

    def test_lower_grade_quiz_disables_field_on_server(self):
        from quiz_module.forms import QuizForm

        grade = Grade.objects.create(school=self.school, title='پنجم')
        FieldOfStudy.objects.create(grade=grade, title='عمومی')
        self.admin.allowed_grades.add(grade)
        form = QuizForm({'grade': grade.pk, 'field': self.field.pk}, user=self.admin)
        self.assertTrue(form.fields['field'].disabled)
        self.assertIsNone(form.initial['field'])

    def test_rebuild_replaces_ids_without_losing_dependents(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from django.utils import timezone
        from courses_module.models import Course
        from news_module.models import Article, Category
        from quiz_module.models import Quiz

        course = Course.objects.create(title='دوره محفوظ', slug='preserved-course', author=self.root)
        course.allowed_grades.add(self.grade)
        course.allowed_fields.add(self.field)
        duplicate_grade = Grade.objects.create(school=self.school, title=self.grade.title)
        duplicate_field = FieldOfStudy.objects.create(grade=duplicate_grade, title=self.field.title)
        course.allowed_grades.add(duplicate_grade)
        course.allowed_fields.add(duplicate_field)
        category = Category.objects.create(name='خبر ساختار', slug='structure-news')
        article = Article.objects.create(title='خبر محفوظ', slug='preserved-news', content='متن', author=self.root, category=category)
        article.target_grades.add(self.grade, duplicate_grade)
        quiz = Quiz.objects.create(title='آزمون محفوظ', opens_at=timezone.now(), closes_at=timezone.now(),
                                   grade=self.grade, field=self.field, creator=self.root)
        previous_grades = set(Grade.objects.values_list('pk', flat=True))
        previous_fields = set(FieldOfStudy.objects.values_list('pk', flat=True))
        migration = import_module('users_module.migrations.0008_rebuild_school_grades')
        migration.rebuild_structure(apps, SimpleNamespace(connection=connection))
        self.assertFalse(Grade.objects.filter(pk__in=previous_grades).exists())
        self.assertFalse(FieldOfStudy.objects.filter(pk__in=previous_fields).exists())
        self.book.refresh_from_db()
        profile = Student.objects.get(user=self.student)
        self.assertEqual(profile.field_id, self.book.field_id)
        self.assertEqual(self.book.field.grade.title, 'دهم')
        self.assertEqual(course.allowed_grades.get().pk, self.book.field.grade_id)
        self.assertEqual(course.allowed_fields.get().pk, self.book.field_id)
        self.assertEqual(self.admin.allowed_grades.get().pk, self.book.field.grade_id)
        self.assertEqual(article.target_grades.get().pk, self.book.field.grade_id)
        quiz.refresh_from_db()
        self.assertEqual(quiz.grade_id, self.book.field.grade_id)
        self.assertEqual(quiz.field_id, self.book.field_id)
        for school in School.objects.all():
            self.assertSetEqual(set(school.grades.values_list('title', flat=True)), set(migration.GRADES))
            self.assertEqual(school.grades.count(), 8)
        self.assertSetEqual(set(FieldOfStudy.objects.exclude(title='عمومی').values_list('title', flat=True)), {'ریاضی', 'تجربی', 'انسانی'})
