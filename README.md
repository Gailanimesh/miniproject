Django Auth - Progress README

This README documents the TODO events for building user authentication for the chat bot application.

Summary of tasks:

- Create todo plan and confirm requirements: COMPLETED
  - Notes: Requirements clarified; waiting on choices for REST vs server-rendered, DB, and email settings.

- Scaffold Django project + virtualenv: IN-PROGRESS
  - Next: Create a Python virtualenv, install Django and DRF, start project and initial app structure.

- Add `users` app and models: NOT STARTED
  - Next: Create `users` app, custom `User` model if needed, serializers and admin registration.

- Configure DRF and JWT authentication: NOT STARTED
  - Next: Add `djangorestframework` and `djangorestframework-simplejwt` to settings and configure token lifetimes.

- Implement registration endpoint: NOT STARTED
  - Next: Create an endpoint to register users with validation and email confirmation stub.

- Implement login (token obtain/refresh) endpoints: NOT STARTED
  - Next: Wire up JWT token obtain and refresh views and serializers.

- Implement logout/token blacklist: NOT STARTED
  - Next: Add token blacklist support or short token lifetimes + refresh revocation strategy.

- Password reset via email: NOT STARTED
  - Next: Integrate Django's password reset views or DRF endpoints; configure email backend for dev.

- Unit tests for auth flows: NOT STARTED
  - Next: Add tests for registration, login, refresh, logout, and reset flows.

- Documentation and README with run steps: NOT STARTED
  - Next: Expand this README with setup commands after scaffolding is complete.

How to proceed now (recommended):

1. Confirm whether you want a REST API (DRF + JWT) or server-rendered auth (Django sessions & templates).
2. Confirm the database choice: `sqlite` (default/dev) or `postgres` (production-like).
3. Confirm whether to set up email (SMTP) now or stub it for development.

Once you confirm, I'll finish scaffolding the project and update this README with concrete setup and run commands.
