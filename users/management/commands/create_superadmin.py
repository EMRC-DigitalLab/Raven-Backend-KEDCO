"""
users/management/commands/create_superadmin.py

Creates a Django superuser from environment variables.
Safe to run on every deploy — skips silently if the user already exists.

Required env vars:
  DJANGO_SUPERUSER_USERNAME
  DJANGO_SUPERUSER_EMAIL
  DJANGO_SUPERUSER_PASSWORD

Usage:
  python manage.py create_superadmin
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create superadmin from env vars (idempotent)'

    def handle(self, *args, **options):
        User = get_user_model()

        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
        email    = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()

        if not username or not password:
            self.stdout.write(self.style.WARNING(
                '[create_superadmin] DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD not set — skipping.'
            ))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.SUCCESS(
                f'[create_superadmin] User "{username}" already exists — skipping.'
            ))
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            role='super_admin',
        )
        self.stdout.write(self.style.SUCCESS(
            f'[create_superadmin] Superuser "{username}" created successfully.'
        ))
