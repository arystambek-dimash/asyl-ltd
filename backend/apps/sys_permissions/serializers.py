from rest_framework import serializers

from .models import Permission
from .perms import SECTION_LABELS


class PermissionSerializer(serializers.ModelSerializer):
    # Заголовок раздела в пикере прав = страница меню; снятые разделы — сырым кодом.
    section_label = serializers.SerializerMethodField()

    class Meta:
        model = Permission
        fields = ["id", "code", "section", "action", "label", "section_label"]

    def get_section_label(self, permission):
        return SECTION_LABELS.get(permission.section, permission.section)
