# users/tests.py
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Department, Permission, Role, RolePermission, Section, User, UserSession


class UserManagementTests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username='super', email='super@example.com', password='password123', role='super_admin'
        )
        self.admin = User.objects.create_user(
            username='admin1', email='admin1@example.com', password='password123', role='admin'
        )
        self.staff = User.objects.create_user(
            username='staff1', email='staff1@example.com', password='password123', role='staff'
        )

    def test_list_is_paginated_and_searchable(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/users/users/', {'search': 'staff1'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertIn('count', response.data)
        usernames = [u['username'] for u in response.data['results']]
        self.assertEqual(usernames, ['staff1'])

    def test_list_filters_by_role_and_status(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get('/api/users/users/', {'role': 'staff', 'is_active': 'true'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(u['role'] == 'staff' for u in response.data['results']))

    def test_bulk_status_updates_multiple_users(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch('/api/users/users/bulk_status/', {
            'user_ids': [self.staff.id], 'is_active': False,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated'], 1)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)

    def test_bulk_status_rejects_self_target(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch('/api/users/users/bulk_status/', {
            'user_ids': [self.admin.id], 'is_active': False,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated'], 0)
        self.assertEqual(response.data['errors'], 1)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_single_delete_rejects_self(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/users/users/{self.admin.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(id=self.admin.id).exists())

    def test_admin_cannot_create_super_admin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/users/', {
            'username': 'newsuper', 'email': 'newsuper@example.com',
            'password': 'password123', 'role': 'super_admin',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('role', response.data)

    def test_super_admin_can_create_super_admin(self):
        self.client.force_authenticate(self.super_admin)
        response = self.client.post('/api/users/users/', {
            'username': 'newsuper', 'email': 'newsuper@example.com',
            'password': 'password123', 'role': 'super_admin',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_blank_employee_id_does_not_collide(self):
        self.client.force_authenticate(self.admin)
        payload = lambda username: {
            'username': username, 'email': f'{username}@example.com',
            'password': 'password123', 'role': 'staff', 'employee_id': '',
        }
        first = self.client.post('/api/users/users/', payload('empidone'), format='json')
        second = self.client.post('/api/users/users/', payload('empidtwo'), format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)


class RoleDepartmentCatalogTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', email='admin1@example.com', password='password123', role='admin'
        )
        self.staff = User.objects.create_user(
            username='staff1', email='staff1@example.com', password='password123', role='staff'
        )

    def test_seed_migration_ran(self):
        self.assertTrue(Role.objects.filter(name='super_admin', is_system=True).exists())
        self.assertTrue(Role.objects.filter(name='staff', is_system=False).exists())
        self.assertTrue(Department.objects.filter(name='TMO').exists())

    def test_admin_can_create_custom_role(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/roles/', {
            'name': 'tmo', 'display_name': 'TMO', 'description': 'TMO team lead',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Role.objects.filter(name='tmo').exists())

    def test_non_admin_cannot_create_role(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post('/api/users/roles/', {
            'name': 'cto', 'display_name': 'Chief Technical Officer',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_system_role_cannot_be_deleted(self):
        self.client.force_authenticate(self.admin)
        role = Role.objects.get(name='super_admin')
        response = self.client.delete(f'/api/users/roles/{role.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Role.objects.filter(name='super_admin').exists())

    def test_role_in_use_cannot_be_deleted(self):
        self.client.force_authenticate(self.admin)
        role = Role.objects.get(name='staff')  # self.staff holds this role
        response = self.client.delete(f'/api/users/roles/{role.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unused_custom_role_can_be_deleted(self):
        self.client.force_authenticate(self.admin)
        role = Role.objects.create(name='cco', display_name='Chief Commercial Officer')
        response = self.client.delete(f'/api/users/roles/{role.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Role.objects.filter(name='cco').exists())

    def test_role_name_immutable_after_creation(self):
        self.client.force_authenticate(self.admin)
        role = Role.objects.create(name='cto', display_name='Chief Technical Officer')
        response = self.client.patch(f'/api/users/roles/{role.id}/', {'name': 'cco'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assigning_unknown_role_is_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/users/', {
            'username': 'ghost', 'email': 'ghost@example.com',
            'password': 'password123', 'role': 'not_a_real_role',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assigning_custom_role_works_and_displays_nicely(self):
        Role.objects.create(name='cto', display_name='Chief Technical Officer')
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/users/', {
            'username': 'newcto', 'email': 'newcto@example.com',
            'password': 'password123', 'role': 'cto',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(username='newcto')
        self.assertEqual(user.role, 'cto')
        self.assertEqual(user.get_role_display(), 'Chief Technical Officer')
        self.assertIn('Chief Technical Officer', str(user))

    def test_assigning_unknown_department_is_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/users/', {
            'username': 'nodept', 'email': 'nodept@example.com',
            'password': 'password123', 'role': 'staff', 'department': 'Not A Real Dept',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_department_in_use_cannot_be_deleted(self):
        self.client.force_authenticate(self.admin)
        self.staff.department = 'TMO'
        self.staff.save(update_fields=['department'])
        dept = Department.objects.get(name='TMO')
        response = self.client.delete(f'/api/users/departments/{dept.id}/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MyProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='selfuser', email='self@example.com', password='password123',
            role='staff', department='Commercial',
        )

    def test_get_returns_own_profile(self):
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/users/me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'selfuser')

    def test_patch_updates_editable_fields_only(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch('/api/users/me/', {
            'first_name': 'New', 'last_name': 'Name', 'phone_number': '08012345678',
            'department': 'Financial', 'role': 'admin',  # both should be silently ignored (read-only)
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'New')
        self.assertEqual(self.user.phone_number, '08012345678')
        self.assertEqual(self.user.department, 'Commercial')  # unchanged
        self.assertEqual(self.user.role, 'staff')  # unchanged

    def test_patch_uploads_profile_picture(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = BytesIO()
        Image.new('RGB', (1, 1)).save(buffer, format='GIF')
        image = SimpleUploadedFile('avatar.gif', buffer.getvalue(), content_type='image/gif')

        self.client.force_authenticate(self.user)
        response = self.client.patch('/api/users/me/', {'profile_picture': image}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.profile_picture.name.startswith('profile_pictures/avatar'))
        self.user.profile_picture.delete(save=True)


class SessionManagementTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', email='admin1@example.com', password='password123', role='admin'
        )
        self.target = User.objects.create_user(
            username='target', email='target@example.com', password='password123', role='staff'
        )

    def test_login_creates_session(self):
        response = self.client.post('/api/auth/token/', {
            'username': 'target', 'password': 'password123',
        }, format='json', HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(UserSession.objects.filter(user=self.target, is_active=True).count(), 1)
        session = UserSession.objects.get(user=self.target)
        self.assertEqual(session.device_label, 'Chrome on Windows')

    def test_force_logout_blacklists_session(self):
        login = self.client.post('/api/auth/token/', {
            'username': 'target', 'password': 'password123',
        }, format='json')
        refresh = login.data['refresh']
        session = UserSession.objects.get(user=self.target)

        self.client.force_authenticate(self.admin)
        response = self.client.post(f'/api/users/sessions/{session.id}/force_logout/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session.refresh_from_db()
        self.assertFalse(session.is_active)

        self.client.force_authenticate(None)
        refresh_response = self.client.post('/api/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_block_user_deactivates_account_and_sessions(self):
        self.client.post('/api/auth/token/', {'username': 'target', 'password': 'password123'}, format='json')

        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/users/sessions/block_user/', {'user_id': self.target.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)
        self.assertFalse(UserSession.objects.filter(user=self.target, is_active=True).exists())

    def test_manager_cannot_force_logout(self):
        self.client.post('/api/auth/token/', {'username': 'target', 'password': 'password123'}, format='json')
        session = UserSession.objects.get(user=self.target)

        manager = User.objects.create_user(
            username='mgr', email='mgr@example.com', password='password123', role='manager'
        )
        self.client.force_authenticate(manager)
        response = self.client.post(f'/api/users/sessions/{session.id}/force_logout/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RolePermissionMatrixTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin1', email='admin1@example.com', password='password123', role='admin'
        )
        self.manager = User.objects.create_user(
            username='mgr1', email='mgr1@example.com', password='password123', role='manager'
        )
        self.section = Section.objects.create(name='commercial', display_name='Commercial')
        self.perm = Permission.objects.create(
            section=self.section, name='View', codename='view_commercial', permission_type='view'
        )

    def test_matrix_upsert_requires_admin(self):
        self.client.force_authenticate(self.manager)
        response = self.client.put('/api/users/role-permissions/matrix/', {
            'changes': [{'role': 'manager', 'section': self.section.id, 'permission_ids': [self.perm.id]}]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_matrix_upsert_flows_into_current_user_permissions(self):
        self.client.force_authenticate(self.admin)
        response = self.client.put('/api/users/role-permissions/matrix/', {
            'changes': [{
                'role': 'manager', 'section': self.section.id,
                'permission_ids': [self.perm.id], 'is_manager': True,
            }]
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['saved'], 1)
        self.assertEqual(RolePermission.objects.filter(role='manager', section=self.section).count(), 1)

        self.client.force_authenticate(self.manager)
        current = self.client.get('/api/users/current-user/')
        sections = {s['name']: s for s in current.data['permissions']['sections']}
        self.assertIn('commercial', sections)
        self.assertIn('view_commercial', sections['commercial']['permissions'])
        self.assertTrue(sections['commercial']['is_manager'])
