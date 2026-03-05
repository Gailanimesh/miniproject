# Django Chatbot & Timetable API
[![CI](https://github.com/Gailanimesh/miniproject/actions/workflows/ci.yml/badge.svg)](https://github.com/Gailanimesh/miniproject/actions)

## Overview

This project provides a backend for user authentication, timetable management, and a chatbot API. It uses Django REST Framework (DRF) and JWT authentication. The chatbot is ready for future RAG/ML integration.

## Features

- User Authentication: Register, login, logout, JWT token management.
- Timetable Management: Create topics, add free slots, schedule entries, set reminders.
- Chatbot API: Basic conversation flow, ready for RAG/ML extension.
- Validation: Prevents duplicate topics and overlapping slots.
- Comprehensive Testing: Unit and API tests for all major endpoints.
- CI/CD: Automated testing via GitHub Actions.

## Setup Instructions

1. Clone the repository
	 ```
	 git clone https://github.com/Gailanamesh/miniproject.git
	 cd miniproject
	 ```

2. Create and activate a virtual environment
	 ```
	 python -m venv venv
	 source venv/bin/activate  # On Windows: venv\Scripts\activate
	 ```

3. Install dependencies
	 ```
	 pip install -r requirements.txt
	 ```

4. Apply migrations
	 ```
	 python manage.py migrate
	 ```

5. Run the development server
	 ```
	 python manage.py runserver
	 ```

## API Endpoints

- User Registration: `/api/users/register/`
- Login: `/api/users/login/`
- Logout: `/api/users/logout/`
- Token Refresh: `/api/users/token/refresh/`
- Timetable: `/api/timetable/`
- Chatbot Conversation: `/api/chatbot/converse/`

## Testing

- Run all tests
	```
	python manage.py test
	```

- Test Coverage
	- Authentication flows
	- Timetable validation
	- Chatbot API

## CI/CD Workflow

- Automated tests run on every push and pull request via GitHub Actions.
- See `.github/workflows/ci.yml` for configuration.

## Usage Examples

- Register a user
	```
	POST /api/users/register/
	{
		"username": "testuser",
		"password": "securepassword"
	}
	```

- Add a timetable topic
	```
	POST /api/timetable/topics/
	{
		"name": "Math"
	}
	```

- Chatbot conversation
	```
	POST /api/chatbot/converse/
	{
		"message": "What is my next free slot?"
	}
	```

## Future Work

- Integrate RAG/ML for chatbot responses.
- Add reminders and notification system.
- Enhance feedback and analytics.
- IoT integration.
Django Chatbot & Timetable API
<img src="https://github.com/Gailanimesh/miniproject/actions/workflows/ci.yml/badge.svg" alt="CI">

Overview
This project provides a backend for user authentication, timetable management, and a chatbot API. It uses Django REST Framework (DRF) and JWT authentication. The chatbot is ready for future RAG/ML integration.

Features
User Authentication: Register, login, logout, JWT token management.
Timetable Management: Create topics, add free slots, schedule entries, set reminders.
Chatbot API: Basic conversation flow, ready for RAG/ML extension.
Validation: Prevents duplicate topics and overlapping slots.
Comprehensive Testing: Unit and API tests for all major endpoints.
CI/CD: Automated testing via GitHub Actions.
Setup Instructions
Clone the repository

Create and activate a virtual environment

Install dependencies

Apply migrations

Run the development server

API Endpoints
User Registration: /api/users/register/
Login: /api/users/login/
Logout: /api/users/logout/
Token Refresh: /api/users/token/refresh/
Timetable: /api/timetable/
Chatbot Conversation: /api/chatbot/converse/
Testing
Run all tests

Test Coverage

Authentication flows
Timetable validation
Chatbot API
CI/CD Workflow
Automated tests run on every push and pull request via GitHub Actions.
See ci.yml for configuration.
Usage Examples
Register a user

Add a timetable topic

Chatbot conversation

Future Work
Integrate RAG/ML for chatbot responses.
Add reminders and notification system.
Enhance feedback and analytics.
IoT integration.