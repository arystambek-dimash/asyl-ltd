from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient


class RetireMonoblockAccountsTests(TransactionTestCase):
    def test_migration_revokes_login_preserves_cameras_authors_and_rollback(self):
        executor = MigrationExecutor(connection)
        previous = [('accounts', '0003_user_must_change_password'), ('cameras', '0030_multi_warehouse_stock_guard'), ('eventlog', '0003_eventlog_eventlog_recent_idx_and_more')]
        current = [('accounts', '0003_user_must_change_password'), ('cameras', '0031_retire_monoblock_accounts')]
        try:
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            User = apps.get_model('accounts', 'User')
            Device = apps.get_model('cameras', 'MonoblockDevice')
            Settings = apps.get_model('cameras', 'MonoblockCameraSettings')
            Event = apps.get_model('eventlog', 'EventLog')
            first = User.objects.create(username='retired-post', is_active=True, password='old-password')
            second = User.objects.create(username='inactive-post', is_active=False)
            staff = User.objects.create(username='ordinary-employee', is_active=True)
            Device.objects.create(user=first, name='Пост', camera_source='cam5')
            Device.objects.create(user=second, name='Отключённый', camera_source='cam6', is_active=False)
            Settings.objects.create(camera_sources=['cam2'], always_on_camera_sources=['cam3'])
            history = Event.objects.create(user=first, event_type='shipping', message='Погрузка завершена')
            from apps.accounts.models import User as RuntimeUser
            from apps.cameras.api_views.access import _camera_token_payload, _camera_token_user, CAM_TOKEN_SALT
            from django.core import signing
            from rest_framework_simplejwt.tokens import RefreshToken
            token_user = RuntimeUser.objects.get(pk=first.pk)
            cookie = signing.dumps(_camera_token_payload(token_user), salt=CAM_TOKEN_SALT)
            jwt = str(RefreshToken.for_user(token_user).access_token)
            executor = MigrationExecutor(connection)
            executor.migrate(current)
            new_apps = executor.loader.project_state(current).apps
            NewUser = new_apps.get_model('accounts', 'User')
            assert NewUser.objects.get(pk=first.pk).is_active is False
            assert NewUser.objects.get(pk=first.pk).password.startswith('!')
            assert NewUser.objects.get(pk=staff.pk).is_active is True
            assert new_apps.get_model('cameras','MonoblockCameraSettings').objects.get().camera_sources == ['cam2','cam5']
            assert new_apps.get_model('eventlog','EventLog').objects.get(pk=history.pk).user_id == first.pk
            user = RuntimeUser.objects.get(pk=first.pk)
            assert user.has_perm_code('shipping.load') is False
            assert _camera_token_user(cookie) is None
            old_session = APIClient()
            old_session.credentials(HTTP_AUTHORIZATION=f'Bearer {jwt}')
            assert old_session.get('/api/auth/me/').status_code == 401
            client = APIClient()
            client.force_authenticate(RuntimeUser.objects.get(pk=staff.pk))
            assert client.get('/api/cameras/monoblock-devices/').status_code == 404
            # Exercise rollback: no dropped table and no reactivated login.
            executor = MigrationExecutor(connection)
            executor.migrate(previous)
            restored = executor.loader.project_state(previous).apps
            assert restored.get_model('cameras','MonoblockDevice').objects.filter(is_active=True).count() == 0
            assert restored.get_model('accounts','User').objects.get(pk=first.pk).is_active is False
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
