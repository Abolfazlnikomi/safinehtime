from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from auth_module.models import ProvinceTrustee, UserRole
from questions_module.models import Chapter, Lesson, Question, Topic
from users_module.models import Access, FieldOfStudy, Grade, Province, School, Subject

from .forms import QuizForm, QuizQuestionForm
from .models import Quiz, QuizAnswer, QuizChoice, QuizQuestion
from .permissions import can_create_quiz
from .services import create_attempt, finalize_attempt


class QuizFeatureTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user(username="admin-q", password="x", role=UserRole.ADMIN)
        self.consultant = self.User.objects.create_user(username="consultant-q", password="x", role=UserRole.CONSULTANT)
        self.student = self.User.objects.create_user(username="student-q", password="x", role=UserRole.STUDENT)
        self.province = Province.objects.create(name="kerman")
        self.school = School.objects.create(name="مدرسه آزمون", province=self.province)
        self.grade = Grade.objects.create(title="دهم", school=self.school)
        self.field = FieldOfStudy.objects.create(title="ریاضی", grade=self.grade)

    def test_consultant_needs_quiz_access(self):
        self.assertFalse(can_create_quiz(self.consultant))
        access = Access.objects.create(name=Access.Code.QUIZ)
        self.consultant.accesses.add(access)
        self.assertTrue(can_create_quiz(self.consultant))
        self.assertTrue(can_create_quiz(self.admin))

    def test_question_curriculum_selection_and_edit_initials(self):
        self.admin.is_superuser = True
        book = Subject.objects.create(title="کتاب ریاضی", field=self.field)
        chapter = Chapter.objects.create(subject=book, name="فصل اول")
        lesson = Lesson.objects.create(chapter=chapter, name="درس اول")
        topic = Topic.objects.create(chapter=chapter, lesson=lesson, name="مبحث اول")
        other_chapter = Chapter.objects.create(subject=book, name="فصل دوم")
        other_topic = Topic.objects.create(chapter=other_chapter, name="مبحث دوم")
        data = {
            "book": book.pk, "chapter": chapter.pk, "lesson": lesson.pk,
            "bank_topic": topic.pk, "question_type": "DES", "text": "سؤال", "points": "1",
        }
        form = QuizQuestionForm(data=data, user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).bank_topic, topic)
        edited = QuizQuestionForm(instance=QuizQuestion(bank_topic=topic), user=self.admin)
        self.assertEqual(edited.initial["book"], book.pk)
        self.assertEqual(edited.initial["chapter"], chapter.pk)
        self.assertEqual(edited.initial["lesson"], lesson.pk)
        invalid = QuizQuestionForm(data={**data, "bank_topic": other_topic.pk}, user=self.admin)
        self.assertFalse(invalid.is_valid())
        self.assertIn("bank_topic", invalid.errors)
        missing = QuizQuestionForm(data={**data, "bank_topic": ""}, user=self.admin)
        self.assertFalse(missing.is_valid())
        self.assertIn("bank_topic", missing.errors)
        restricted = QuizQuestionForm(data=data, user=self.student)
        self.assertFalse(restricted.is_valid())
        self.assertIn("book", restricted.errors)

    def test_jalali_form_accepts_arbitrary_minute(self):
        form = QuizForm(data={
            "title": "آزمون ساعت دلخواه", "province": self.province.pk, "school": self.school.pk,
            "grade": self.grade.pk, "field": self.field.pk, "duration_minutes": 45, "max_attempts": 1,
            "opens_date": "1405/06/26", "opens_time": "07:13", "closes_date": "1405/06/26", "closes_time": "09:47",
            "release_date": "1405/06/26", "release_time": "10:02", "negative_ratio": "0.33",
            "shuffle_questions": "on", "shuffle_choices": "on", "publish_results": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        quiz = form.save(commit=False)
        self.assertEqual(timezone.localtime(quiz.opens_at).strftime("%H:%M"), "07:13")
        self.assertEqual(timezone.localtime(quiz.closes_at).strftime("%H:%M"), "09:47")

    def test_negative_marking_and_auto_grading(self):
        now = timezone.now()
        quiz = Quiz.objects.create(
            title="آزمون تستی", creator=self.admin, status=Quiz.Status.APPROVED,
            opens_at=now - timedelta(minutes=5), closes_at=now + timedelta(hours=1),
            duration_minutes=30, negative_marking=True, negative_ratio=Decimal("0.25"),
        )
        question = QuizQuestion.objects.create(quiz=quiz, text="دو بعلاوه دو؟", points=4)
        QuizChoice.objects.create(question=question, text="۴", is_correct=True)
        wrong = QuizChoice.objects.create(question=question, text="۵", is_correct=False)
        QuizChoice.objects.create(question=question, text="۶")
        QuizChoice.objects.create(question=question, text="۷")
        attempt = create_attempt(quiz, self.student)
        QuizAnswer.objects.create(attempt=attempt, question=question, choice=wrong)
        result = finalize_attempt(attempt)
        self.assertEqual(result.wrong_count, 1)
        self.assertEqual(result.score, Decimal("0"))
        self.assertEqual(result.answers.get().points_earned, Decimal("-1"))
        self.client.force_login(self.student)
        response = self.client.get(f"/quizzes/attempt/{attempt.pk}/")
        self.assertRedirects(response, f"/quizzes/attempt/{attempt.pk}/result/")
        response = self.client.get(f"/quizzes/attempt/{attempt.pk}/result/")
        self.assertContains(response, "بازگشت به آزمون‌ها")

    def test_admin_quiz_pages_render(self):
        self.client.force_login(self.admin)
        response = self.client.get("/quizzes/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مدیریت آزمون‌ها")
        self.assertContains(response, "سؤال جدید")
        response = self.client.get("/quizzes/create/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ساعت شروع")
        self.assertContains(response, "همه دانش‌آموزان")
        self.assertContains(response, "data-calendar-for")

    def test_question_creation_and_bank_import_use_topic(self):
        subject = Subject.objects.create(title="ریاضی جریان", field=self.field)
        chapter = Chapter.objects.create(subject=subject, name="فصل جریان")
        topic = Topic.objects.create(chapter=chapter, name="مبحث جریان")
        bank_question = Question.objects.create(
            creator=self.admin,
            topic=topic,
            chapter=chapter,
            text="سؤال بانک",
            approval_status=Question.ApprovalStatus.APPROVED,
        )
        now = timezone.now()
        quiz = Quiz.objects.create(
            title="آزمون جریان سؤال",
            creator=self.admin,
            field=self.field,
            grade=self.grade,
            school=self.school,
            province=self.province,
            status=Quiz.Status.APPROVED,
            opens_at=now,
            closes_at=now + timedelta(hours=2),
        )
        self.client.force_login(self.admin)
        response = self.client.get(f"/quizzes/{quiz.pk}/questions/create/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مبحث مرتبط")
        response = self.client.post(f"/quizzes/{quiz.pk}/bank/", {"questions": [bank_question.pk]})
        self.assertRedirects(response, f"/quizzes/{quiz.pk}/builder/")
        copied = QuizQuestion.objects.get(quiz=quiz, source_question=bank_question)
        self.assertEqual(copied.bank_topic, topic)

    def test_admin_sees_all_results_and_student_result_is_time_gated(self):
        now = timezone.now()
        quiz = Quiz.objects.create(
            title="آزمون زمان‌بندی نتیجه",
            creator=self.admin,
            status=Quiz.Status.APPROVED,
            opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=1),
            answer_release_at=now + timedelta(hours=2),
            all_students=True,
        )
        question = QuizQuestion.objects.create(quiz=quiz, text="سؤال نتیجه", points=1)
        correct = QuizChoice.objects.create(question=question, text="صحیح", is_correct=True)
        QuizChoice.objects.create(question=question, text="غلط ۱")
        QuizChoice.objects.create(question=question, text="غلط ۲")
        QuizChoice.objects.create(question=question, text="غلط ۳")
        attempt = create_attempt(quiz, self.student)
        QuizAnswer.objects.create(attempt=attempt, question=question, choice=correct)
        finalize_attempt(attempt)

        self.client.force_login(self.student)
        response = self.client.get(f"/quizzes/attempt/{attempt.pk}/result/")
        self.assertRedirects(response, "/quizzes/")

        self.client.force_login(self.admin)
        response = self.client.get(f"/quizzes/{quiz.pk}/results/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "نتایج این آزمون")
        self.assertContains(response, self.student.get_full_name() or self.student.username)

        quiz.closes_at = now - timedelta(minutes=5)
        quiz.answer_release_at = now - timedelta(minutes=1)
        quiz.save(update_fields=["closes_at", "answer_release_at"])
        self.client.force_login(self.student)
        response = self.client.get(f"/quizzes/attempt/{attempt.pk}/result/")
        self.assertEqual(response.status_code, 200)

    def test_all_students_quiz_is_visible_and_editable(self):
        now = timezone.now()
        quiz = Quiz.objects.create(
            title="آزمون همگانی", creator=self.admin, status=Quiz.Status.APPROVED,
            opens_at=now - timedelta(minutes=5), closes_at=now + timedelta(hours=1),
            all_students=True,
        )
        self.client.force_login(self.student)
        response = self.client.get("/quizzes/")
        self.assertContains(response, quiz.title)

        self.client.force_login(self.admin)
        response = self.client.get("/quizzes/")
        self.assertContains(response, "ویرایش آزمون")
        self.assertContains(response, f'/quizzes/{quiz.pk}/edit/')

    def test_province_trustee_can_edit_approved_quiz_questions(self):
        trustee = self.User.objects.create_user(username="trustee-q", password="x", role=UserRole.PROVINCE_TRUSTEE)
        ProvinceTrustee.objects.create(user=trustee, province=self.province)
        now = timezone.now()
        quiz = Quiz.objects.create(
            title="آزمون تأییدشده استان", creator=self.consultant, province=self.province,
            status=Quiz.Status.APPROVED, opens_at=now, closes_at=now + timedelta(hours=1),
        )
        question = QuizQuestion.objects.create(quiz=quiz, text="سؤال قابل ویرایش")
        self.client.force_login(trustee)
        self.assertEqual(self.client.get(f"/quizzes/{quiz.pk}/builder/").status_code, 200)
        self.assertEqual(self.client.get(f"/quizzes/{quiz.pk}/questions/{question.pk}/edit/").status_code, 200)

    def test_province_trustee_can_edit_approved_bank_question(self):
        trustee = self.User.objects.create_user(username="trustee-bank", password="x", role=UserRole.PROVINCE_TRUSTEE)
        ProvinceTrustee.objects.create(user=trustee, province=self.province)
        subject = Subject.objects.create(title="ریاضی آزمون", field=self.field)
        chapter = Chapter.objects.create(subject=subject, name="فصل اول")
        topic = Topic.objects.create(chapter=chapter, name="مبحث اول")
        question = Question.objects.create(
            creator=self.consultant, topic=topic, text="سؤال تأیید شده",
            approval_status=Question.ApprovalStatus.APPROVED,
        )
        self.client.force_login(trustee)
        response = self.client.get("/questions/approval/")
        self.assertContains(response, "سؤال‌های تأییدشده")
        self.assertContains(response, f'/questions/{question.pk}/edit/')
        self.assertEqual(self.client.get(f"/questions/{question.pk}/edit/").status_code, 200)
