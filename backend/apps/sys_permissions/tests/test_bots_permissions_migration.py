from apps.sys_permissions.tests.migration_case import PermissionMigrationTestCase


class BotPermissionsMigrationTests(PermissionMigrationTestCase):
    """Журнал WhatsApp-бота сразу видят администраторы; у остальных права не меняются."""

    rbac_from = "0027_loader_transport_permissions"
    rbac_to = "0028_bots_permissions"

    def seed(self):
        self.admin = self.employee("mig-bot-admin", "sys_permissions.manage", "events.view")
        self.loader = self.employee("mig-bot-loader", "loader.view", "loader.wagons")

    def test_admins_get_the_journal_and_others_keep_their_rights(self):
        assert self.codes(self.admin) == {"sys_permissions.manage", "events.view", "bots.view", "bots.manage"}
        assert self.codes(self.loader) == {"loader.view", "loader.wagons"}

    def test_catalog_rows_are_labelled(self):
        Permission = self.apps.get_model("rbac", "Permission")
        labels = dict(Permission.objects.filter(section="bots").values_list("code", "label"))
        assert labels == {"bots.view": "WhatsApp-бот: Журнал сообщений",
                          "bots.manage": "WhatsApp-бот: Провести и пропустить сообщение"}
