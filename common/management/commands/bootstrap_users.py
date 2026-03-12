# management/commands/bootstrap_users.py
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone


class Command(BaseCommand):
    help = 'Bootstrap users app by creating tables and migration records'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write("=== DRY RUN MODE ===\n")
        else:
            self.stdout.write("=== CREATING USERS TABLES AND MIGRATION RECORDS ===\n")
        
        with connection.cursor() as cursor:
            # Step 1: Create users_section table
            section_sql = """
            CREATE TABLE IF NOT EXISTS users_section (
                id bigserial PRIMARY KEY,
                name varchar(20) UNIQUE NOT NULL,
                display_name varchar(50) NOT NULL,
                description text NOT NULL,
                icon varchar(50) NOT NULL,
                is_active boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT NOW()
            );
            """
            
            # Step 2: Create users_user table
            user_sql = """
            CREATE TABLE IF NOT EXISTS users_user (
                id bigserial PRIMARY KEY,
                password varchar(128) NOT NULL,
                last_login timestamptz,
                is_superuser boolean NOT NULL DEFAULT false,
                username varchar(150) UNIQUE NOT NULL,
                first_name varchar(150) NOT NULL,
                last_name varchar(150) NOT NULL,
                email varchar(254) NOT NULL,
                is_staff boolean NOT NULL DEFAULT false,
                date_joined timestamptz NOT NULL DEFAULT NOW(),
                role varchar(20) NOT NULL DEFAULT 'staff',
                phone_number varchar(20),
                department varchar(50),
                employee_id varchar(20) UNIQUE,
                is_active boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT NOW(),
                updated_at timestamptz NOT NULL DEFAULT NOW(),
                created_by_id bigint REFERENCES users_user(id) ON DELETE SET NULL
            );
            """
            
            # Step 3: Create users_user groups and permissions tables
            user_groups_sql = """
            CREATE TABLE IF NOT EXISTS users_user_groups (
                id bigserial PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
                group_id integer NOT NULL REFERENCES auth_group(id) ON DELETE CASCADE,
                UNIQUE(user_id, group_id)
            );
            """
            
            user_permissions_sql = """
            CREATE TABLE IF NOT EXISTS users_user_user_permissions (
                id bigserial PRIMARY KEY,
                user_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
                permission_id integer NOT NULL REFERENCES auth_permission(id) ON DELETE CASCADE,
                UNIQUE(user_id, permission_id)
            );
            """
            
            # Step 4: Create other users app tables
            permission_sql = """
            CREATE TABLE IF NOT EXISTS users_permission (
                id bigserial PRIMARY KEY,
                name varchar(50) NOT NULL,
                codename varchar(100) UNIQUE NOT NULL,
                permission_type varchar(10) NOT NULL,
                description text NOT NULL,
                section_id bigint NOT NULL REFERENCES users_section(id) ON DELETE CASCADE,
                UNIQUE(section_id, codename)
            );
            """
            
            access_log_sql = """
            CREATE TABLE IF NOT EXISTS users_accesslog (
                id bigserial PRIMARY KEY,
                action varchar(20) NOT NULL,
                details jsonb,
                timestamp timestamptz NOT NULL DEFAULT NOW(),
                ip_address inet,
                performed_by_id bigint REFERENCES users_user(id) ON DELETE SET NULL,
                user_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
                section_id bigint NOT NULL REFERENCES users_section(id) ON DELETE CASCADE
            );
            """
            
            temporary_access_sql = """
            CREATE TABLE IF NOT EXISTS users_temporaryaccess (
                id bigserial PRIMARY KEY,
                expires_at timestamptz NOT NULL,
                reason text NOT NULL,
                granted_at timestamptz NOT NULL DEFAULT NOW(),
                is_active boolean NOT NULL DEFAULT true,
                granted_by_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
                section_id bigint NOT NULL REFERENCES users_section(id) ON DELETE CASCADE,
                user_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE
            );
            """
            
            temporary_access_permissions_sql = """
            CREATE TABLE IF NOT EXISTS users_temporaryaccess_permissions (
                id bigserial PRIMARY KEY,
                temporaryaccess_id bigint NOT NULL REFERENCES users_temporaryaccess(id) ON DELETE CASCADE,
                permission_id bigint NOT NULL REFERENCES users_permission(id) ON DELETE CASCADE,
                UNIQUE(temporaryaccess_id, permission_id)
            );
            """
            
            user_section_access_sql = """
            CREATE TABLE IF NOT EXISTS users_usersectionaccess (
                id bigserial PRIMARY KEY,
                is_manager boolean NOT NULL DEFAULT false,
                granted_at timestamptz NOT NULL DEFAULT NOW(),
                is_active boolean NOT NULL DEFAULT true,
                granted_by_id bigint REFERENCES users_user(id) ON DELETE SET NULL,
                section_id bigint NOT NULL REFERENCES users_section(id) ON DELETE CASCADE,
                user_id bigint NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
                UNIQUE(user_id, section_id)
            );
            """
            
            user_section_access_permissions_sql = """
            CREATE TABLE IF NOT EXISTS users_usersectionaccess_permissions (
                id bigserial PRIMARY KEY,
                usersectionaccess_id bigint NOT NULL REFERENCES users_usersectionaccess(id) ON DELETE CASCADE,
                permission_id bigint NOT NULL REFERENCES users_permission(id) ON DELETE CASCADE,
                UNIQUE(usersectionaccess_id, permission_id)
            );
            """
            
            # Migration record SQL
            migration_sql = """
            INSERT INTO django_migrations (app, name, applied)
            VALUES ('users', '0001_initial', %s)
            ON CONFLICT DO NOTHING;
            """
            
            all_sql_commands = [
                ("users_section", section_sql),
                ("users_user", user_sql),
                ("users_user_groups", user_groups_sql),
                ("users_user_user_permissions", user_permissions_sql),
                ("users_permission", permission_sql),
                ("users_accesslog", access_log_sql),
                ("users_temporaryaccess", temporary_access_sql),
                ("users_temporaryaccess_permissions", temporary_access_permissions_sql),
                ("users_usersectionaccess", user_section_access_sql),
                ("users_usersectionaccess_permissions", user_section_access_permissions_sql),
            ]
            
            if dry_run:
                for table_name, sql in all_sql_commands:
                    self.stdout.write(f"WOULD CREATE: {table_name}")
                self.stdout.write("WOULD INSERT: users.0001_initial migration record")
                return
            
            # Execute all commands in a transaction
            with transaction.atomic():
                for table_name, sql in all_sql_commands:
                    try:
                        cursor.execute(sql)
                        self.stdout.write(f"Created table: {table_name}")
                    except Exception as e:
                        self.stdout.write(f"Error creating {table_name}: {e}")
                        raise
                
                # Add migration record
                cursor.execute(migration_sql, [timezone.now()])
                self.stdout.write("Added users.0001_initial to migration history")
                
                # Migrate existing auth_user data if it exists
                cursor.execute("SELECT COUNT(*) FROM auth_user;")
                auth_user_count = cursor.fetchone()[0]
                
                if auth_user_count > 0:
                    self.stdout.write(f"Found {auth_user_count} users in auth_user")
                    
                    # Copy data from auth_user to users_user
                    copy_users_sql = """
                    INSERT INTO users_user (
                        password, last_login, is_superuser, username, first_name, 
                        last_name, email, is_staff, date_joined, role, is_active, 
                        created_at, updated_at
                    )
                    SELECT 
                        password, last_login, is_superuser, username, first_name,
                        last_name, email, is_staff, date_joined, 'staff', is_active,
                        NOW(), NOW()
                    FROM auth_user
                    ON CONFLICT (username) DO NOTHING;
                    """
                    
                    cursor.execute(copy_users_sql)
                    self.stdout.write("Migrated users from auth_user to users_user")
                    
                    # Copy user groups
                    copy_groups_sql = """
                    INSERT INTO users_user_groups (user_id, group_id)
                    SELECT uu.id, aug.group_id
                    FROM auth_user_groups aug
                    JOIN auth_user au ON aug.user_id = au.id
                    JOIN users_user uu ON au.username = uu.username
                    ON CONFLICT DO NOTHING;
                    """
                    
                    cursor.execute(copy_groups_sql)
                    self.stdout.write("Migrated user groups")
                    
                    # Copy user permissions
                    copy_permissions_sql = """
                    INSERT INTO users_user_user_permissions (user_id, permission_id)
                    SELECT uu.id, aup.permission_id
                    FROM auth_user_user_permissions aup
                    JOIN auth_user au ON aup.user_id = au.id
                    JOIN users_user uu ON au.username = uu.username
                    ON CONFLICT DO NOTHING;
                    """
                    
                    cursor.execute(copy_permissions_sql)
                    self.stdout.write("Migrated user permissions")
                
                self.stdout.write("\n" + "="*50)
                self.stdout.write("SUCCESS: Users app bootstrapped successfully!")
                self.stdout.write("You can now run: python manage.py migrate")
                self.stdout.write("="*50)