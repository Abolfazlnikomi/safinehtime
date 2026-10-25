from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from auth_module.models import User, UserRole


class LandingPageTests(TestCase):
    def test_home_uses_local_theme_assets(self):
        response = self.client.get(reverse('landing_module:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/tailwind/tailwind.js')
        self.assertContains(response, '/static/fontawesome/css/all.min.css')
        self.assertNotContains(response, 'cdn.tailwindcss.com')
        self.assertNotContains(response, 'cdnjs.cloudflare.com')
        self.assertContains(response, 'آینده‌ای روشن‌تر')
        for asset in ('tailwind/tailwind.js', 'fontawesome/css/all.min.css', 'font/font.css'):
            self.assertIsNotNone(finders.find(asset))

    def test_logged_in_home_does_not_load_dashboard_notifications(self):
        user = User.objects.create_user(username='landing-student', role=UserRole.STUDENT)
        self.client.force_login(user)
        with patch('dashboard_module.context_processors.scheduled_events') as events:
            response = self.client.get(reverse('landing_module:index'))
        self.assertEqual(response.status_code, 200)
        events.assert_not_called()

    def test_reveal_effect_is_progressive_and_does_not_hide_content_by_default(self):
        response = self.client.get(reverse('landing_module:index'))
        html = response.content.decode()
        default_rule = html.split('.reveal-on-scroll {', 1)[1].split('}', 1)[0]
        self.assertIn('opacity: 1', default_rule)
        self.assertIn('transform: none', default_rule)
        self.assertIn('revealObserver.unobserve(entry.target)', html)
        self.assertNotIn('classList.toggle("is-visible", entry.isIntersecting)', html)
