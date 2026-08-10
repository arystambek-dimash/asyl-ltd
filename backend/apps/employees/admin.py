from django import forms
from django.contrib import admin

from .models import Employee


class EmployeeAdminForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)

    class Meta:
        model = Employee
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.user_id:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeAdminForm
    list_display = ("name", "username", "position", "is_active")
    list_select_related = ("user",)
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "phone",
        "position",
    )

    @admin.display(ordering="user__username", description="Логин")
    def username(self, obj):
        return obj.user.username

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        user = obj.user
        user.first_name = form.cleaned_data["first_name"]
        user.last_name = form.cleaned_data["last_name"]
        user.save(update_fields=["first_name", "last_name"])
