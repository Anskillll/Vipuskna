from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Profile, TelegramConnection


@override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_BOT_USERNAME='test-bot')
class AdminTelegramBannerTests(TestCase):
    def test_clinic_admin_has_no_personal_telegram_prompt(self):
        admin = User.objects.create_user(username='clinic-admin', password='test-password')
        Profile.objects.create(user=admin, role=Profile.ROLE_CLINIC_ADMIN)
        self.client.force_login(admin)

        for connected in (False, True):
            with self.subTest(connected=connected):
                if connected:
                    TelegramConnection.objects.create(user=admin, chat_id=12345)
                response = self.client.get(reverse('home'))
                self.assertFalse(response.context['telegram_show_connect'])
                self.assertFalse(response.context['telegram_connected'])
                self.assertNotContains(response, 'Приєднайте Telegram-бота')
                self.assertNotContains(response, 'Під’єднати Telegram-бота')
                self.assertNotContains(response, 'Переприв’язати бота')
