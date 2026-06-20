from django.urls import path
from .views import ArriveView, LoadView, ShipView

urlpatterns = [
    path("orders/<int:pk>/arrive/", ArriveView.as_view()),
    path("orders/<int:pk>/load/", LoadView.as_view()),
    path("orders/<int:pk>/ship/", ShipView.as_view()),
]
