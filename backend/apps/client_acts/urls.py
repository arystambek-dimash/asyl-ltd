from django.urls import path

from .views import ClientActImportView

urlpatterns = [
    path("clients/<int:pk>/act-import/", ClientActImportView.as_view()),
]
