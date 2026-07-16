from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet, ReportSummaryView

router = DefaultRouter()
router.register("orders", OrderViewSet)
urlpatterns = [
    path("reports/summary/", ReportSummaryView.as_view(), name="report-summary"),
] + router.urls
