from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auth_module.models import User, UserRole
from blog_module.models import Category as BlogCategory, Post
from news_module.models import Article, Category as NewsCategory
from .models import ModuleAvailability, PresencePeak


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
