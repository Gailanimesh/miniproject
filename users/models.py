from django.db import models
from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone


class UserManager(BaseUserManager):
	def create_user(self, email, password=None, **extra_fields):
		if not email:
			raise ValueError("Email must be set")
		email = self.normalize_email(email)
		user = self.model(email=email, **extra_fields)
		user.set_password(password)
		user.save(using=self._db)
		return user

	def create_superuser(self, email, password=None, **extra_fields):
		extra_fields.setdefault("is_staff", True)
		extra_fields.setdefault("is_superuser", True)
		extra_fields.setdefault("is_active", True)
		if not extra_fields.get("is_staff"):
			raise ValueError("Superuser must have is_staff=True.")
		if not extra_fields.get("is_superuser"):
			raise ValueError("Superuser must have is_superuser=True.")
		return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
	email = models.EmailField(unique=True)
	first_name = models.CharField(max_length=30, blank=True)
	last_name = models.CharField(max_length=150, blank=True)
	is_staff = models.BooleanField(default=False)
	is_active = models.BooleanField(default=True)
	date_joined = models.DateTimeField(default=timezone.now)

	objects = UserManager()

	USERNAME_FIELD = "email"
	REQUIRED_FIELDS = []

	def __str__(self):
		return self.email


class UserProfile(models.Model):
	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name='profile'
	)
	goal_type = models.CharField(max_length=100, blank=True)
	exam_date = models.DateField(null=True, blank=True)
	knowledge_level = models.CharField(max_length=50, blank=True)
	daily_free_hours = models.PositiveIntegerField(default=0)
	occupation = models.CharField(max_length=100, blank=True)
	preferred_study_time = models.CharField(max_length=50, blank=True)
	learning_style = models.CharField(max_length=50, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"Profile<{self.user.email}>"
