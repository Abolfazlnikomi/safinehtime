from django.test import TestCase
from django.urls import reverse

from auth_module.models import Consultant, ProvinceTrustee, Student, User, UserRole
from users_module.models import Access, FieldOfStudy, Grade, Province, School, Subject

from .models import (
    ModerationStatus,
    Ticket,
    TicketAuditLog,
    TicketMessage,
    TicketQueue,
    TicketStatus,
    TicketType,
)
from .forms import ReferralForm, TicketCreateForm
from .services import can_access_ticket, consultant_can_access, visible_tickets_for


class TicketCreateFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        province = Province.objects.create(name="ticket-form-province")
        school = School.objects.create(name="مدرسه", province=province)
        grade = Grade.objects.create(title="دهم", school=school)
        field = FieldOfStudy.objects.create(title="ریاضی", grade=grade)
        user = User.objects.create_user(username="ticket-form-student", role=UserRole.STUDENT)
        cls.student = Student.objects.create(user=user, field=field)
        cls.subject = Subject.objects.create(title="حسابان", field=field)

    def form_data(self, ticket_type, subject=""):
        return {"title": "پرسش جدید", "content": "متن پرسش", "ticket_type": ticket_type, "subject": subject}

    def test_lesson_requires_subject(self):
        form = TicketCreateForm(self.form_data(TicketType.LESSON), student=self.student)
        self.assertFalse(form.is_valid())
        self.assertIn("subject", form.errors)
        self.assertFalse(form.fields["subject"].disabled)

    def test_lesson_accepts_student_subject(self):
        form = TicketCreateForm(self.form_data(TicketType.LESSON, self.subject.pk), student=self.student)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["subject"], self.subject)

    def test_non_lesson_disables_and_ignores_subject(self):
        for ticket_type in (TicketType.TECHNICAL, TicketType.PSYCHOLOGY):
            for subject in ("", str(self.subject.pk), "invalid-subject"):
                with self.subTest(ticket_type=ticket_type, subject=subject):
                    form = TicketCreateForm(self.form_data(ticket_type, subject), student=self.student)
                    self.assertTrue(form.fields["subject"].disabled)
                    self.assertTrue(form.is_valid(), form.errors)
                    self.assertIsNone(form.cleaned_data["subject"])

    def test_create_page_renders_topic_cards(self):
        self.client.force_login(self.student.user)
        response = self.client.get(reverse("ticketing:create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="radio"', count=3)
        self.assertContains(response, 'ticket-create.js')
        self.assertContains(response, 'ticket-create.css')


class TicketingFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.province = Province.objects.create(name="hormozgan")
        cls.other_province = Province.objects.create(name="kerman")
        cls.school = School.objects.create(name="مدرسه یک", province=cls.province)
        cls.grade = Grade.objects.create(title="دهم", school=cls.school)
        cls.field = FieldOfStudy.objects.create(title="ریاضی", grade=cls.grade)
        cls.subject = Subject.objects.create(title="حسابان", field=cls.field)
        cls.ticket_access = Access.objects.create(name="ticket", subject=cls.subject)

        cls.student_user = User.objects.create_user(
            username="student", password="pass12345", role=UserRole.STUDENT, gender="MALE"
        )
        cls.student = Student.objects.create(user=cls.student_user, field=cls.field)
        cls.other_student_user = User.objects.create_user(
            username="other-student", password="pass12345", role=UserRole.STUDENT
        )
        cls.other_student = Student.objects.create(user=cls.other_student_user, field=cls.field)
        cls.consultant_user = User.objects.create_user(
            username="consultant", password="pass12345", role=UserRole.CONSULTANT,
            first_name="مشاور", last_name="آزمایشی",
        )
        cls.scope = Consultant.objects.create(consultant=cls.consultant_user)
        cls.scope.accesses.add(cls.ticket_access)
        cls.psychologist_user = User.objects.create_user(
            username="psychologist", password="pass12345", role=UserRole.CONSULTANT
        )
        Consultant.objects.create(
            consultant=cls.psychologist_user,
            can_answer_psychology=True,
        )
        cls.trustee_user = User.objects.create_user(
            username="trustee", password="pass12345", role=UserRole.PROVINCE_TRUSTEE
        )
        ProvinceTrustee.objects.create(user=cls.trustee_user, province=cls.province)
        cls.other_trustee = User.objects.create_user(
            username="other-trustee", password="pass12345", role=UserRole.PROVINCE_TRUSTEE
        )
        ProvinceTrustee.objects.create(user=cls.other_trustee, province=cls.other_province)
        cls.moderator = User.objects.create_user(
            username="moderator", password="pass12345", role=UserRole.CONTENT_MODERATOR,
            first_name="ناظر", last_name="محتوا",
        )

    def create_ticket(self, ticket_type=TicketType.LESSON, subject=True):
        ticket = Ticket.objects.create(
            title="پرسش آزمایشی",
            student=self.student,
            ticket_type=ticket_type,
            subject=self.subject if subject else None,
        )
        message = TicketMessage.objects.create(
            ticket=ticket, sender=self.student_user, content="متن پرسش"
        )
        return ticket, message

    def test_student_creates_ticket_in_moderator_queue(self):
        self.client.force_login(self.student_user)
        response = self.client.post(
            reverse("ticketing:create"),
            {
                "title": "سؤال حسابان",
                "ticket_type": TicketType.LESSON,
                "subject": self.subject.pk,
                "content": "چطور این مسئله را حل کنم؟",
            },
        )
        ticket = Ticket.objects.get(title="سؤال حسابان")
        self.assertRedirects(response, reverse("ticketing:detail", args=[ticket.pk]))
        self.assertEqual(ticket.current_queue, TicketQueue.MODERATOR)
        self.assertEqual(ticket.status, TicketStatus.PENDING_APPROVAL)
        self.assertEqual(ticket.messages.get().moderation_status, ModerationStatus.PENDING)

    def test_moderator_approval_routes_lesson_to_matching_consultant_queue(self):
        ticket, item = self.create_ticket()
        self.client.force_login(self.moderator)
        self.client.post(
            reverse("ticketing:moderate_message", args=[item.pk]),
            {"decision": "approve", "note": "مناسب است"},
        )
        ticket.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.moderation_status, ModerationStatus.APPROVED)
        self.assertEqual(ticket.current_queue, TicketQueue.CONSULTANT)
        self.assertEqual(ticket.status, TicketStatus.OPEN)
        self.assertTrue(consultant_can_access(self.consultant_user, ticket))

    def test_consultant_without_ticket_access_cannot_see_lesson_ticket(self):
        consultant = User.objects.create_user(
            username="no-ticket-access",
            password="pass12345",
            role=UserRole.CONSULTANT,
        )
        Consultant.objects.create(consultant=consultant)
        ticket, item = self.create_ticket()
        item.moderation_status = ModerationStatus.APPROVED
        item.is_approved_by_moderator = True
        item.save()
        ticket.current_queue = TicketQueue.CONSULTANT
        ticket.status = TicketStatus.OPEN
        ticket.save()

        self.assertFalse(consultant_can_access(consultant, ticket))
        self.assertNotIn(ticket, visible_tickets_for(consultant))

    def test_technical_ticket_is_visible_only_to_its_province_trustee(self):
        ticket, item = self.create_ticket(TicketType.TECHNICAL, subject=False)
        self.client.force_login(self.moderator)
        self.client.post(
            reverse("ticketing:moderate_message", args=[item.pk]),
            {"decision": "approve"},
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.current_queue, TicketQueue.TRUSTEE)
        self.assertIn(ticket, visible_tickets_for(self.trustee_user))
        self.assertNotIn(ticket, visible_tickets_for(self.other_trustee))

    def test_consultant_answer_is_hidden_until_moderator_approval(self):
        ticket, first = self.create_ticket()
        first.moderation_status = ModerationStatus.APPROVED
        first.is_approved_by_moderator = True
        first.save()
        ticket.current_queue = TicketQueue.CONSULTANT
        ticket.status = TicketStatus.OPEN
        ticket.save()

        self.client.force_login(self.consultant_user)
        self.client.post(
            reverse("ticketing:add_message", args=[ticket.pk]), {"content": "پاسخ مشاور"}
        )
        answer = ticket.messages.get(sender=self.consultant_user)
        self.assertEqual(answer.moderation_status, ModerationStatus.PENDING)

        self.client.force_login(self.student_user)
        response = self.client.get(reverse("ticketing:detail", args=[ticket.pk]))
        self.assertNotContains(response, "پاسخ مشاور")

        self.client.force_login(self.moderator)
        self.client.post(
            reverse("ticketing:moderate_message", args=[answer.pk]),
            {"decision": "approve"},
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.RESOLVED)
        self.client.force_login(self.student_user)
        self.assertContains(
            self.client.get(reverse("ticketing:detail", args=[ticket.pk])), "پاسخ مشاور"
        )

    def test_referral_and_subject_change_are_audited(self):
        ticket, item = self.create_ticket()
        item.moderation_status = ModerationStatus.APPROVED
        item.is_approved_by_moderator = True
        item.save()
        ticket.current_queue = TicketQueue.CONSULTANT
        ticket.status = TicketStatus.OPEN
        ticket.save()

        self.client.force_login(self.consultant_user)
        self.client.post(
            reverse("ticketing:refer", args=[ticket.pk]),
            {"queue": TicketQueue.TRUSTEE, "assignee": self.trustee_user.pk, "note": "درس اشتباه است"},
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.current_queue, TicketQueue.TRUSTEE)
        self.assertTrue(ticket.logs.filter(action=TicketAuditLog.Action.REFERRED).exists())

        self.client.force_login(self.trustee_user)
        self.client.post(
            reverse("ticketing:edit", args=[ticket.pk]),
            {"ticket_type": TicketType.PSYCHOLOGY, "subject": "", "status": TicketStatus.IN_PROGRESS},
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.ticket_type, TicketType.PSYCHOLOGY)
        self.assertIsNone(ticket.subject)
        self.assertTrue(ticket.logs.filter(action=TicketAuditLog.Action.TYPE_CHANGED).exists())
        self.assertTrue(ticket.logs.filter(action=TicketAuditLog.Action.SUBJECT_CHANGED).exists())

    def test_student_cannot_view_another_students_ticket(self):
        ticket, _ = self.create_ticket()
        self.client.force_login(self.other_student_user)
        response = self.client.get(reverse("ticketing:detail", args=[ticket.pk]))
        self.assertEqual(response.status_code, 403)

    def test_consultant_cannot_see_student_academic_location_details(self):
        ticket, item = self.create_ticket()
        item.moderation_status = ModerationStatus.APPROVED
        item.is_approved_by_moderator = True
        item.save()
        ticket.current_queue = TicketQueue.CONSULTANT
        ticket.status = TicketStatus.OPEN
        ticket.save()

        self.client.force_login(self.consultant_user)
        response = self.client.get(reverse("ticketing:detail", args=[ticket.pk]))
        self.assertNotContains(response, "مدرسه یک")
        self.assertNotContains(response, "اطلاعات تیکت")

    def test_trustee_sees_persian_province_display_name_and_full_actor_name(self):
        ticket, item = self.create_ticket(TicketType.TECHNICAL, subject=False)
        ticket.current_queue = TicketQueue.TRUSTEE
        ticket.status = TicketStatus.OPEN
        ticket.save()
        TicketAuditLog.objects.create(
            ticket=ticket,
            actor=self.moderator,
            action=TicketAuditLog.Action.APPROVED,
        )

        self.client.force_login(self.trustee_user)
        response = self.client.get(reverse("ticketing:detail", args=[ticket.pk]))
        self.assertContains(response, "هرمزگان")
        self.assertContains(response, "ناظر محتوا")
        self.assertNotContains(response, "moderator —")


class ConsultantTicketGenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        province = Province.objects.create(name="hormozgan")
        school = School.objects.create(name="مدرسه", province=province)
        grade = Grade.objects.create(title="دهم", school=school)
        field = FieldOfStudy.objects.create(title="ریاضی", grade=grade)
        cls.subject = Subject.objects.create(title="حسابان", field=field)
        cls.access = Access.objects.create(name="ticket", subject=cls.subject)
        cls.consultant = User.objects.create_user(
            username="gender-consultant", role=UserRole.CONSULTANT
        )
        cls.scope = Consultant.objects.create(
            consultant=cls.consultant, can_answer_psychology=True
        )
        cls.scope.accesses.add(cls.access)
        cls.admin = User.objects.create_user(username="gender-admin", role=UserRole.ADMIN)
        cls.tickets = {}
        for gender in ("MALE", "FEMALE", None):
            user = User.objects.create_user(
                username=f"student-{gender}", role=UserRole.STUDENT, gender=gender
            )
            student = Student.objects.create(user=user, field=field)
            cls.tickets[gender] = Ticket.objects.create(
                title=f"Ticket {gender}", student=student, subject=cls.subject,
                current_queue=TicketQueue.CONSULTANT, status=TicketStatus.OPEN,
            )

    def test_default_allows_both_genders_and_existing_unknown_gender(self):
        self.assertEqual(self.scope.ticket_student_gender, "BOTH")
        for ticket in self.tickets.values():
            self.assertTrue(can_access_ticket(self.consultant, ticket))
            self.assertIn(ticket, visible_tickets_for(self.consultant))

    def test_restricted_scope_only_allows_matching_gender(self):
        for allowed_gender in ("MALE", "FEMALE"):
            self.scope.ticket_student_gender = allowed_gender
            self.scope.save()
            for gender, ticket in self.tickets.items():
                with self.subTest(allowed=allowed_gender, student=gender):
                    self.assertEqual(
                        consultant_can_access(self.consultant, ticket), gender == allowed_gender
                    )
                    self.assertEqual(
                        ticket in visible_tickets_for(self.consultant), gender == allowed_gender
                    )

    def test_psychology_tickets_obey_gender_restriction(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        for gender, ticket in self.tickets.items():
            ticket.ticket_type = TicketType.PSYCHOLOGY
            ticket.subject = None
            ticket.save()
            self.assertEqual(can_access_ticket(self.consultant, ticket), gender == "FEMALE")

    def test_assignment_does_not_bypass_gender_restriction(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        ticket = self.tickets["MALE"]
        ticket.current_assignee_user = self.consultant
        ticket.save()
        self.assertFalse(can_access_ticket(self.consultant, ticket))
        self.assertNotIn(ticket, visible_tickets_for(self.consultant))

    def test_private_ticket_assignment_obeys_gender_restriction(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        for gender, ticket in self.tickets.items():
            ticket.is_private_consultation = True
            ticket.current_assignee_user = self.consultant
            ticket.save()
            self.assertEqual(can_access_ticket(self.consultant, ticket), gender == "FEMALE")
            self.assertEqual(
                ticket in visible_tickets_for(self.consultant), gender == "FEMALE"
            )

    def test_restricted_consultant_cannot_open_or_answer_disallowed_ticket(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        ticket = self.tickets["MALE"]
        self.client.force_login(self.consultant)
        self.assertEqual(
            self.client.get(reverse("ticketing:detail", args=[ticket.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("ticketing:add_message", args=[ticket.pk]), {"content": "پاسخ"}
            ).status_code, 403
        )
        self.assertFalse(ticket.messages.exists())

    def test_referral_excludes_and_rejects_wrong_gender_consultant(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        for ticket_type in (TicketType.LESSON, TicketType.PSYCHOLOGY):
            for gender, ticket in self.tickets.items():
                ticket.ticket_type = ticket_type
                ticket.subject = self.subject if ticket_type == TicketType.LESSON else None
                form = ReferralForm(
                    {"queue": TicketQueue.CONSULTANT, "assignee": self.consultant.pk},
                    actor=self.admin, ticket=ticket,
                )
                self.assertEqual(form.is_valid(), gender == "FEMALE")
                if gender != "FEMALE":
                    self.assertIn("assignee", form.errors)

    def test_gender_and_subject_permissions_must_match_same_scope(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.can_answer_psychology = False
        self.scope.save()
        Consultant.objects.create(consultant=self.consultant, ticket_student_gender="MALE")
        ticket = self.tickets["MALE"]
        self.assertFalse(can_access_ticket(self.consultant, ticket))
        form = ReferralForm(actor=self.admin, ticket=ticket)
        self.assertNotIn(self.consultant, form.fields["assignee"].queryset)

    def test_gender_restriction_does_not_change_other_accesses(self):
        self.scope.ticket_student_gender = "FEMALE"
        self.scope.save()
        self.assertTrue(self.scope.has_access("ticket", self.tickets["MALE"].student, self.subject))
        self.assertTrue(can_access_ticket(self.admin, self.tickets["MALE"]))

    def test_assigned_consultant_without_scope_retains_existing_access(self):
        self.scope.delete()
        ticket = self.tickets["MALE"]
        ticket.is_private_consultation = True
        ticket.current_assignee_user = self.consultant
        ticket.save()
        self.assertTrue(can_access_ticket(self.consultant, ticket))


class ModeratorTicketGenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        province = Province.objects.create(name="hormozgan")
        school = School.objects.create(name="مدرسه", province=province)
        grade = Grade.objects.create(title="دهم", school=school)
        field = FieldOfStudy.objects.create(title="ریاضی", grade=grade)
        cls.moderator = User.objects.create_user(
            username="restricted-moderator", role=UserRole.CONTENT_MODERATOR, gender="MALE"
        )
        cls.admin = User.objects.create_user(username="moderation-admin", role=UserRole.ADMIN)
        cls.tickets = {}
        for gender in ("MALE", "FEMALE", None):
            user = User.objects.create_user(
                username=f"moderation-student-{gender}", role=UserRole.STUDENT, gender=gender
            )
            student = Student.objects.create(user=user, field=field)
            ticket = Ticket.objects.create(
                title=f"Moderation ticket {gender}", student=student,
                ticket_type=TicketType.PSYCHOLOGY,
            )
            TicketMessage.objects.create(ticket=ticket, sender=user, content="پرسش دانش‌آموز")
            cls.tickets[gender] = ticket

    def restrict_moderator(self, gender="FEMALE"):
        self.moderator.moderation_student_gender = gender
        self.moderator.save()

    def test_default_allows_both_and_unknown_gender(self):
        self.assertEqual(self.moderator.moderation_student_gender, "BOTH")
        for ticket in self.tickets.values():
            self.assertTrue(can_access_ticket(self.moderator, ticket))
            self.assertIn(ticket, visible_tickets_for(self.moderator))

    def test_restriction_filters_tickets_for_each_gender(self):
        for allowed_gender in ("MALE", "FEMALE"):
            self.restrict_moderator(allowed_gender)
            for gender, ticket in self.tickets.items():
                with self.subTest(allowed=allowed_gender, student=gender):
                    self.assertEqual(can_access_ticket(self.moderator, ticket), gender == allowed_gender)
                    self.assertEqual(ticket in visible_tickets_for(self.moderator), gender == allowed_gender)

    def test_private_tickets_also_obey_restriction(self):
        self.restrict_moderator()
        for gender, ticket in self.tickets.items():
            ticket.is_private_consultation = True
            ticket.current_assignee_user = self.moderator
            ticket.save()
            self.assertEqual(can_access_ticket(self.moderator, ticket), gender == "FEMALE")
            self.assertEqual(ticket in visible_tickets_for(self.moderator), gender == "FEMALE")

    def test_moderation_queue_filters_by_student_not_message_sender(self):
        self.restrict_moderator()
        ticket = self.tickets["FEMALE"]
        staff_message = TicketMessage.objects.create(
            ticket=ticket, sender=self.moderator, content="پاسخ دارای پیوست"
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("ticketing:moderation_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(
            response.context["pending_messages"], list(ticket.messages.all())
        )
        self.assertIn(staff_message, response.context["pending_messages"])

    def test_restricted_moderator_cannot_open_or_answer_disallowed_ticket(self):
        self.restrict_moderator()
        self.client.force_login(self.moderator)
        for gender in ("MALE", None):
            ticket = self.tickets[gender]
            self.assertEqual(
                self.client.get(reverse("ticketing:detail", args=[ticket.pk])).status_code, 403
            )
            self.assertEqual(
                self.client.post(
                    reverse("ticketing:add_message", args=[ticket.pk]), {"content": "پاسخ"}
                ).status_code, 403
            )
            self.assertEqual(ticket.messages.count(), 1)

    def test_direct_moderation_post_cannot_bypass_restriction(self):
        self.restrict_moderator()
        self.client.force_login(self.moderator)
        ticket = self.tickets["MALE"]
        message = ticket.messages.get()
        for decision in ("approve", "reject"):
            response = self.client.post(
                reverse("ticketing:moderate_message", args=[message.pk]),
                {"decision": decision, "note": "بررسی"},
            )
            self.assertEqual(response.status_code, 403)
            message.refresh_from_db()
            ticket.refresh_from_db()
            self.assertEqual(message.moderation_status, ModerationStatus.PENDING)
            self.assertIsNone(message.reviewed_by)
            self.assertEqual(ticket.status, TicketStatus.PENDING_APPROVAL)
            self.assertFalse(ticket.logs.exists())

    def test_matching_moderator_can_approve_message(self):
        self.restrict_moderator()
        self.client.force_login(self.moderator)
        ticket = self.tickets["FEMALE"]
        message = ticket.messages.get()
        response = self.client.post(
            reverse("ticketing:moderate_message", args=[message.pk]), {"decision": "approve"}
        )
        self.assertRedirects(response, reverse("ticketing:detail", args=[ticket.pk]))
        message.refresh_from_db()
        self.assertEqual(message.moderation_status, ModerationStatus.APPROVED)
        self.assertEqual(message.reviewed_by, self.moderator)

    def test_referral_excludes_and_rejects_wrong_gender_moderator(self):
        for allowed_gender in ("MALE", "FEMALE"):
            self.restrict_moderator(allowed_gender)
            for gender, ticket in self.tickets.items():
                form = ReferralForm(
                    {"queue": TicketQueue.MODERATOR, "assignee": self.moderator.pk},
                    actor=self.admin, ticket=ticket,
                )
                self.assertEqual(form.is_valid(), gender == allowed_gender)
                self.assertEqual(
                    self.moderator in form.fields["assignee"].queryset, gender == allowed_gender
                )

    def test_admin_and_superuser_keep_unrestricted_access(self):
        self.admin.moderation_student_gender = "FEMALE"
        self.admin.save()
        self.restrict_moderator()
        self.moderator.is_superuser = True
        self.moderator.save()
        ticket = self.tickets["MALE"]
        for user in (self.admin, self.moderator):
            self.assertTrue(can_access_ticket(user, ticket))
            self.assertIn(ticket, visible_tickets_for(user))
            self.client.force_login(user)
            response = self.client.get(reverse("ticketing:moderation_queue"))
            self.assertEqual(response.context["pending_messages"].count(), 3)
        form = ReferralForm(actor=self.admin, ticket=ticket)
        self.assertIn(self.moderator, form.fields["assignee"].queryset)
