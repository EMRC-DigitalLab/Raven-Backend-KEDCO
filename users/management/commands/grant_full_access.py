from django.core.management.base import BaseCommand

from users.models import Permission, Section, User, UserSectionAccess


class Command(BaseCommand):
    help = 'Grant a user full access to all sections'

    def add_arguments(self, parser):
        parser.add_argument('email', type=str, help='Email of the user to grant access')

    def handle(self, *args, **options):
        email = options['email']

        users = User.objects.filter(email=email)
        if not users.exists():
            self.stdout.write(self.style.ERROR(f'User with email {email} not found'))
            return

        sections = Section.objects.filter(is_active=True)
        if not sections.exists():
            self.stdout.write(self.style.ERROR('No active sections found. Run setup_sections first.'))
            return

        for user in users:
            self.stdout.write(f'\nProcessing user: {user.username} (id={user.id})')
            for section in sections:
                access, created = UserSectionAccess.objects.get_or_create(
                    user=user,
                    section=section,
                    defaults={'is_manager': True, 'is_active': True},
                )

                if not created:
                    access.is_manager = True
                    access.is_active = True
                    access.save()

                all_perms = Permission.objects.filter(section=section)
                access.permissions.set(all_perms)

                action = 'Created' if created else 'Updated'
                self.stdout.write(f'  {action}: {section.display_name} ({all_perms.count()} permissions)')

        self.stdout.write(self.style.SUCCESS(f'\nDone. All accounts for {email} now have full access to all sections.'))
