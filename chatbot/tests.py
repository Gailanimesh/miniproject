from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

# Create your tests here.

class ChatbotRAGTests(APITestCase):
    def test_chatbot_converse_endpoint(self):
        url = reverse('chatbot-converse')
        response = self.client.post(url, {"message": "Hello!"}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('response', response.data)
