from django.core.management.base import BaseCommand

from users.models import Permission, Section, User, UserSectionAccess

EMRC_SECTIONS = ('energy_account', 'grid_lens')


class Command(BaseCommand):
    help = 'Grant the EMRC user access to the Energy Account and GridLens sections'

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username='emrc')
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR('User with username "EMRC" not found.'))
            return

        self.stdout.write(f'Granting access for user: {user.username} (id={user.id})')

        for section_name in EMRC_SECTIONS:
            try:
                section = Section.objects.get(name=section_name)
            except Section.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'  Section "{section_name}" not found — run setup_sections first.'
                ))
                continue

            access, created = UserSectionAccess.objects.get_or_create(
                user=user,
                section=section,
                defaults={'is_active': True, 'is_manager': False},
            )

            if not created:
                access.is_active = True
                access.save()

            all_perms = Permission.objects.filter(section=section)
            access.permissions.set(all_perms)

            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'  {action}: {section.display_name} ({all_perms.count()} permissions)'
            ))

        self.stdout.write(self.style.SUCCESS('\nDone. EMRC now has access to Energy Account and GridLens.'))
