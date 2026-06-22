from django.urls import path
from .views import ArriveView, LoadView, FinishLoadingView, ShipView

urlpatterns = [
    path("orders/<int:pk>/arrive/", ArriveView.as_view()),
    path("orders/<int:pk>/load/", LoadView.as_view()),
    path("orders/<int:pk>/finish-loading/", FinishLoadingView.as_view()),
    path("orders/<int:pk>/ship/", ShipView.as_view()),
]
