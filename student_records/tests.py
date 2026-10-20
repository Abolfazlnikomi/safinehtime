from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from auth_module.models import ProvinceTrustee, Student, User, UserRole
from counseling_module.models import StudentConsultantAssignment
from questions_module.models import Chapter, Topic
from quiz_module.models import Quiz, QuizAnswer, QuizAttempt, QuizQuestion
from users_module.models import Access, FieldOfStudy, Grade, Province, School, Subject
from .fields import ShamsiDateField
from .forms import NoteForm, ReportForm
from .models import CounselingNote, ReportCard, SubjectGrade
from .permissions import can_access, students_for
from ticketing_module.models import ModerationStatus, Ticket, TicketMessage, TicketQueue
from ticketing_module.services import visible_messages_for


class ShamsiDateFieldTests(SimpleTestCase):
    def test_accepts_persian_arabic_and_latin_digits(self):
        field = ShamsiDateField()
        for value in ('۱۴۰۵/۰۷/۲۸', '١٤٠٥/٠٧/٢٨', '1405/07/28', '1405-7-28', ' 1405/7/28 '):
            with self.subTest(value=value):
                self.assertEqual(field.clean(value), date(2026, 10, 20))

    def test_rejects_invalid_dates_and_formats(self):
        field = ShamsiDateField()
        for value in ('1404/12/30', '1405/07/31', '1405/13/01', '1405/00/01', '1405/01/00', '1405/07-28', '1405.07.28', 'invalid'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                field.clean(value)

    def test_valid_leap_day(self):
        self.assertEqual(ShamsiDateField().clean('1403/12/30'), date(2025, 3, 20))

    def test_optional_and_required_dates(self):
        self.assertIsNone(ShamsiDateField(required=False).clean(''))
        with self.assertRaises(ValidationError):
            ShamsiDateField().clean('')
        form = NoteForm({'text': 'Follow up', 'status': 'normal', 'follow_up': ''})
        self.assertTrue(form.is_valid(), form.errors)

    def test_existing_dates_render_in_shamsi(self):
        form = ReportForm(instance=ReportCard(date=date(2026, 10, 20)))
        self.assertIn('۱۴۰۵/۰۷/۲۸', str(form['date']))
        self.assertNotIn('type="date"', str(form['date']))
        form = NoteForm(instance=CounselingNote(follow_up=date(2026, 11, 1)))
        self.assertIn('۱۴۰۵/۰۸/۱۰', str(form['follow_up']))
        self.assertEqual(ShamsiDateField().prepare_value(datetime(2026, 10, 20)), '۱۴۰۵/۰۷/۲۸')

    def test_invalid_input_is_preserved(self):
        form = NoteForm({'text': 'Follow up', 'status': 'normal', 'follow_up': '۱۴۰۵/۱۳/۰۱'})
        self.assertFalse(form.is_valid())
        self.assertIn('follow_up', form.errors)
        self.assertIn('۱۴۰۵/۱۳/۰۱', str(form['follow_up']))

    def test_date_inputs_have_calendar_buttons(self):
        for form, name in ((ReportForm(), 'date'), (NoteForm(), 'follow_up')):
            with self.subTest(field=name):
                markup = str(form[name])
                self.assertIn('data-record-calendar', markup)
                self.assertIn('type="button"', markup)
                self.assertIn('باز کردن تقویم شمسی', markup)
                self.assertNotIn('readonly', markup)


class StudentRecordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.province = Province.objects.create(name='hormozgan')
        cls.school = School.objects.create(province=cls.province, name='مدرسه تست', city='بندرعباس')
        cls.grade = Grade.objects.create(school=cls.school, title='دهم')
        cls.field = FieldOfStudy.objects.create(grade=cls.grade, title='ریاضی')
        cls.subject = Subject.objects.create(field=cls.field, title='فیزیک')
        cls.student_user = User.objects.create_user(username='student-record-test', first_name='سارا', role=UserRole.STUDENT)
        cls.student = Student.objects.create(user=cls.student_user, field=cls.field)
        cls.admin = User.objects.create_user(username='record-admin', role=UserRole.ADMIN)
        cls.consultant = User.objects.create_user(username='record-consultant', role=UserRole.CONSULTANT)
        cls.trustee = User.objects.create_user(username='record-trustee', role=UserRole.PROVINCE_TRUSTEE)
        cls.access, _ = Access.objects.get_or_create(name='student_record', subject=None)

    def url(self):
        return reverse('student_records:detail', args=[self.student.pk])

    def test_all_tabs_render_and_invalid_tab_falls_back(self):
        self.client.force_login(self.admin)
        for tab in ('overview', 'reports', 'exams', 'notes', 'chats'):
            response = self.client.get(self.url(), {'tab': tab})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['active_tab'], tab)
        self.assertEqual(self.client.get(self.url(), {'tab': 'invalid'}).context['active_tab'], 'overview')

    def test_invalid_form_stays_on_relevant_tab(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url(), {'action': 'note'})
        self.assertEqual(response.context['active_tab'], 'notes')
        response = self.client.post(self.url(), self.report_data('21'))
        self.assertEqual(response.context['active_tab'], 'reports')

    def test_chat_messages_render_escaped_with_attachment(self):
        ticket = Ticket.objects.create(student=self.student, title='Academic discussion')
        TicketMessage.objects.create(ticket=ticket, sender=self.student_user, content='<script>unsafe</script>', attachment='ticket_attachments/example.pdf')
        self.client.force_login(self.admin)
        response = self.client.get(self.url(), {'tab': 'chats'})
        self.assertContains(response, 'Academic discussion')
        self.assertContains(response, '&lt;script&gt;unsafe&lt;/script&gt;')
        self.assertContains(response, 'ticket_attachments/example.pdf')
        self.assertNotContains(response, '<script>unsafe</script>')

    def test_chat_cannot_select_another_students_ticket(self):
        other_user = User.objects.create_user(username='other-chat-student', role=UserRole.STUDENT)
        other_student = Student.objects.create(user=other_user, field=self.field)
        ticket = Ticket.objects.create(student=other_student, title='Other record')
        self.client.force_login(self.admin)
        for conversation in (ticket.pk, 'invalid', '9' * 30):
            self.assertEqual(self.client.get(self.url(), {'tab': 'chats', 'conversation': conversation}).status_code, 404)

    def test_trustee_cannot_read_private_consultation(self):
        self.trustee.accesses.add(self.access)
        ProvinceTrustee.objects.create(user=self.trustee, province=self.province)
        private = Ticket.objects.create(student=self.student, title='Private consultation', is_private_consultation=True)
        public = Ticket.objects.create(student=self.student, title='Public discussion')
        self.client.force_login(self.trustee)
        response = self.client.get(self.url(), {'tab': 'chats'})
        self.assertContains(response, public.title)
        self.assertNotContains(response, private.title)
        self.assertEqual(self.client.get(self.url(), {'tab': 'chats', 'conversation': private.pk}).status_code, 404)

    def test_record_assignment_does_not_bypass_chat_access(self):
        self.consultant.accesses.add(self.access)
        StudentConsultantAssignment.objects.create(student=self.student, consultant=self.consultant)
        ticket = Ticket.objects.create(student=self.student, title='Admin only', current_queue=TicketQueue.ADMIN)
        self.client.force_login(self.consultant)
        response = self.client.get(self.url(), {'tab': 'chats'})
        self.assertEqual(response.context['conversations'].paginator.count, 0)
        self.assertEqual(self.client.get(self.url(), {'tab': 'chats', 'conversation': ticket.pk}).status_code, 404)

    def test_chat_message_visibility_preserves_moderation(self):
        ticket = Ticket.objects.create(student=self.student, title='Moderated discussion')
        approved = TicketMessage.objects.create(ticket=ticket, sender=self.student_user, content='Visible', moderation_status=ModerationStatus.APPROVED)
        own = TicketMessage.objects.create(ticket=ticket, sender=self.consultant, content='Own pending')
        TicketMessage.objects.create(ticket=ticket, sender=self.student_user, content='Pending')
        TicketMessage.objects.create(ticket=ticket, sender=self.admin, content='Internal', is_internal=True, moderation_status=ModerationStatus.APPROVED)
        TicketMessage.objects.create(ticket=ticket, sender=self.student_user, content='Rejected', moderation_status=ModerationStatus.REJECTED)
        self.assertSetEqual(set(visible_messages_for(ticket, self.consultant).values_list('pk', flat=True)), {approved.pk, own.pk})

    def test_consultant_can_read_only_assigned_private_conversation(self):
        self.consultant.accesses.add(self.access)
        StudentConsultantAssignment.objects.create(student=self.student, consultant=self.consultant)
        allowed = Ticket.objects.create(student=self.student, title='Assigned private consultation', is_private_consultation=True, current_queue=TicketQueue.CONSULTANT, current_assignee_user=self.consultant)
        blocked = Ticket.objects.create(student=self.student, title='Another private consultation', is_private_consultation=True, current_queue=TicketQueue.CONSULTANT, current_assignee_user=self.admin)
        TicketMessage.objects.create(ticket=allowed, sender=self.student_user, content='Approved private message', moderation_status=ModerationStatus.APPROVED)
        TicketMessage.objects.create(ticket=allowed, sender=self.student_user, content='Hidden pending message')
        self.client.force_login(self.consultant)
        response = self.client.get(self.url(), {'tab': 'chats'})
        self.assertContains(response, 'Approved private message')
        self.assertNotContains(response, 'Hidden pending message')
        self.assertNotContains(response, blocked.title)
        self.assertEqual(self.client.get(self.url(), {'tab': 'chats', 'conversation': blocked.pk}).status_code, 404)

    def test_chat_pagination_defaults_to_latest_messages(self):
        ticket = Ticket.objects.create(student=self.student, title='Long discussion')
        TicketMessage.objects.bulk_create([TicketMessage(ticket=ticket, sender=self.student_user, content=f'Message {number}') for number in range(31)])
        self.client.force_login(self.admin)
        response = self.client.get(self.url(), {'tab': 'chats'})
        self.assertEqual(response.context['conversation_messages'].number, 2)
        self.assertContains(response, 'Message 30')
        response = self.client.get(self.url(), {'tab': 'chats', 'message_page': 1})
        self.assertEqual(len(response.context['conversation_messages']), 30)
        Ticket.objects.bulk_create([Ticket(student=self.student, title=f'Conversation {number}') for number in range(8)])
        response = self.client.get(self.url(), {'tab': 'chats', 'chat_page': 2})
        self.assertEqual(len(response.context['conversations']), 1)

    def test_admin_has_access_without_tick_and_pages_render(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('student_records:list')), 'پرونده دانش‌آموزی')
        self.assertContains(self.client.get(self.url()), 'سارا')

    def test_student_denied_even_with_access(self):
        self.student_user.accesses.add(self.access)
        self.client.force_login(self.student_user)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_consultant_requires_tick_and_assignment(self):
        self.assertFalse(can_access(self.consultant))
        self.consultant.accesses.add(self.access)
        self.assertFalse(students_for(self.consultant).exists())
        StudentConsultantAssignment.objects.create(student=self.student, consultant=self.consultant)
        self.assertEqual(students_for(self.consultant).get(), self.student)
        self.client.force_login(self.consultant)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.consultant.accesses.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_trustee_scoped_to_province(self):
        self.trustee.accesses.add(self.access)
        self.client.force_login(self.trustee)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        ProvinceTrustee.objects.create(user=self.trustee, province=self.province)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_unassigned_consultant_cannot_write(self):
        self.consultant.accesses.add(self.access)
        self.client.force_login(self.consultant)
        response = self.client.post(self.url(), {'action': 'note', 'note-text': 'محرمانه', 'note-status': 'urgent'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(CounselingNote.objects.exists())

    def test_notes_save_author_and_student(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), {'action': 'note', 'note-text': 'پیگیری درسی', 'note-status': 'followup', 'note-follow_up': '۱۴۰۵/۰۸/۱۰'}).status_code, 302)
        note = CounselingNote.objects.get()
        self.assertEqual(note.author, self.admin)
        self.assertEqual(note.student, self.student)
        self.assertEqual(note.follow_up, date(2026, 11, 1))

    def report_data(self, score='18'):
        return {'action': 'report', 'report-title': 'نوبت اول', 'report-date': '۱۴۰۵/۰۷/۲۸', 'report-average': '18.5', 'grades-TOTAL_FORMS': '1', 'grades-INITIAL_FORMS': '0', 'grades-0-subject': str(self.subject.pk), 'grades-0-score': score}

    def test_report_and_subject_grades_saved(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), self.report_data()).status_code, 302)
        self.assertEqual(ReportCard.objects.get().average, Decimal('18.5'))
        self.assertEqual(ReportCard.objects.get().date, date(2026, 10, 20))
        self.assertEqual(SubjectGrade.objects.get().score, 18)
        response = self.client.get(self.url())
        self.assertEqual(response.context['overall'], 18)

    def test_invalid_grade_does_not_save_report(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), self.report_data('21')).status_code, 200)
        self.assertFalse(ReportCard.objects.exists())

    def test_subject_from_other_field_rejected(self):
        other_field = FieldOfStudy.objects.create(grade=self.grade, title='تجربی')
        subject = Subject.objects.create(field=other_field, title='زیست')
        data = self.report_data()
        data['grades-0-subject'] = str(subject.pk)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), data).status_code, 200)
        self.assertFalse(ReportCard.objects.exists())

    def test_add_subject_uses_student_field(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url(), {'action': 'subject', 'subject-title': 'شیمی'}).status_code, 302)
        self.assertTrue(Subject.objects.filter(title='شیمی', field=self.field).exists())

    def test_filters_and_invalid_id(self):
        self.client.force_login(self.admin)
        url = reverse('student_records:list')
        for params in ({'q': 'سارا'}, {'city': 'بندرعباس'}, {'province': self.province.pk}, {'school': self.school.pk}, {'grade': self.grade.pk}, {'field': self.field.pk}):
            self.assertEqual(self.client.get(url, params).context['page'].paginator.count, 1)
        self.assertEqual(self.client.get(url, {'grade': 'invalid'}).context['page'].paginator.count, 0)

    def test_quiz_history_and_subject_average_are_live(self):
        quiz = Quiz.objects.create(title='تست', opens_at=timezone.now(), closes_at=timezone.now() + timedelta(hours=1))
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student_user, status='graded', score=1, total_score=2)
        chapter = Chapter.objects.create(name='فصل اول', subject=self.subject)
        topic = Topic.objects.create(name='مبحث', chapter=chapter)
        question = QuizQuestion.objects.create(quiz=quiz, bank_topic=topic, text='سؤال', points=2)
        answer = QuizAnswer.objects.create(attempt=attempt, question=question, points_earned=1)
        self.client.force_login(self.admin)
        response = self.client.get(self.url())
        self.assertEqual(response.context['overall'], 10)
        self.assertEqual(len(response.context['attempts']), 1)
        answer.points_earned = 2
        answer.save()
        self.assertEqual(self.client.get(self.url()).context['overall'], 20)
        attempt.status = 'submitted'
        attempt.save()
        self.assertIsNone(self.client.get(self.url()).context['overall'])

    def test_image_requires_same_scope_as_record(self):
        with TemporaryDirectory() as folder:
            field = ReportCard._meta.get_field('image')
            previous_storage = field.storage
            field.storage = FileSystemStorage(location=folder)
            self.addCleanup(setattr, field, 'storage', previous_storage)
            output = BytesIO()
            Image.new('RGB', (2, 2)).save(output, format='PNG')
            report = ReportCard.objects.create(student=self.student, title='کارنامه', image=SimpleUploadedFile('report.png', output.getvalue(), content_type='image/png'))
            url = reverse('student_records:image', args=[self.student.pk, report.pk])
            self.client.force_login(self.admin)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
            self.consultant.accesses.add(self.access)
            self.client.force_login(self.consultant)
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_non_image_upload_rejected(self):
        form = ReportForm({'title': 'کارنامه', 'date': '1405/07/28'}, {'image': SimpleUploadedFile('fake.png', b'not-an-image', content_type='image/png')})
        self.assertFalse(form.is_valid())

    def test_average_without_subject_scores_sets_level(self):
        ReportCard.objects.create(student=self.student, title='معدل', average=19)
        self.client.force_login(self.admin)
        response = self.client.get(self.url())
        self.assertEqual(response.context['overall'], 19)
        self.assertEqual(response.context['level'], 'عالی')

    def test_unanswered_questions_count_in_quiz_denominator(self):
        quiz = Quiz.objects.create(title='تست', opens_at=timezone.now(), closes_at=timezone.now() + timedelta(hours=1))
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student_user, status='graded', score=1, total_score=2)
        chapter = Chapter.objects.create(name='فصل', subject=self.subject)
        topic = Topic.objects.create(name='مبحث', chapter=chapter)
        answered = QuizQuestion.objects.create(quiz=quiz, bank_topic=topic, text='اول', points=1)
        QuizQuestion.objects.create(quiz=quiz, bank_topic=topic, text='دوم', points=1)
        QuizAnswer.objects.create(attempt=attempt, question=answered, points_earned=1)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url()).context['overall'], 10)
