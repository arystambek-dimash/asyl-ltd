from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    OrderViewSet, PaymentReceiptView, PaymentRefundView,
    PaymentProviderIssueView, PaymentRejectView, PaymentRestoreView,
    PaymentTransactionListView, ReportSummaryView,
)

router = DefaultRouter()
router.register("orders", OrderViewSet)
urlpatterns = [
    path("reports/summary/", ReportSummaryView.as_view(), name="report-summary"),
    path("payment-transactions/", PaymentTransactionListView.as_view(),
         name="payment-transactions"),
    path("payment-transactions/<int:payment_id>/receipt/",
         PaymentReceiptView.as_view(), name="payment-receipt"),
    path("payment-transactions/<int:payment_id>/refund/",
         PaymentRefundView.as_view(), name="payment-refund"),
    path("payment-transactions/<int:payment_id>/reject/",
         PaymentRejectView.as_view(), name="payment-reject"),
    path("payment-transactions/<int:payment_id>/restore/",
         PaymentRestoreView.as_view(), name="payment-restore"),
    path("payment-transactions/<int:payment_id>/issue/",
         PaymentProviderIssueView.as_view(), name="payment-provider-issue"),
] + router.urls
