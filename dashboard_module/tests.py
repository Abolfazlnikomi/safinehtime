from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from auth_module.models import User, UserRole
from blog_module.models import Category as BlogCategory, Post
from news_module.models import Article, Category as NewsCategory
from classroom_module.models import OnlineClass
from quiz_module.models import Quiz

from .models import ModuleAvailability, PresencePeak


class QuestionBankSidebarTests(SimpleTestCase):
    def render_sidebar(self, user, active=True):
        request = RequestFactory().get(reverse("questions_module:choose_mode"))
        content = render_to_string("includes/sidebar_navigation.html", {
            "user": user,
            "request": request,
            "fanous_question_bank_active": active,
        })
        return content.split("<nav ", 1)[1].split("</nav>", 1)[0]

    def test_question_bank_link_is_visible_for_every_authenticated_role(self):
        for role in UserRole.values:
            with self.subTest(role=role):
                sidebar = self.render_sidebar(User(username="sidebar-user", role=role))
                self.assertIn('data-nav-prefix="/questions/"', sidebar)
                self.assertIn(f'href="{reverse("questions_module:choose_mode")}"', sidebar)

    def test_disabled_question_bank_link_is_hidden(self):
        for role in UserRole.values:
            with self.subTest(role=role):
                sidebar = self.render_sidebar(User(username="sidebar-user", role=role), active=False)
                self.assertNotIn('data-nav-prefix="/questions/"', sidebar)

    def test_question_bank_link_is_hidden_for_anonymous_users(self):
        self.assertNotIn('data-nav-prefix="/questions/"', self.render_sidebar(AnonymousUser()))


class SidebarNavigationTests(SimpleTestCase):
    def render_sidebar(self, role=UserRole.STUDENT, path=None, **context):
        return render_to_string("includes/sidebar_navigation.html", {
            "user": User(username="navigation-user", role=role),
            "request": RequestFactory().get(path or reverse("dashboard")),
            "fanous_courses_active": True,
            "fanous_question_bank_active": True,
            "fanous_quizzes_active": True,
            "can_view_assignments": True,
            "can_view_student_records": True,
            **context,
        })

    def section(self, content, name):
        return content.split(f'data-sidebar-section="{name}"', 1)[1].split("</section>", 1)[0]

    def test_features_are_grouped_by_function(self):
        content = self.render_sidebar(UserRole.ADMIN)
        for group, routes in {
            "workspace": ["dashboard"],
            "learning": ["classroom_module:list", "courses_module:course_list"],
            "assessment": ["assignments:list", "questions_module:choose_mode", "quiz_module:quiz_list"],
            "guidance": ["plans_module:board", "counseling:manage", "student_records:list"],
            "updates": ["news_module:article_list", "blog:post_list"],
            "support": ["ticketing:list"],
            "management": ["auth_module:user-management", "school_structure"],
            "system": ["reports_module:people_activity", "module_settings"],
        }.items():
            for route in routes:
                with self.subTest(group=group, route=route):
                    self.assertIn(f'href="{reverse(route)}"', self.section(content, group))

    def test_admin_course_destination_is_not_duplicated(self):
        content = self.render_sidebar(UserRole.ADMIN)
        self.assertEqual(content.count(f'href="{reverse("courses_module:course_list")}"'), 1)

    def test_students_do_not_see_management_groups(self):
        content = self.render_sidebar(can_view_student_records=False)
        self.assertNotIn('data-sidebar-section="management"', content)
        self.assertNotIn('data-sidebar-section="system"', content)
        self.assertNotIn(reverse("student_records:list"), content)

    def test_trustee_school_structure_is_grouped_with_management(self):
        content = self.render_sidebar(UserRole.PROVINCE_TRUSTEE)
        management = self.section(content, "management")
        self.assertIn(reverse("school_structure"), management)
        self.assertNotIn(reverse("auth_module:user-management"), management)

    def test_disabled_assessment_features_do_not_leave_empty_group(self):
        content = self.render_sidebar(
            can_view_assignments=False,
            fanous_question_bank_active=False,
            fanous_quizzes_active=False,
        )
        self.assertNotIn('data-sidebar-section="assessment"', content)

    def test_settings_do_not_highlight_dashboard(self):
        content = self.render_sidebar(UserRole.ADMIN, reverse("module_settings"))
        self.assertNotIn("active", self.section(content, "workspace"))
        self.assertIn("active", self.section(content, "system"))

    def test_classroom_uses_actual_route_for_active_state(self):
        content = self.render_sidebar(path=reverse("classroom_module:list"))
        self.assertIn("active", self.section(content, "learning"))

    def test_consultant_question_links_have_one_active_destination(self):
        for route in ["questions_module:question_list", "questions_module:choose_mode"]:
            with self.subTest(route=route):
                content = self.render_sidebar(UserRole.CONSULTANT, reverse(route))
                self.assertEqual(self.section(content, "assessment").count(" active"), 1)


class FanousDashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="fanous-student", password="pass12345", role=UserRole.STUDENT
        )
        blog_category = BlogCategory.objects.create(name="آموزش", slug="learning")
        news_category = NewsCategory.objects.create(name="اطلاعیه", slug="notice")
        self.post = Post.objects.create(
            title="مطلب تازه فانوس",
            slug="new-fanous-post",
            summary="خلاصه",
            body="متن",
            category=blog_category,
            author=self.user,
            status="published",
            published_at=timezone.now(),
        )
        self.article = Article.objects.create(
            title="خبر تازه فانوس",
            slug="new-fanous-news",
            category=news_category,
            summary="خلاصه خبر",
            content="متن خبر",
            status=Article.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        self.client.force_login(self.user)

    def test_dashboard_shows_role_greeting_and_new_content_notifications(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "دانش‌آموز پرتلاش فانوس")
        self.assertContains(response, self.post.title)
        self.assertContains(response, self.article.title)
        self.assertContains(response, 'id="notification-count"')

    def test_marking_notifications_read_clears_new_content_badge(self):
        response = self.client.post(reverse("notifications_read"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, 'id="notification-count"')

    def test_disabled_courses_are_hidden_and_redirect_students(self):
        admin = User.objects.create_user(username="toggle-admin", password="pass12345", role=UserRole.ADMIN)
        self.client.force_login(admin)
        self.assertEqual(self.client.post(reverse("toggle_module", args=["courses"])).status_code, 302)
        self.assertFalse(ModuleAvailability.objects.get(code="courses").is_active)
        self.assertNotContains(self.client.get(reverse("dashboard")), 'href="/courses/my/"')
        self.client.force_login(self.user)
        response = self.client.get(reverse("courses_module:course_list"))
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_presence_peak_records_distinct_active_users(self):
        self.client.force_login(self.user)
        self.client.get(reverse("dashboard"))
        another = User.objects.create_user(username="second-presence", password="pass12345", role=UserRole.STUDENT)
        self.client.force_login(another)
        self.client.get(reverse("dashboard"))
        self.assertGreaterEqual(PresencePeak.objects.get(day=timezone.localdate()).count, 2)

    def test_admin_presence_panel_uses_glass_layout_and_persian_number_font(self):
        admin = User.objects.create_user(username="glass-presence-admin", role=UserRole.ADMIN)
        self.client.force_login(admin)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'class="dash-section presence-glass-panel"')
        self.assertContains(response, 'class="presence-card-number presence-number"')
        self.assertContains(response, 'aria-label="به‌روزرسانی کاربران برخط"')
        self.assertContains(response, 'aria-controls="presence-details"')
        self.assertContains(response, '#live-presence {')
        self.assertContains(response, '#live-presence .presence-detail-dock.hidden { display: none; }')
        self.assertContains(response, 'class="presence-user-info"')
        panel = response.content.decode().split('id="live-presence"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn("font-mono", panel)
        self.assertNotContains(response, "presence-neon-btn")

    def test_student_dashboard_does_not_show_admin_presence_panel(self):
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, 'id="live-presence"')


class ScheduledDashboardTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user(username="schedule-student", role=UserRole.STUDENT)
        self.other = User.objects.create_user(username="schedule-other", role=UserRole.STUDENT)
        self.client.force_login(self.user)
        self.online_class = OnlineClass.objects.create(
            title="کلاس پیش رو", meeting_url="https://example.com/class",
            starts_at=self.now + timedelta(minutes=10), ends_at=self.now + timedelta(hours=1),
            created_by=self.other,
        )
        self.quiz = Quiz.objects.create(
            title="آزمون پیش رو", status=Quiz.Status.APPROVED, all_students=True,
            opens_at=self.now + timedelta(minutes=10), closes_at=self.now + timedelta(hours=1),
            creator=self.other,
        )

    def feed(self):
        response = self.client.get(reverse("notifications_feed"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_dashboard_has_both_schedule_panels_and_timers(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "آزمون‌های پیش رو")
        self.assertContains(response, "کلاس‌های پیش رو")
        self.assertContains(response, 'data-event-timer', count=2)
        self.assertContains(response, 'aria-controls="notification-panel"')
        self.assertContains(response, 'fanous-event-feed')
        self.assertContains(response, 'dashboard_module/upcoming-events.css')
        self.assertContains(response, 'id="dashboard-upcoming"')
        self.assertContains(response, 'class="schedule-timer-units"', count=2)

    def test_feed_contains_upcoming_events_and_does_not_cache(self):
        response = self.client.get(reverse("notifications_feed"))
        data = response.json()
        self.assertEqual({event["kind"] for event in data["events"]}, {"quiz", "classroom"})
        self.assertTrue(all(not event["live"] for event in data["events"]))
        self.assertEqual(data["unread_count"], 2)
        self.assertIn("no-store", response["Cache-Control"])

    def test_live_timers_identify_event_without_extra_end_deadline(self):
        self.quiz.opens_at = self.now - timedelta(minutes=5)
        self.quiz.save()
        self.online_class.starts_at = self.now - timedelta(minutes=5)
        self.online_class.save()
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "زمان باقی‌مانده تا پایان آزمون")
        self.assertContains(response, "زمان باقی‌مانده تا پایان کلاس")
        self.assertContains(response, 'data-event-timer', count=2)
        self.assertNotContains(response, 'class="schedule-end-date"')
        self.assertNotContains(response, "به وقت تهران")

    def test_read_upcoming_notifications_become_unread_again_at_start(self):
        initial = self.feed()
        response = self.client.post(reverse("notifications_read"), {
            "event_keys": [item["key"] for item in initial["notifications"]],
            "seen_at": initial["now"],
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.feed()["unread_count"], 0)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=11)):
            started = self.feed()
        self.assertEqual(started["unread_count"], 2)
        self.assertTrue(all(event["live"] for event in started["events"]))
        self.assertTrue(all("شروع شد" in item["title"] for item in started["notifications"]))

    def test_ended_inactive_unapproved_and_paused_events_are_excluded(self):
        self.online_class.is_active = False
        self.online_class.save()
        for status, paused in ((Quiz.Status.DRAFT, False), (Quiz.Status.APPROVED, True)):
            self.quiz.status = status
            self.quiz.is_paused = paused
            self.quiz.save()
            self.assertEqual(self.feed()["events"], [])
        self.online_class.is_active = True
        self.online_class.save()
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(hours=2)):
            self.assertEqual(self.feed()["events"], [])

    def test_assigned_quiz_is_not_disclosed_to_other_students(self):
        self.quiz.all_students = False
        self.quiz.save()
        self.quiz.assigned_students.add(self.other)
        self.assertEqual([event["kind"] for event in self.feed()["events"]], ["classroom"])
        self.assertNotIn(self.quiz.title, str(self.feed()["notifications"]))

    def test_disabled_quiz_module_is_respected(self):
        ModuleAvailability.objects.create(code=ModuleAvailability.Code.QUIZZES, is_active=False)
        self.assertEqual([event["kind"] for event in self.feed()["events"]], ["classroom"])

    def test_consultant_sees_only_their_own_quizzes(self):
        consultant = User.objects.create_user(username="schedule-consultant", role=UserRole.CONSULTANT)
        self.client.force_login(consultant)
        self.assertEqual([event["kind"] for event in self.feed()["events"]], ["classroom"])
        self.quiz.creator = consultant
        self.quiz.save()
        self.assertEqual(len(self.feed()["events"]), 2)

    def test_invalid_read_payload_does_not_clear_notifications(self):
        for payload in ('{"event_keys":"bad"}', '[1]', '{"seen_at": "bad"}', '{broken'):
            response = self.client.post(reverse("notifications_read"), payload, content_type="application/json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.feed()["unread_count"], 2)

    def test_notification_endpoints_require_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("notifications_feed")).status_code, 302)
        self.assertEqual(self.client.post(reverse("notifications_read")).status_code, 302)
