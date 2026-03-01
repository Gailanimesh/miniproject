# ...existing code...
from django.urls import reverse
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import PasswordResetTokenGenerator

User = get_user_model()

class AuthFlowTests(APITestCase):
    def test_register_obtain_token_and_me(self):
        register_url = reverse('auth-register')
        token_url = reverse('token_obtain_pair')
        me_url = reverse('auth-me')

        user_data = {
            "email": "testuser@example.com",
            "password": "Test1234",
            "first_name": "Test",
            "last_name": "User"
        }

        # register
        resp = self.client.post(register_url, user_data, format='json')
        self.assertIn(resp.status_code, (200, 201))

        # obtain tokens
        resp = self.client.post(token_url, {"email": user_data["email"], "password": user_data["password"]}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        access = resp.data["access"]

        # call protected endpoint
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.get(me_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("email"), user_data["email"])

    def test_password_reset_confirm_and_login(self):
        # create user via model for token generation
        email = "resetuser@example.com"
        password = "OrigPass123"
        user = User.objects.create_user(email=email, password=password, first_name="R", last_name="U")

        # generate uid and token
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = PasswordResetTokenGenerator().make_token(user)

        # confirm reset using API
        new_password = "NewPass1234"
        confirm_url = reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
        resp = self.client.post(confirm_url, {"uid": uid, "token": token, "new_password": new_password}, format='json')
        self.assertIn(resp.status_code, (200, 204))

        # verify can obtain tokens with new password
        token_url = reverse('token_obtain_pair')
        resp = self.client.post(token_url, {"email": email, "password": new_password}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_token_refresh_and_logout(self):
        # register user and obtain tokens
        register_url = reverse('auth-register')
        token_url = reverse('token_obtain_pair')
        logout_url = reverse('auth-logout')
        user_data = {"email": "flowuser@example.com", "password": "FlowPass123", "first_name": "F", "last_name": "U"}
        self.client.post(register_url, user_data, format='json')
        resp = self.client.post(token_url, {"email": user_data["email"], "password": user_data["password"]}, format='json')
        self.assertEqual(resp.status_code, 200)
        refresh = resp.data["refresh"]
        access = resp.data["access"]

        # refresh access token
        refresh_url = reverse('token_refresh')
        resp = self.client.post(refresh_url, {"refresh": refresh}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

        # logout (protected)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.post(logout_url, {}, format='json')
        self.assertIn(resp.status_code, (200, 204))
# ...existing code...