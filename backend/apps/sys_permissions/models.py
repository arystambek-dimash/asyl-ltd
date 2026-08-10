from django.db import models


class Permission(models.Model):
    code = models.CharField(max_length=50, unique=True)
    section = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    label = models.CharField(max_length=120)

    def __str__(self):
        return self.code
