from django.db import models


class Permission(models.Model):
    code = models.CharField(max_length=50, unique=True)
    section = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    label = models.CharField(max_length=120)

    def __str__(self):
        return self.code


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=300, blank=True, default="")
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)

    def __str__(self):
        return self.name
