# Инвентаризация API и backend-модулей — 2026-09-06

Сгенерировано из Django URL resolver и AST текущего checkout. Наличие маршрута не является доказательством корректности DTO; проверенные сквозные контракты описаны в основном отчёте.

Уникальных записей маршрутизации без format suffix: 205.

| Маршрут | Методы / действия | View | Источник |
|---|---|---|---|
| `/api/auth/login/` | POST:post | ThrottledTokenObtainPairView | [backend/config/urls.py:15](../../backend/config/urls.py#L15) |
| `/api/auth/initial-password/` | POST:post | InitialPasswordView | [backend/apps/accounts/views.py:20](../../backend/apps/accounts/views.py#L20) |
| `/api/auth/refresh/` | POST:post | RevocableTokenRefreshView | [backend/apps/accounts/views.py:16](../../backend/apps/accounts/views.py#L16) |
| `/api/auth/me/` | GET:get | MeView | [backend/apps/accounts/views.py:35](../../backend/apps/accounts/views.py#L35) |
| `/api/webhooks/apipay/` | FUNCTION:see source | apipay_webhook | [backend/.venv/lib/python3.11/site-packages/django/views/decorators/csrf.py:357](../../backend/.venv/lib/python3.11/site-packages/django/views/decorators/csrf.py#L357) |
| `/api/^products/$` | GET:list, POST:create | ProductViewSet | [backend/apps/catalog/views.py:19](../../backend/apps/catalog/views.py#L19) |
| `/api/^products/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | ProductViewSet | [backend/apps/catalog/views.py:19](../../backend/apps/catalog/views.py#L19) |
| `/api/^products/(?P<pk>[^/.]+)/archive/$` | POST:archive | ProductViewSet | [backend/apps/catalog/views.py:19](../../backend/apps/catalog/views.py#L19) |
| `/api/^products/(?P<pk>[^/.]+)/restore/$` | POST:restore | ProductViewSet | [backend/apps/catalog/views.py:19](../../backend/apps/catalog/views.py#L19) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/client-prices/` | GET:get | ClientPricesView | [backend/apps/catalog/views.py:61](../../backend/apps/catalog/views.py#L61) |
| `/api/^clients/$` | GET:list, POST:create | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/statement/$` | GET:all_statement | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/debts/$` | GET:debts | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/picker/$` | GET:picker | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/debt-detail/$` | GET:debt_detail | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/history/$` | GET:history | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/prices/$` | GET:prices, PUT:prices | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/purge/$` | POST:purge | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/password/$` | POST:set_password | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^clients/(?P<pk>[^/.]+)/statement/$` | GET:statement | ClientViewSet | [backend/apps/clients/views.py:96](../../backend/apps/clients/views.py#L96) |
| `/api/^stores/$` | GET:list, POST:create | StoreViewSet | [backend/apps/clients/views.py:657](../../backend/apps/clients/views.py#L657) |
| `/api/^stores/check-overdue/$` | POST:check_overdue | StoreViewSet | [backend/apps/clients/views.py:657](../../backend/apps/clients/views.py#L657) |
| `/api/^stores/debts/$` | GET:debts | StoreViewSet | [backend/apps/clients/views.py:657](../../backend/apps/clients/views.py#L657) |
| `/api/^stores/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | StoreViewSet | [backend/apps/clients/views.py:657](../../backend/apps/clients/views.py#L657) |
| `/api/^stores/(?P<pk>[^/.]+)/debt-detail/$` | GET:debt_detail | StoreViewSet | [backend/apps/clients/views.py:657](../../backend/apps/clients/views.py#L657) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/^departments/$` | GET:list, POST:create | DepartmentViewSet | [backend/apps/sales/views.py:14](../../backend/apps/sales/views.py#L14) |
| `/api/^departments/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | DepartmentViewSet | [backend/apps/sales/views.py:14](../../backend/apps/sales/views.py#L14) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/^events/$` | GET:list | EventLogViewSet | [backend/apps/eventlog/views.py:24](../../backend/apps/eventlog/views.py#L24) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/reports/summary/` | GET:get | ReportSummaryView | [backend/apps/orders/views.py:281](../../backend/apps/orders/views.py#L281) |
| `/api/payment-transactions/` | GET:get | PaymentTransactionListView | [backend/apps/orders/views.py:303](../../backend/apps/orders/views.py#L303) |
| `/api/payment-transactions/<int:payment_id>/receipt/` | GET:get | PaymentReceiptView | [backend/apps/orders/views.py:455](../../backend/apps/orders/views.py#L455) |
| `/api/payment-transactions/<int:payment_id>/refund/` | POST:post | PaymentRefundView | [backend/apps/orders/views.py:478](../../backend/apps/orders/views.py#L478) |
| `/api/payment-transactions/<int:payment_id>/reject/` | POST:post | PaymentRejectView | [backend/apps/orders/views.py:595](../../backend/apps/orders/views.py#L595) |
| `/api/payment-transactions/<int:payment_id>/restore/` | POST:post | PaymentRestoreView | [backend/apps/orders/views.py:575](../../backend/apps/orders/views.py#L575) |
| `/api/payment-transactions/<int:payment_id>/issue/` | POST:post | PaymentProviderIssueView | [backend/apps/orders/views.py:544](../../backend/apps/orders/views.py#L544) |
| `/api/^orders/$` | GET:list, POST:create | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/cashier-log/$` | GET:cashier_log | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/dashboard-operational/$` | GET:dashboard_operational | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/department-summary/$` | GET:department_summary | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/form-options/$` | GET:form_options | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/payments-queue/$` | GET:payments_queue | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/trash/$` | GET:trash | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/trash-preview/$` | GET:trash_preview | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/status-requests/(?P<rid>\d+)/approve/$` | POST:approve_status | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/confirm/$` | POST:confirm | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/(?P<pid>\d+)/confirm/$` | POST:confirm_payment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/correct-price/$` | POST:correct_price | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/invoice-pdf/$` | GET:invoice_pdf | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/loading-camera/$` | POST:loading_camera | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/$` | POST:payments | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/purge/$` | DELETE:purge | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/(?P<pid>\d+)/receive/$` | POST:receive_payment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/reject/$` | POST:reject | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/(?P<pid>\d+)/reject/$` | POST:reject_payment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/status-requests/(?P<rid>\d+)/reject/$` | POST:reject_status | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/(?P<pid>\d+)/reopen/$` | POST:reopen_payment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/repeat/$` | POST:repeat | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/restore/$` | POST:restore | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/payments/(?P<pid>\d+)/restore/$` | POST:restore_payment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/rollback-shipment/$` | POST:rollback_shipment | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/set-status/$` | POST:set_status | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/status-requests/$` | GET:status_requests | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/^orders/(?P<pk>[^/.]+)/train/$` | POST:train | OrderViewSet | [backend/apps/orders/views.py:634](../../backend/apps/orders/views.py#L634) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/^warehouses/$` | GET:list, POST:create | WarehouseViewSet | [backend/apps/warehouse/views.py:31](../../backend/apps/warehouse/views.py#L31) |
| `/api/^warehouses/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | WarehouseViewSet | [backend/apps/warehouse/views.py:31](../../backend/apps/warehouse/views.py#L31) |
| `/api/^stock/$` | GET:list | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/^stock/adjust/$` | POST:adjust | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/^stock/movements/$` | GET:movements | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/^stock/receive/$` | POST:receive | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/^stock/transfer/$` | POST:transfer | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/^stock/(?P<pk>[^/.]+)/$` | DELETE:destroy | StockViewSet | [backend/apps/warehouse/views.py:70](../../backend/apps/warehouse/views.py#L70) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/orders/<int:pk>/arrive/` | POST:arrive | ShipmentViewSet | [backend/apps/shipments/views.py:21](../../backend/apps/shipments/views.py#L21) |
| `/api/orders/<int:pk>/load/` | POST:load | ShipmentViewSet | [backend/apps/shipments/views.py:21](../../backend/apps/shipments/views.py#L21) |
| `/api/orders/<int:pk>/finish-loading/` | POST:finish_loading | ShipmentViewSet | [backend/apps/shipments/views.py:21](../../backend/apps/shipments/views.py#L21) |
| `/api/orders/<int:pk>/rewind-loading/` | POST:rewind_loading | ShipmentViewSet | [backend/apps/shipments/views.py:21](../../backend/apps/shipments/views.py#L21) |
| `/api/orders/<int:pk>/ship/` | POST:ship | ShipmentViewSet | [backend/apps/shipments/views.py:21](../../backend/apps/shipments/views.py#L21) |
| `/api/^portal/catalog/$` | GET:list | PortalCatalogViewSet | [backend/apps/portal/views.py:48](../../backend/apps/portal/views.py#L48) |
| `/api/^portal/orders/$` | GET:list, POST:create | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/$` | GET:retrieve | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/invoice/$` | GET:invoice | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/pay/$` | POST:pay | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/receipt/$` | GET:receipt | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/payments/(?P<payment_id>\d+)/release/$` | POST:release_payment | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/request-debt/$` | POST:request_debt | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/orders/(?P<pk>[^/.]+)/truck/$` | PATCH:truck | PortalOrderViewSet | [backend/apps/portal/views.py:100](../../backend/apps/portal/views.py#L100) |
| `/api/^portal/stores/$` | GET:list | PortalStoreViewSet | [backend/apps/portal/views.py:35](../../backend/apps/portal/views.py#L35) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/portal/register/` | POST:post | RegisterView | [backend/apps/portal/registration.py:84](../../backend/apps/portal/registration.py#L84) |
| `/api/^portal/notifications/$` | GET:list | NotificationViewSet | [backend/apps/notifications/views.py:9](../../backend/apps/notifications/views.py#L9) |
| `/api/^portal/notifications/(?P<pk>[^/.]+)/read/$` | POST:read | NotificationViewSet | [backend/apps/notifications/views.py:9](../../backend/apps/notifications/views.py#L9) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/^permissions/$` | GET:list | PermissionViewSet | [backend/apps/sys_permissions/views.py:8](../../backend/apps/sys_permissions/views.py#L8) |
| `/api/^permissions/(?P<pk>[^/.]+)/$` | GET:retrieve | PermissionViewSet | [backend/apps/sys_permissions/views.py:8](../../backend/apps/sys_permissions/views.py#L8) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/^employees/$` | GET:list, POST:create | EmployeeViewSet | [backend/apps/employees/views.py:20](../../backend/apps/employees/views.py#L20) |
| `/api/^employees/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | EmployeeViewSet | [backend/apps/employees/views.py:20](../../backend/apps/employees/views.py#L20) |
| `/api/^employees/(?P<pk>[^/.]+)/security/$` | PATCH:security | EmployeeViewSet | [backend/apps/employees/views.py:20](../../backend/apps/employees/views.py#L20) |
| `/api/^employees/(?P<pk>[^/.]+)/password/$` | POST:set_password | EmployeeViewSet | [backend/apps/employees/views.py:20](../../backend/apps/employees/views.py#L20) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/integrations/vehicle-plate-events` | POST:post | VehiclePlateWebhookView | [backend/apps/cameras/vehicle_plate_events.py:468](../../backend/apps/cameras/vehicle_plate_events.py#L468) |
| `/api/vehicle-plate-events` | GET:get | VehiclePlateEventListView | [backend/apps/cameras/vehicle_plate_events.py:681](../../backend/apps/cameras/vehicle_plate_events.py#L681) |
| `/api/cameras/` | GET:get, PATCH:patch | CameraListView | [backend/apps/cameras/api_views/configuration.py:38](../../backend/apps/cameras/api_views/configuration.py#L38) |
| `/api/cameras/token/` | POST:post | CameraTokenView | [backend/apps/cameras/api_views/access.py:137](../../backend/apps/cameras/api_views/access.py#L137) |
| `/api/cameras/auth/` | GET:get | CameraAuthView | [backend/apps/cameras/api_views/access.py:154](../../backend/apps/cameras/api_views/access.py#L154) |
| `/api/cameras/health/` | GET:get | CameraHealthView | [backend/apps/cameras/api_views/operations.py:723](../../backend/apps/cameras/api_views/operations.py#L723) |
| `/api/cameras/monoblock-settings/` | GET:get, PUT:put | MonoblockCameraSettingsView | [backend/apps/cameras/api_views/configuration.py:151](../../backend/apps/cameras/api_views/configuration.py#L151) |
| `/api/cameras/monoblock-devices/` | GET:get, POST:post | MonoblockDeviceListView | [backend/apps/cameras/api_views/configuration.py:421](../../backend/apps/cameras/api_views/configuration.py#L421) |
| `/api/cameras/monoblock-devices/<int:pk>/` | PATCH:patch, PUT:put, DELETE:delete | MonoblockDeviceDetailView | [backend/apps/cameras/api_views/configuration.py:509](../../backend/apps/cameras/api_views/configuration.py#L509) |
| `/api/cameras/always-on-settings/` | GET:get, PUT:put | AlwaysOnCameraSettingsView | [backend/apps/cameras/api_views/operations.py:184](../../backend/apps/cameras/api_views/operations.py#L184) |
| `/api/cameras/always-on-detections/` | GET:get | AlwaysOnDetectionsView | [backend/apps/cameras/api_views/operations.py:155](../../backend/apps/cameras/api_views/operations.py#L155) |
| `/api/cameras/shipping-continuous-settings/` | GET:get | ShippingContinuousSettingsView | [backend/apps/cameras/api_views/operations.py:351](../../backend/apps/cameras/api_views/operations.py#L351) |
| `/api/cameras/shipping-continuous-detections/` | GET:get | ShippingContinuousDetectionsView | [backend/apps/cameras/api_views/operations.py:424](../../backend/apps/cameras/api_views/operations.py#L424) |
| `/api/cameras/shipping-continuous-analytics/` | GET:get | ShippingContinuousAnalyticsView | [backend/apps/cameras/api_views/operations.py:454](../../backend/apps/cameras/api_views/operations.py#L454) |
| `/api/cameras/wagon-number-settings/` | GET:get, PUT:put | WagonNumberCameraSettingsView | [backend/apps/cameras/api_views/operations.py:469](../../backend/apps/cameras/api_views/operations.py#L469) |
| `/api/cameras/always-on-analytics/` | GET:get | AlwaysOnAnalyticsView | [backend/apps/cameras/api_views/operations.py:550](../../backend/apps/cameras/api_views/operations.py#L550) |
| `/api/cameras/always-on-production/` | GET:get, PATCH:patch, PUT:put | AlwaysOnProductionView | [backend/apps/cameras/api_views/operations.py:645](../../backend/apps/cameras/api_views/operations.py#L645) |
| `/api/cameras/always-on-production/batches/<int:batch_id>/retry/` | POST:post | AlwaysOnStockRetryView | [backend/apps/cameras/api_views/operations.py:682](../../backend/apps/cameras/api_views/operations.py#L682) |
| `/api/cameras/always-on-analytics/<str:cam>/subtract/` | POST:post | AlwaysOnAnalyticsSubtractView | [backend/apps/cameras/api_views/operations.py:560](../../backend/apps/cameras/api_views/operations.py#L560) |
| `/api/cameras/always-on-analytics/archives/` | GET:get, POST:post, DELETE:delete | AlwaysOnAnalyticsArchiveView | [backend/apps/cameras/api_views/operations.py:578](../../backend/apps/cameras/api_views/operations.py#L578) |
| `/api/cameras/always-on-analytics/archives/<int:archive_id>/` | GET:get, POST:post, DELETE:delete | AlwaysOnAnalyticsArchiveView | [backend/apps/cameras/api_views/operations.py:578](../../backend/apps/cameras/api_views/operations.py#L578) |
| `/api/cameras/always-on-analytics/<str:cam>/archive/` | GET:get, POST:post, DELETE:delete | AlwaysOnAnalyticsArchiveView | [backend/apps/cameras/api_views/operations.py:578](../../backend/apps/cameras/api_views/operations.py#L578) |
| `/api/cameras/shipping-settings/` | GET:get, PATCH:patch, PUT:put | ShippingBoardSettingsView | [backend/apps/cameras/api_views/operations.py:689](../../backend/apps/cameras/api_views/operations.py#L689) |
| `/api/cameras/ai/sessions/` | GET:get | CameraAiSessionListView | [backend/apps/cameras/api_views/history.py:33](../../backend/apps/cameras/api_views/history.py#L33) |
| `/api/cameras/ai/history/` | GET:get | CameraAiSessionHistoryView | [backend/apps/cameras/api_views/history.py:140](../../backend/apps/cameras/api_views/history.py#L140) |
| `/api/cameras/ai/history/<int:pk>/recording/` | GET:get | CameraAiRecordingView | [backend/apps/cameras/api_views/history.py:219](../../backend/apps/cameras/api_views/history.py#L219) |
| `/api/cameras/ai/history/<int:pk>/recording/video/` | GET:get | CameraAiRecordingVideoView | [backend/apps/cameras/api_views/history.py:253](../../backend/apps/cameras/api_views/history.py#L253) |
| `/api/cameras/<str:cam>/counting-line` | GET:get, PUT:put | CameraCountingLineView | [backend/apps/cameras/api_views/counting.py:170](../../backend/apps/cameras/api_views/counting.py#L170) |
| `/api/cameras/vehicle-plate-runtime/` | GET:get, PUT:put | VehiclePlateRuntimeView | [backend/apps/cameras/api_views/vehicle_runtime.py:371](../../backend/apps/cameras/api_views/vehicle_runtime.py#L371) |
| `/api/cameras/<str:cam>/vehicle-plate-runtime/` | GET:get, PUT:put | VehiclePlateRuntimeView | [backend/apps/cameras/api_views/vehicle_runtime.py:371](../../backend/apps/cameras/api_views/vehicle_runtime.py#L371) |
| `/api/cameras/<str:cam>/ai/` | GET:get, POST:post, DELETE:delete | CameraAiView | [backend/apps/cameras/api_views/counting.py:194](../../backend/apps/cameras/api_views/counting.py#L194) |
| `/api/cameras/<str:cam>/ai/reset/` | POST:post | CameraAiResetView | [backend/apps/cameras/api_views/counting.py:255](../../backend/apps/cameras/api_views/counting.py#L255) |
| `/api/task-notifications/` | GET:get, POST:post | TaskNotificationView | [backend/apps/tasks/views.py:164](../../backend/apps/tasks/views.py#L164) |
| `/api/task-assignees/` | GET:get | TaskAssigneeListView | [backend/apps/tasks/views.py:184](../../backend/apps/tasks/views.py#L184) |
| `/api/task-attachments/<int:pk>/` | GET:get | TaskAttachmentDownloadView | [backend/apps/tasks/views.py:205](../../backend/apps/tasks/views.py#L205) |
| `/api/^tasks/$` | GET:list, POST:create | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/attachments/(?P<attachment_id>\d+)/url/$` | GET:attachment_url | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/complete/$` | POST:complete | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/reassign/$` | POST:reassign | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/reopen/$` | POST:reopen | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/^tasks/(?P<pk>[^/.]+)/attachments/$` | POST:upload | TaskViewSet | [backend/apps/tasks/views.py:35](../../backend/apps/tasks/views.py#L35) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |
| `/api/truck-scale/reading/` | GET:get | TruckScaleReadingView | [backend/apps/grain/views.py:67](../../backend/apps/grain/views.py#L67) |
| `/api/grain/automatic-passage-scale/acknowledge/` | POST:post | AutomaticPassageScaleAcknowledgeView | [backend/apps/grain/views.py:85](../../backend/apps/grain/views.py#L85) |
| `/api/grain/automatic-passage-scale/runtime/` | GET:get | AutomaticPassageScaleRuntimeView | [backend/apps/grain/views.py:136](../../backend/apps/grain/views.py#L136) |
| `/api/grain/automatic-passage-scale/settings/` | GET:get, PATCH:patch, PUT:put | AutomaticPassageScaleSettingsView | [backend/apps/grain/views.py:147](../../backend/apps/grain/views.py#L147) |
| `/api/truck-scales/<str:scale_key>/reading/` | GET:get | TruckScaleReadingView | [backend/apps/grain/views.py:67](../../backend/apps/grain/views.py#L67) |
| `/api/grain/photos/<str:kind>/<int:pk>/` | GET:get | WeighingPhotoView | [backend/apps/grain/photos.py:61](../../backend/apps/grain/photos.py#L61) |
| `/api/^grain/supplies/$` | GET:list, POST:create | GrainSupplyViewSet | [backend/apps/grain/views.py:271](../../backend/apps/grain/views.py#L271) |
| `/api/^grain/supplies/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | GrainSupplyViewSet | [backend/apps/grain/views.py:271](../../backend/apps/grain/views.py#L271) |
| `/api/^grain/supplies/(?P<pk>[^/.]+)/wagons/$` | POST:add_wagons | GrainSupplyViewSet | [backend/apps/grain/views.py:271](../../backend/apps/grain/views.py#L271) |
| `/api/^grain/supplies/(?P<pk>[^/.]+)/publish/$` | POST:publish | GrainSupplyViewSet | [backend/apps/grain/views.py:271](../../backend/apps/grain/views.py#L271) |
| `/api/^grain/wagons/$` | GET:list | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/arrive/$` | POST:arrive | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/camera-arrive/$` | POST:camera_arrive | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/passage/$` | POST:passage | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/vehicle-plate-candidates/$` | GET:vehicle_plate_candidates | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/$` | GET:retrieve | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/approve/$` | POST:approve | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/assign-silo/$` | POST:assign_silo_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/change-silo/$` | POST:change_silo_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/delete/$` | DELETE:delete_wagon | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/entry-weight/$` | POST:entry_weight | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/exit/$` | POST:exit | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/exit-weight/$` | POST:exit_weight | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/finish-unloading/$` | POST:finish_unloading_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/gross/$` | POST:gross | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/inventory/$` | POST:inventory | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/lab/$` | POST:lab | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/pause-unloading/$` | POST:pause_unloading | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/resolve-discrepancy/$` | POST:resolve_discrepancy_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/resolve-simple-discrepancy/$` | POST:resolve_simple_discrepancy_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/number/$` | PATCH:set_number, POST:set_number | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/start-unloading/$` | POST:start_unloading_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/suggest-silos/$` | GET:suggest_silos_action | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/tare/$` | POST:tare | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/wagons/(?P<pk>[^/.]+)/timeline/$` | GET:timeline | WagonViewSet | [backend/apps/grain/views.py:337](../../backend/apps/grain/views.py#L337) |
| `/api/^grain/silos/$` | GET:list, POST:create | SiloViewSet | [backend/apps/grain/views.py:978](../../backend/apps/grain/views.py#L978) |
| `/api/^grain/silos/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | SiloViewSet | [backend/apps/grain/views.py:978](../../backend/apps/grain/views.py#L978) |
| `/api/^grain/silos/(?P<pk>[^/.]+)/adjust/$` | POST:adjust | SiloViewSet | [backend/apps/grain/views.py:978](../../backend/apps/grain/views.py#L978) |
| `/api/^grain/silos/(?P<pk>[^/.]+)/movements/$` | GET:movements | SiloViewSet | [backend/apps/grain/views.py:978](../../backend/apps/grain/views.py#L978) |
| `/api/^grain/unassigned-weighings/$` | GET:list | UnassignedWeighingViewSet | [backend/apps/grain/views.py:682](../../backend/apps/grain/views.py#L682) |
| `/api/^grain/unassigned-weighings/(?P<pk>[^/.]+)/$` | GET:retrieve | UnassignedWeighingViewSet | [backend/apps/grain/views.py:682](../../backend/apps/grain/views.py#L682) |
| `/api/^grain/unassigned-weighings/(?P<pk>[^/.]+)/assign/$` | POST:assign | UnassignedWeighingViewSet | [backend/apps/grain/views.py:682](../../backend/apps/grain/views.py#L682) |
| `/api/^grain/unassigned-weighings/(?P<pk>[^/.]+)/create-passage/$` | POST:create_passage | UnassignedWeighingViewSet | [backend/apps/grain/views.py:682](../../backend/apps/grain/views.py#L682) |
| `/api/^grain/unassigned-weighings/(?P<pk>[^/.]+)/discard/$` | POST:discard | UnassignedWeighingViewSet | [backend/apps/grain/views.py:682](../../backend/apps/grain/views.py#L682) |
| `/api/^grain/orientation-samples/$` | GET:list | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/orientation-samples/purge/$` | POST:purge | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/orientation-samples/summary/$` | GET:summary | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/orientation-samples/(?P<pk>[^/.]+)/$` | GET:retrieve | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/orientation-samples/(?P<pk>[^/.]+)/exclude/$` | POST:exclude | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/orientation-samples/(?P<pk>[^/.]+)/label/$` | POST:label | VehicleOrientationSampleViewSet | [backend/apps/grain/views.py:800](../../backend/apps/grain/views.py#L800) |
| `/api/^grain/silo-types/$` | GET:list, POST:create | SiloTypeViewSet | [backend/apps/grain/views.py:1034](../../backend/apps/grain/views.py#L1034) |
| `/api/^grain/silo-types/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | SiloTypeViewSet | [backend/apps/grain/views.py:1034](../../backend/apps/grain/views.py#L1034) |
| `/api/^grain/types/$` | GET:list, POST:create | SiloTypeViewSet | [backend/apps/grain/views.py:1034](../../backend/apps/grain/views.py#L1034) |
| `/api/^grain/types/(?P<pk>[^/.]+)/$` | GET:retrieve, PUT:update, PATCH:partial_update, DELETE:destroy | SiloTypeViewSet | [backend/apps/grain/views.py:1034](../../backend/apps/grain/views.py#L1034) |
| `/api/` | GET:get | APIRootView | [backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py:314](../../backend/.venv/lib/python3.11/site-packages/rest_framework/routers.py#L314) |

## Модули

| Приложение | Файлов без tests/migrations | Строк |
|---|---:|---:|
| __pycache__ | 0 | 0 |
| accounts | 9 | 354 |
| cameras | 36 | 14208 |
| catalog | 8 | 342 |
| clients | 19 | 4033 |
| common | 8 | 290 |
| employees | 7 | 601 |
| eventlog | 8 | 147 |
| grain | 22 | 10819 |
| notifications | 7 | 65 |
| orders | 25 | 9737 |
| portal | 9 | 742 |
| sales | 7 | 228 |
| shipments | 8 | 904 |
| sys_permissions | 9 | 221 |
| tasks | 9 | 771 |
| warehouse | 8 | 1271 |

### __pycache__

```text
```

### accounts

```text
backend/apps/accounts/models.py:7 User
backend/apps/accounts/serializers.py:20 _password_change_required
backend/apps/accounts/serializers.py:29 PasswordChangeAwareTokenObtainPairSerializer
backend/apps/accounts/serializers.py:41 RevocableTokenRefreshSerializer
backend/apps/accounts/serializers.py:71 InitialPasswordSerializer
backend/apps/accounts/serializers.py:138 MeSerializer
backend/apps/accounts/apps.py:4 AccountsConfig
backend/apps/accounts/views.py:16 RevocableTokenRefreshView
backend/apps/accounts/views.py:20 InitialPasswordView
backend/apps/accounts/views.py:35 MeView
backend/apps/accounts/management/commands/create_superuser_env.py:27 Command
```

### cameras

```text
backend/apps/cameras/alerts.py:22 Delivery
backend/apps/cameras/alerts.py:28 _post_json
backend/apps/cameras/alerts.py:41 _post_telegram
backend/apps/cameras/alerts.py:60 send
backend/apps/cameras/recordings.py:17 RecordingUnavailable
backend/apps/cameras/recordings.py:21 _request
backend/apps/cameras/recordings.py:39 list_segments
backend/apps/cameras/recordings.py:81 open_segment
backend/apps/cameras/recordings.py:93 delete_session_segments
backend/apps/cameras/sessions.py:8 AiSessionBusy
backend/apps/cameras/sessions.py:15 current_for_camera
backend/apps/cameras/sessions.py:25 current_for_order
backend/apps/cameras/sessions.py:35 lock_camera_binding
backend/apps/cameras/sessions.py:41 reserve
backend/apps/cameras/services.py:81 normalize_camera_path
backend/apps/cameras/services.py:91 update_cached_counting_line
backend/apps/cameras/services.py:128 _offline_view
backend/apps/cameras/services.py:139 _refresh_cameras
backend/apps/cameras/services.py:155 discover_cameras
backend/apps/cameras/services.py:196 _discover_by_inventory
backend/apps/cameras/services.py:255 _natural
backend/apps/cameras/services.py:260 _static_slot
backend/apps/cameras/services.py:268 _sync_go2rtc
backend/apps/cameras/services.py:289 _go2rtc_put
backend/apps/cameras/services.py:300 _probe_path
backend/apps/cameras/services.py:324 _discover_by_probe
backend/apps/cameras/models.py:9 AiCountingSession
backend/apps/cameras/models.py:75 MonoblockCameraSettings
backend/apps/cameras/models.py:260 MonoblockDevice
backend/apps/cameras/models.py:288 AlwaysOnCounterCursor
backend/apps/cameras/models.py:334 AlwaysOnImportedEvent
backend/apps/cameras/models.py:400 ManualBagAnalyticsImportBatch
backend/apps/cameras/models.py:434 ManualBagAnalyticsImportEvent
backend/apps/cameras/models.py:477 VehiclePlateEvent
backend/apps/cameras/models.py:533 AlwaysOnDailyAnalytics
backend/apps/cameras/models.py:574 ShippingDailyAnalytics
backend/apps/cameras/models.py:605 ShippingAnalyticsBootstrap
backend/apps/cameras/models.py:617 ContinuousCameraRole
backend/apps/cameras/models.py:640 AlwaysOnColorProductMapping
backend/apps/cameras/models.py:670 AlwaysOnWarehouseRoute
backend/apps/cameras/models.py:701 AlwaysOnProductionRun
backend/apps/cameras/models.py:732 AlwaysOnProductionCorrection
backend/apps/cameras/models.py:759 AlwaysOnStockBatch
backend/apps/cameras/models.py:806 AlwaysOnStockPosting
backend/apps/cameras/models.py:835 AlwaysOnCountArchive
backend/apps/cameras/models.py:870 CameraHealthState
backend/apps/cameras/models.py:913 CameraIncident
backend/apps/cameras/vehicle_plate_events.py:52 PayloadTooLarge
backend/apps/cameras/vehicle_plate_events.py:58 _reject_nonfinite_json
backend/apps/cameras/vehicle_plate_events.py:62 BoundedJSONParser
backend/apps/cameras/vehicle_plate_events.py:82 _parse_uuid
backend/apps/cameras/vehicle_plate_events.py:93 _decimal_number
backend/apps/cameras/vehicle_plate_events.py:116 VehiclePlateWebhookSerializer
backend/apps/cameras/vehicle_plate_events.py:226 VehiclePlateEventSerializer
backend/apps/cameras/vehicle_plate_events.py:253 VehiclePlateWebhookRateThrottle
backend/apps/cameras/vehicle_plate_events.py:269 VehiclePlateEventPagination
backend/apps/cameras/vehicle_plate_events.py:295 _bounded_json_number
backend/apps/cameras/vehicle_plate_events.py:305 _project_bbox
backend/apps/cameras/vehicle_plate_events.py:330 _project_vehicle_roi
backend/apps/cameras/vehicle_plate_events.py:348 _project_image
backend/apps/cameras/vehicle_plate_events.py:363 _project_models
backend/apps/cameras/vehicle_plate_events.py:374 _project_payload
backend/apps/cameras/vehicle_plate_events.py:404 _safe_log_value
backend/apps/cameras/vehicle_plate_events.py:413 _log_result
backend/apps/cameras/vehicle_plate_events.py:442 _authorized
backend/apps/cameras/vehicle_plate_events.py:459 _error_response
backend/apps/cameras/vehicle_plate_events.py:468 VehiclePlateWebhookView
backend/apps/cameras/vehicle_plate_events.py:681 VehiclePlateEventListView
backend/apps/cameras/event_sync.py:39 EventSyncError
backend/apps/cameras/event_sync.py:44 CountEvent
backend/apps/cameras/event_sync.py:63 EventPage
backend/apps/cameras/event_sync.py:72 SyncResult
backend/apps/cameras/event_sync.py:81 _daily_model
backend/apps/cameras/event_sync.py:89 _plain_int
backend/apps/cameras/event_sync.py:95 _optional_text
backend/apps/cameras/event_sync.py:109 _optional_confidence
backend/apps/cameras/event_sync.py:123 _parse_event
backend/apps/cameras/event_sync.py:182 _applies_to_continuous_analytics
backend/apps/cameras/event_sync.py:190 parse_page
backend/apps/cameras/event_sync.py:234 _event_color
backend/apps/cameras/event_sync.py:239 _event_brand
backend/apps/cameras/event_sync.py:249 mark_sync_failure
backend/apps/cameras/event_sync.py:275 require_fresh_drain
backend/apps/cameras/event_sync.py:298 request_stop_drain
backend/apps/cameras/event_sync.py:319 confirm_stop_drain
backend/apps/cameras/event_sync.py:344 reactivate_stop_drain
backend/apps/cameras/event_sync.py:386 _mark_events_observed
backend/apps/cameras/event_sync.py:397 _mark_events_unsupported
backend/apps/cameras/event_sync.py:426 _production_period_posted
backend/apps/cameras/event_sync.py:445 _assert_open_accounting_period
backend/apps/cameras/event_sync.py:470 apply_page
backend/apps/cameras/event_sync.py:765 sync_camera
backend/apps/cameras/health.py:44 _positive_int
backend/apps/cameras/health.py:78 expected_streams
backend/apps/cameras/health.py:103 Observation
backend/apps/cameras/health.py:122 _rtsp_path
backend/apps/cameras/health.py:129 _probe_rtsp
backend/apps/cameras/health.py:133 _inventory_component
backend/apps/cameras/health.py:168 _go2rtc_catalog
backend/apps/cameras/health.py:181 _go2rtc_frame
backend/apps/cameras/health.py:199 probe_once
backend/apps/cameras/health.py:396 _open_incident
backend/apps/cameras/health.py:404 _record_degraded_incident
backend/apps/cameras/health.py:435 _record_outage_incident
backend/apps/cameras/health.py:472 record_observation
backend/apps/cameras/health.py:568 _alert_payload
backend/apps/cameras/health.py:617 _deliver_incident
backend/apps/cameras/health.py:636 deliver_pending_alerts
backend/apps/cameras/health.py:693 monitor_once
backend/apps/cameras/health.py:701 state_payload
backend/apps/cameras/health.py:869 exit_code
backend/apps/cameras/counting.py:51 _lock_device_camera
backend/apps/cameras/counting.py:69 metadata
backend/apps/cameras/counting.py:97 _assert_order_department_scope
backend/apps/cameras/counting.py:104 _payload
backend/apps/cameras/counting.py:108 _valid_total
backend/apps/cameras/counting.py:114 _is_continuous_shipping
backend/apps/cameras/counting.py:121 _assert_expected_session
backend/apps/cameras/counting.py:134 _stream
backend/apps/cameras/counting.py:139 _cleanup_error
backend/apps/cameras/counting.py:144 _mark_failed_locked
backend/apps/cameras/counting.py:158 _activate_locked
backend/apps/cameras/counting.py:177 _save_live_status_locked
backend/apps/cameras/counting.py:185 _release_camera_binding
backend/apps/cameras/counting.py:194 _pending_cleanup
backend/apps/cameras/counting.py:207 _delete_exact_session
backend/apps/cameras/counting.py:247 _finish_pending_cleanup
backend/apps/cameras/counting.py:303 get_status
backend/apps/cameras/counting.py:342 _validate_start
backend/apps/cameras/counting.py:366 start
backend/apps/cameras/counting.py:501 _save_final_snapshot
backend/apps/cameras/counting.py:523 _stored_snapshot
backend/apps/cameras/counting.py:536 _capture_final
backend/apps/cameras/counting.py:577 _finish_with_authoritative_final
backend/apps/cameras/counting.py:611 _locked_open_session
backend/apps/cameras/counting.py:621 stop
backend/apps/cameras/counting.py:786 reset
backend/apps/cameras/production_repair.py:29 ProductionRepairError
backend/apps/cameras/production_repair.py:34 RebuiltRun
backend/apps/cameras/production_repair.py:45 ProductionRepairResult
backend/apps/cameras/production_repair.py:58 _day_window
backend/apps/cameras/production_repair.py:71 _local_day
backend/apps/cameras/production_repair.py:75 _plant_today
backend/apps/cameras/production_repair.py:81 _event_color
backend/apps/cameras/production_repair.py:89 _segment_events
backend/apps/cameras/production_repair.py:131 _color_totals
backend/apps/cameras/production_repair.py:138 _business_color_totals
backend/apps/cameras/production_repair.py:145 _calendar_color_totals
backend/apps/cameras/production_repair.py:152 _run_signature
backend/apps/cameras/production_repair.py:164 _validate_candidate_shape
backend/apps/cameras/production_repair.py:206 rebuild_event_production_runs
backend/apps/cameras/ai.py:33 vehicle_orientation
backend/apps/cameras/ai.py:73 AiUnavailable
backend/apps/cameras/ai.py:77 AiProtocolError
backend/apps/cameras/ai.py:81 AiError
backend/apps/cameras/ai.py:91 enabled
backend/apps/cameras/ai.py:95 _invalid_json_response
backend/apps/cameras/ai.py:101 _read_json_object
backend/apps/cameras/ai.py:126 _request
backend/apps/cameras/ai.py:184 _call
backend/apps/cameras/ai.py:212 normalize
backend/apps/cameras/ai.py:222 camera_id
backend/apps/cameras/ai.py:230 validate_counting_line
backend/apps/cameras/ai.py:274 _path
backend/apps/cameras/ai.py:278 inventory
backend/apps/cameras/ai.py:283 counting_line
backend/apps/cameras/ai.py:288 save_counting_line
backend/apps/cameras/ai.py:297 vehicle_number_info
backend/apps/cameras/ai.py:309 vehicle_roi
backend/apps/cameras/ai.py:321 save_vehicle_roi
backend/apps/cameras/ai.py:331 _recognize_vehicle_from_camera
backend/apps/cameras/ai.py:437 recognize_vehicle_from_camera
backend/apps/cameras/ai.py:457 retry_vehicle_recognition_from_camera
backend/apps/cameras/ai.py:480 _orientation_error
backend/apps/cameras/ai.py:489 _orientation_done
backend/apps/cameras/ai.py:499 post_orientation_sample
backend/apps/cameras/ai.py:534 delete_orientation_sample
backend/apps/cameras/ai.py:553 clear_orientation_samples
backend/apps/cameras/ai.py:574 vehicle_orientation_info
backend/apps/cameras/ai.py:587 fetch_vehicle_recognition_frame
backend/apps/cameras/ai.py:635 status
backend/apps/cameras/ai.py:640 assert_order_session_identity
backend/apps/cameras/ai.py:662 _order_session_ready
backend/apps/cameras/ai.py:687 wait_for_order_session
backend/apps/cameras/ai.py:710 start
backend/apps/cameras/ai.py:717 reset
backend/apps/cameras/ai.py:728 delete
backend/apps/cameras/ai.py:734 _normalize_always_on
backend/apps/cameras/ai.py:742 always_on_status
backend/apps/cameras/ai.py:757 count_events
backend/apps/cameras/ai.py:787 always_on_status_cached
backend/apps/cameras/ai.py:802 always_on_detections_cached
backend/apps/cameras/ai.py:850 cached_always_on_status
backend/apps/cameras/ai.py:861 invalidate_always_on_cache
backend/apps/cameras/ai.py:865 invalidate_counting_line_caches
backend/apps/cameras/ai.py:870 configure_always_on
backend/apps/cameras/ai.py:908 wagon_number_status
backend/apps/cameras/ai.py:919 wagon_number_status_cached
backend/apps/cameras/ai.py:934 configure_wagon_number
backend/apps/cameras/ai.py:954 delete_recordings
backend/apps/cameras/ai.py:959 camera_frame_jpeg
backend/apps/cameras/ai.py:984 detect_wagon_plate
backend/apps/cameras/ai.py:1007 accepted_plate_number
backend/apps/cameras/ai.py:1026 wagon_plate_scan
backend/apps/cameras/ai.py:1046 wagon_plate_seen
backend/apps/cameras/serializers.py:17 CameraRenameSerializer
backend/apps/cameras/serializers.py:57 CameraSourcesSerializer
backend/apps/cameras/serializers.py:95 MonoblockDeviceCreateUpdateSerializer
backend/apps/cameras/serializers.py:220 WagonNumberCameraSettingsSerializer
backend/apps/cameras/serializers.py:246 AlwaysOnAnalyticsSubtractSerializer
backend/apps/cameras/serializers.py:276 AlwaysOnAnalyticsArchiveSerializer
backend/apps/cameras/serializers.py:290 AlwaysOnProductMappingItemSerializer
backend/apps/cameras/serializers.py:301 AlwaysOnProductMappingsSerializer
backend/apps/cameras/serializers.py:315 ShippingBoardSettingsSerializer
backend/apps/cameras/serializers.py:346 CameraAiActionSerializer
backend/apps/cameras/apps.py:4 CamerasConfig
backend/apps/cameras/continuous.py:31 _always_on_policy_mutex
backend/apps/cameras/continuous.py:64 _live_sources
backend/apps/cameras/continuous.py:75 _processor_readiness
backend/apps/cameras/continuous.py:149 contour_readiness
backend/apps/cameras/continuous.py:167 contour_sync_state
backend/apps/cameras/continuous.py:181 policy_sync_state
backend/apps/cameras/continuous.py:206 always_on_sync_state
backend/apps/cameras/continuous.py:225 _draining_event_sources
backend/apps/cameras/continuous.py:241 _record_counts
backend/apps/cameras/continuous.py:281 _observed_analytics_scopes
backend/apps/cameras/continuous.py:301 _confirmed_shipping_sources
backend/apps/cameras/continuous.py:330 _confirm_shipping_bootstraps
backend/apps/cameras/continuous.py:335 _complete_shipping_bootstraps
backend/apps/cameras/continuous.py:340 sync_always_on_policy
backend/apps/cameras/continuous.py:373 reconcile
backend/apps/cameras/continuous.py:490 reconcile_wagon_number
backend/apps/cameras/continuous.py:523 poll_wagon_plate
backend/apps/cameras/analytics.py:31 _assert_ai247_reservation
backend/apps/cameras/analytics.py:46 _daily_model
backend/apps/cameras/analytics.py:55 confirm_shipping_bootstrap_scope
backend/apps/cameras/analytics.py:75 complete_shipping_bootstrap
backend/apps/cameras/analytics.py:152 _processor_total
backend/apps/cameras/analytics.py:163 _processor_colors
backend/apps/cameras/analytics.py:187 _counter_delta
backend/apps/cameras/analytics.py:193 _color_delta
backend/apps/cameras/analytics.py:205 _normalize_brand
backend/apps/cameras/analytics.py:212 _bounded_brand_delta
backend/apps/cameras/analytics.py:239 record_model_delta
backend/apps/cameras/analytics.py:302 _record_processor
backend/apps/cameras/analytics.py:365 record_snapshot
backend/apps/cameras/analytics.py:405 _row_payload
backend/apps/cameras/analytics.py:431 _normalized_colors
backend/apps/cameras/analytics.py:441 _merge_colors
backend/apps/cameras/analytics.py:449 _normalized_brands
backend/apps/cameras/analytics.py:453 _merge_brands
backend/apps/cameras/analytics.py:464 _breakdown_payload
backend/apps/cameras/analytics.py:492 _color_payload
backend/apps/cameras/analytics.py:496 _brand_payload
backend/apps/cameras/analytics.py:500 _dominant_brand
backend/apps/cameras/analytics.py:511 _history_payload
backend/apps/cameras/analytics.py:536 _event_sync_payload
backend/apps/cameras/analytics.py:576 _aggregate_sync_payload
backend/apps/cameras/analytics.py:595 today_payload
backend/apps/cameras/analytics.py:769 archive_camera
backend/apps/cameras/analytics.py:888 _archive_day_rows
backend/apps/cameras/analytics.py:924 _archive_payload
backend/apps/cameras/analytics.py:948 delete_archive
backend/apps/cameras/analytics.py:1030 archives_payload
backend/apps/cameras/analytics.py:1049 subtract_today
backend/apps/cameras/manual_analytics_import.py:46 ManualAnalyticsImportError
backend/apps/cameras/manual_analytics_import.py:51 RecoveredEvent
backend/apps/cameras/manual_analytics_import.py:73 ManualAnalyticsDocument
backend/apps/cameras/manual_analytics_import.py:95 ManualAnalyticsImportResult
backend/apps/cameras/manual_analytics_import.py:100 _fail
backend/apps/cameras/manual_analytics_import.py:104 _object
backend/apps/cameras/manual_analytics_import.py:110 _text
backend/apps/cameras/manual_analytics_import.py:124 _optional_text
backend/apps/cameras/manual_analytics_import.py:130 _integer
backend/apps/cameras/manual_analytics_import.py:136 _number
backend/apps/cameras/manual_analytics_import.py:145 _confidence
backend/apps/cameras/manual_analytics_import.py:154 _aware_datetime
backend/apps/cameras/manual_analytics_import.py:161 _normalized_color
backend/apps/cameras/manual_analytics_import.py:168 _normalized_brand
backend/apps/cameras/manual_analytics_import.py:177 _validate_event
backend/apps/cameras/manual_analytics_import.py:287 _aggregate
backend/apps/cameras/manual_analytics_import.py:305 _validate_declared_summaries
backend/apps/cameras/manual_analytics_import.py:356 load_manual_analytics_document
backend/apps/cameras/manual_analytics_import.py:479 _batch_metadata
backend/apps/cameras/manual_analytics_import.py:494 _event_metadata
backend/apps/cameras/manual_analytics_import.py:515 _assert_completed_batch
backend/apps/cameras/manual_analytics_import.py:533 _same_optional_float
backend/apps/cameras/manual_analytics_import.py:539 _assert_production_anchors_consumed
backend/apps/cameras/manual_analytics_import.py:616 _inspect_state
backend/apps/cameras/manual_analytics_import.py:660 inspect_manual_analytics_import
backend/apps/cameras/manual_analytics_import.py:672 _merged_breakdown
backend/apps/cameras/manual_analytics_import.py:691 apply_manual_analytics_import
backend/apps/cameras/policies.py:8 CameraRoleImmutable
backend/apps/cameras/policies.py:13 ShippingBootstrapPending
backend/apps/cameras/policies.py:18 reserve_camera_roles
backend/apps/cameras/policies.py:82 assert_no_pending_shipping_bootstrap
backend/apps/cameras/policies.py:104 active_device_for
backend/apps/cameras/policies.py:108 assert_device_camera
backend/apps/cameras/policies.py:115 session_started_by_name
backend/apps/cameras/policies.py:122 can_control_session
backend/apps/cameras/production.py:67 _default_timezone
backend/apps/cameras/production.py:73 _aware
backend/apps/cameras/production.py:80 _local
backend/apps/cameras/production.py:84 _iso
backend/apps/cameras/production.py:88 business_day_for
backend/apps/cameras/production.py:97 scheduled_for
backend/apps/cameras/production.py:103 _normalize_color
backend/apps/cameras/production.py:110 _positive_int
backend/apps/cameras/production.py:121 record_color_deltas
backend/apps/cameras/production.py:296 close_stale_runs
backend/apps/cameras/production.py:325 _compatibility_warehouse
backend/apps/cameras/production.py:340 _warehouse_for_camera
backend/apps/cameras/production.py:373 _effective_stock_warehouse_id
backend/apps/cameras/production.py:384 _stock_scope_for_warehouse
backend/apps/cameras/production.py:391 _product_payload
backend/apps/cameras/production.py:412 _mapping_payload
backend/apps/cameras/production.py:421 _run_payload
backend/apps/cameras/production.py:452 _is_run_smoothing_barrier
backend/apps/cameras/production.py:464 _merge_algorithm_runs
backend/apps/cameras/production.py:483 _coalesce_algorithm_runs
backend/apps/cameras/production.py:501 smooth_day_runs
backend/apps/cameras/production.py:557 _run_color_totals
backend/apps/cameras/production.py:564 _run_smoothing_payload
backend/apps/cameras/production.py:588 _posting_payload
backend/apps/cameras/production.py:601 _batch_payload
backend/apps/cameras/production.py:619 _day_totals
backend/apps/cameras/production.py:648 _selected_day
backend/apps/cameras/production.py:667 _dominant_brand_by_color
backend/apps/cameras/production.py:761 production_payload
backend/apps/cameras/production.py:962 _camera_has_unposted_production
backend/apps/cameras/production.py:991 save_mappings
backend/apps/cameras/production.py:1177 record_correction
backend/apps/cameras/production.py:1248 _locked_or_created_batch
backend/apps/cameras/production.py:1289 _assert_ai247_role
backend/apps/cameras/production.py:1303 _post_one
backend/apps/cameras/production.py:1557 _mark_failed
backend/apps/cameras/production.py:1571 _due_pairs
backend/apps/cameras/production.py:1606 post_due_stock
backend/apps/cameras/production.py:1626 retry_batch
backend/apps/cameras/api_views/configuration.py:38 CameraListView
backend/apps/cameras/api_views/configuration.py:82 _camera_role_conflict
backend/apps/cameras/api_views/configuration.py:97 _sync_effective_always_on
backend/apps/cameras/api_views/configuration.py:113 _sync_changed_device_policy
backend/apps/cameras/api_views/configuration.py:121 _assert_known_always_on_capacity
backend/apps/cameras/api_views/configuration.py:151 MonoblockCameraSettingsView
backend/apps/cameras/api_views/configuration.py:327 _device_payload
backend/apps/cameras/api_views/configuration.py:357 _unique_device_validation
backend/apps/cameras/api_views/configuration.py:376 _assert_device_can_change_binding
backend/apps/cameras/api_views/configuration.py:407 _assert_camera_has_no_active_work
backend/apps/cameras/api_views/configuration.py:421 MonoblockDeviceListView
backend/apps/cameras/api_views/configuration.py:509 MonoblockDeviceDetailView
backend/apps/cameras/api_views/vehicle_runtime.py:39 VehicleRuntimeContractError
backend/apps/cameras/api_views/vehicle_runtime.py:43 _mapping
backend/apps/cameras/api_views/vehicle_runtime.py:49 _boolean
backend/apps/cameras/api_views/vehicle_runtime.py:55 _source
backend/apps/cameras/api_views/vehicle_runtime.py:61 _text_or_none
backend/apps/cameras/api_views/vehicle_runtime.py:69 _non_negative_int
backend/apps/cameras/api_views/vehicle_runtime.py:75 _non_negative_number
backend/apps/cameras/api_views/vehicle_runtime.py:86 _project_stop_gate
backend/apps/cameras/api_views/vehicle_runtime.py:102 _project_monitor
backend/apps/cameras/api_views/vehicle_runtime.py:129 _project_roi
backend/apps/cameras/api_views/vehicle_runtime.py:154 _project_points
backend/apps/cameras/api_views/vehicle_runtime.py:205 project_vehicle_roi_update
backend/apps/cameras/api_views/vehicle_runtime.py:228 project_vehicle_roi_save_response
backend/apps/cameras/api_views/vehicle_runtime.py:255 _project_on_demand
backend/apps/cameras/api_views/vehicle_runtime.py:277 _browser_stream
backend/apps/cameras/api_views/vehicle_runtime.py:282 project_vehicle_runtime
backend/apps/cameras/api_views/vehicle_runtime.py:365 _error_response
backend/apps/cameras/api_views/vehicle_runtime.py:371 VehiclePlateRuntimeView
backend/apps/cameras/api_views/counting.py:19 _hidden_busy_metadata
backend/apps/cameras/api_views/counting.py:28 _session_is_visible
backend/apps/cameras/api_views/counting.py:37 _busy_response
backend/apps/cameras/api_views/counting.py:59 _ai_response
backend/apps/cameras/api_views/counting.py:87 _ai_proxy_response
backend/apps/cameras/api_views/counting.py:109 _order_id
backend/apps/cameras/api_views/counting.py:122 _loading_order
backend/apps/cameras/api_views/counting.py:134 _complete_order
backend/apps/cameras/api_views/counting.py:142 _action_input
backend/apps/cameras/api_views/counting.py:170 CameraCountingLineView
backend/apps/cameras/api_views/counting.py:194 CameraAiView
backend/apps/cameras/api_views/counting.py:255 CameraAiResetView
backend/apps/cameras/api_views/access.py:30 _camera_token_payload
backend/apps/cameras/api_views/access.py:41 _camera_token_user
backend/apps/cameras/api_views/access.py:77 _is_valid_camera_stream_source
backend/apps/cameras/api_views/access.py:106 _camera_stream_source
backend/apps/cameras/api_views/access.py:137 CameraTokenView
backend/apps/cameras/api_views/access.py:154 CameraAuthView
backend/apps/cameras/api_views/operations.py:45 _filtered_live
backend/apps/cameras/api_views/operations.py:86 _camera_role_conflict
backend/apps/cameras/api_views/operations.py:101 _assert_ai247_camera
backend/apps/cameras/api_views/operations.py:116 _assert_reserved_ai247_camera
backend/apps/cameras/api_views/operations.py:131 _HumanAlwaysOnReadPermission
backend/apps/cameras/api_views/operations.py:140 _AlwaysOnPermissionMixin
backend/apps/cameras/api_views/operations.py:155 AlwaysOnDetectionsView
backend/apps/cameras/api_views/operations.py:184 AlwaysOnCameraSettingsView
backend/apps/cameras/api_views/operations.py:344 _shipping_visible_sources
backend/apps/cameras/api_views/operations.py:351 ShippingContinuousSettingsView
backend/apps/cameras/api_views/operations.py:424 ShippingContinuousDetectionsView
backend/apps/cameras/api_views/operations.py:454 ShippingContinuousAnalyticsView
backend/apps/cameras/api_views/operations.py:469 WagonNumberCameraSettingsView
backend/apps/cameras/api_views/operations.py:550 AlwaysOnAnalyticsView
backend/apps/cameras/api_views/operations.py:560 AlwaysOnAnalyticsSubtractView
backend/apps/cameras/api_views/operations.py:578 AlwaysOnAnalyticsArchiveView
backend/apps/cameras/api_views/operations.py:645 AlwaysOnProductionView
backend/apps/cameras/api_views/operations.py:682 AlwaysOnStockRetryView
backend/apps/cameras/api_views/operations.py:689 ShippingBoardSettingsView
backend/apps/cameras/api_views/operations.py:723 CameraHealthView
backend/apps/cameras/api_views/history.py:33 CameraAiSessionListView
backend/apps/cameras/api_views/history.py:76 _recording_stream
backend/apps/cameras/api_views/history.py:87 _history_payload
backend/apps/cameras/api_views/history.py:122 _history_queryset
backend/apps/cameras/api_views/history.py:140 CameraAiSessionHistoryView
backend/apps/cameras/api_views/history.py:189 _history_session
backend/apps/cameras/api_views/history.py:193 _session_segments
backend/apps/cameras/api_views/history.py:207 _segment_video_url
backend/apps/cameras/api_views/history.py:219 CameraAiRecordingView
backend/apps/cameras/api_views/history.py:253 CameraAiRecordingVideoView
backend/apps/cameras/management/commands/check_camera_cutover.py:6 Command
backend/apps/cameras/management/commands/import_manual_bag_analytics.py:14 Command
backend/apps/cameras/management/commands/check_camera_health.py:47 human_diagnostics
backend/apps/cameras/management/commands/check_camera_health.py:116 Command
backend/apps/cameras/management/commands/post_always_on_stock.py:12 Command
backend/apps/cameras/management/commands/monitor_cameras.py:12 Command
backend/apps/cameras/management/commands/rebuild_event_production_runs.py:13 Command
```

### catalog

```text
backend/apps/catalog/services.py:9 archive_product
backend/apps/catalog/services.py:20 restore_product
backend/apps/catalog/models.py:7 Product
backend/apps/catalog/models.py:107 ClientPrice
backend/apps/catalog/serializers.py:8 ProductSerializer
backend/apps/catalog/serializers.py:50 ClientPriceUpdateItemSerializer
backend/apps/catalog/serializers.py:61 ClientPriceUpdateSerializer
backend/apps/catalog/apps.py:4 CatalogConfig
backend/apps/catalog/admin.py:6 ProductAdmin
backend/apps/catalog/views.py:19 ProductViewSet
backend/apps/catalog/views.py:61 ClientPricesView
```

### clients

```text
backend/apps/clients/services.py:12 client_history
backend/apps/clients/services.py:108 is_payment_window_open
backend/apps/clients/services.py:119 detect_overdue
backend/apps/clients/models.py:7 Client
backend/apps/clients/models.py:41 Store
backend/apps/clients/serializers.py:16 ClientReadSerializer
backend/apps/clients/serializers.py:100 ClientCreateUpdateSerializer
backend/apps/clients/serializers.py:188 ClientPasswordSerializer
backend/apps/clients/serializers.py:208 StoreSerializer
backend/apps/clients/apps.py:4 ClientsConfig
backend/apps/clients/views.py:59 ClientNoLongerAvailable
backend/apps/clients/views.py:70 StoreChanged
backend/apps/clients/views.py:81 _lock_scoped_client
backend/apps/clients/views.py:96 ClientViewSet
backend/apps/clients/views.py:657 StoreViewSet
backend/apps/clients/managers.py:8 _normalized_name
backend/apps/clients/managers.py:12 _username_candidate
backend/apps/clients/managers.py:18 ClientManager
backend/apps/clients/management/commands/provision_client_accounts.py:11 Command
backend/apps/clients/reports/statements/sections.py:32 select_sections
backend/apps/clients/reports/statements/pdf.py:47 _text
backend/apps/clients/reports/statements/pdf.py:51 _money
backend/apps/clients/reports/statements/pdf.py:55 _signed
backend/apps/clients/reports/statements/pdf.py:60 _stamp
backend/apps/clients/reports/statements/pdf.py:65 _Styles
backend/apps/clients/reports/statements/pdf.py:100 _table
backend/apps/clients/reports/statements/pdf.py:135 _cells
backend/apps/clients/reports/statements/pdf.py:142 _reconciliation_block
backend/apps/clients/reports/statements/pdf.py:191 _operation_description
backend/apps/clients/reports/statements/pdf.py:215 _ledger_rows
backend/apps/clients/reports/statements/pdf.py:275 _build
backend/apps/clients/reports/statements/pdf.py:315 _start_section
backend/apps/clients/reports/statements/pdf.py:327 _ledger_opening_block
backend/apps/clients/reports/statements/pdf.py:382 _client_summary
backend/apps/clients/reports/statements/pdf.py:423 _client_orders
backend/apps/clients/reports/statements/pdf.py:484 _client_items
backend/apps/clients/reports/statements/pdf.py:520 _client_payments
backend/apps/clients/reports/statements/pdf.py:575 _client_debts
backend/apps/clients/reports/statements/pdf.py:630 render_client_statement_pdf
backend/apps/clients/reports/statements/pdf.py:674 _all_summary
backend/apps/clients/reports/statements/pdf.py:706 _all_clients
backend/apps/clients/reports/statements/pdf.py:753 _all_orders
backend/apps/clients/reports/statements/pdf.py:810 _all_items
backend/apps/clients/reports/statements/pdf.py:846 _all_payments
backend/apps/clients/reports/statements/pdf.py:900 _all_debts
backend/apps/clients/reports/statements/pdf.py:944 render_all_clients_statement_pdf
backend/apps/clients/reports/statements/pdf.py:991 build_client_statement_pdf
backend/apps/clients/reports/statements/pdf.py:1009 build_all_clients_statement_pdf
backend/apps/clients/reports/statements/utils.py:11 statement_departments
backend/apps/clients/reports/statements/utils.py:40 statement_format
backend/apps/clients/reports/statements/utils.py:50 statement_sections
backend/apps/clients/reports/statements/xlsx.py:27 _method_label
backend/apps/clients/reports/statements/xlsx.py:58 _money
backend/apps/clients/reports/statements/xlsx.py:70 _neutralize_formula_cells
backend/apps/clients/reports/statements/xlsx.py:89 _title
backend/apps/clients/reports/statements/xlsx.py:125 _headers
backend/apps/clients/reports/statements/xlsx.py:136 _summary_block
backend/apps/clients/reports/statements/xlsx.py:180 _finish
backend/apps/clients/reports/statements/xlsx.py:221 _reconciliation
backend/apps/clients/reports/statements/xlsx.py:236 _operation_display
backend/apps/clients/reports/statements/xlsx.py:276 _workbook_bytes
backend/apps/clients/reports/statements/xlsx.py:283 build_client_statement
backend/apps/clients/reports/statements/xlsx.py:296 render_client_statement
backend/apps/clients/reports/statements/xlsx.py:498 build_all_clients_statement
backend/apps/clients/reports/statements/xlsx.py:513 render_all_clients_statement
backend/apps/clients/reports/statements/data.py:23 CurrencyTotals
backend/apps/clients/reports/statements/data.py:30 empty_currency_totals
backend/apps/clients/reports/statements/data.py:40 StatementOperation
backend/apps/clients/reports/statements/data.py:50 StatementData
backend/apps/clients/reports/statements/data.py:73 local_time
backend/apps/clients/reports/statements/data.py:77 department_name
backend/apps/clients/reports/statements/data.py:81 _payments_in_period
backend/apps/clients/reports/statements/data.py:90 _refunds_in_period
backend/apps/clients/reports/statements/data.py:99 _orders_in_period
backend/apps/clients/reports/statements/data.py:112 _statement_orders
backend/apps/clients/reports/statements/data.py:139 _statement_payments
backend/apps/clients/reports/statements/data.py:160 _statement_refunds
backend/apps/clients/reports/statements/data.py:184 _current_debt_orders
backend/apps/clients/reports/statements/data.py:195 _client_opening_balances
backend/apps/clients/reports/statements/data.py:231 _ledger_currencies
backend/apps/clients/reports/statements/data.py:238 _period_label
backend/apps/clients/reports/statements/data.py:247 _department_context
backend/apps/clients/reports/statements/data.py:256 _sale_stamp
backend/apps/clients/reports/statements/data.py:261 _payment_stamp
backend/apps/clients/reports/statements/data.py:265 _refund_stamp
backend/apps/clients/reports/statements/data.py:271 _operations
backend/apps/clients/reports/statements/data.py:323 _clients_for_statement
backend/apps/clients/reports/statements/data.py:364 build_statement_data
```

### common

```text
backend/apps/common/viewsets.py:1 SerializerViewSetMixin
backend/apps/common/apps.py:4 CommonConfig
backend/apps/common/money.py:12 money_string
backend/apps/common/money.py:18 sum_by_currency
backend/apps/common/money.py:26 as_money_strings
backend/apps/common/money.py:33 primary_currency
backend/apps/common/admin.py:4 ReadOnlyOperationalAdmin
backend/apps/common/query_params.py:10 parse_iso_date
backend/apps/common/query_params.py:22 validate_date_range
backend/apps/common/query_params.py:29 parse_date_range
backend/apps/common/query_params.py:42 filter_date_range
backend/apps/common/query_params.py:55 parse_money_param
backend/apps/common/query_params.py:80 parse_search_param
backend/apps/common/query_params.py:89 plate_search_q
backend/apps/common/query_params.py:102 parse_store_id
backend/apps/common/permissions.py:4 _auth
backend/apps/common/permissions.py:8 IsStaff
backend/apps/common/permissions.py:13 IsClientUser
backend/apps/common/permissions.py:18 IsSuperUser
backend/apps/common/permissions.py:23 DenyAll
backend/apps/common/permissions.py:28 HasPerm
backend/apps/common/permissions.py:39 HasAllPerms
backend/apps/common/permissions.py:48 PermViewSetMixin
backend/apps/common/permissions.py:64 PermAPIViewMixin
backend/apps/common/pagination.py:14 OptInPageNumberPagination
```

### employees

```text
backend/apps/employees/models.py:5 Employee
backend/apps/employees/serializers.py:17 _permission_codes_field
backend/apps/employees/serializers.py:28 _validate_permission_assignment
backend/apps/employees/serializers.py:59 _validate_user_password
backend/apps/employees/serializers.py:75 EmployeeReadSerializer
backend/apps/employees/serializers.py:109 EmployeeCreateUpdateSerializer
backend/apps/employees/serializers.py:222 EmployeeSecuritySerializer
backend/apps/employees/serializers.py:295 EmployeePasswordSerializer
backend/apps/employees/apps.py:4 EmployeesConfig
backend/apps/employees/admin.py:7 EmployeeAdminForm
backend/apps/employees/admin.py:23 EmployeeAdmin
backend/apps/employees/views.py:20 EmployeeViewSet
```

### eventlog

```text
backend/apps/eventlog/services.py:4 log_event
backend/apps/eventlog/models.py:5 EventLog
backend/apps/eventlog/serializers.py:5 EventLogSerializer
backend/apps/eventlog/apps.py:4 EventlogConfig
backend/apps/eventlog/views.py:16 EventLogPagination
backend/apps/eventlog/views.py:24 EventLogViewSet
```

### grain

```text
backend/apps/grain/vehicle_weight_capture.py:28 PassageCaptureError
backend/apps/grain/vehicle_weight_capture.py:54 _ApplyRejected
backend/apps/grain/vehicle_weight_capture.py:62 _CaptureClaim
backend/apps/grain/vehicle_weight_capture.py:67 _grain_services
backend/apps/grain/vehicle_weight_capture.py:74 _processing_lease
backend/apps/grain/vehicle_weight_capture.py:82 _capture_exception
backend/apps/grain/vehicle_weight_capture.py:98 _completed_wagon_or_raise
backend/apps/grain/vehicle_weight_capture.py:117 _transient_exception
backend/apps/grain/vehicle_weight_capture.py:134 _api_exception_parts
backend/apps/grain/vehicle_weight_capture.py:152 _idempotency_conflict
backend/apps/grain/vehicle_weight_capture.py:163 _terminalize_interrupted_claim
backend/apps/grain/vehicle_weight_capture.py:192 _classify_existing_capture
backend/apps/grain/vehicle_weight_capture.py:259 _begin_capture
backend/apps/grain/vehicle_weight_capture.py:382 _lock_wagon_then_capture
backend/apps/grain/vehicle_weight_capture.py:415 _persist_scale_reading
backend/apps/grain/vehicle_weight_capture.py:465 _safe_ai_payload
backend/apps/grain/vehicle_weight_capture.py:535 _finish_capture_error
backend/apps/grain/vehicle_weight_capture.py:570 _terminal_ai_error
backend/apps/grain/vehicle_weight_capture.py:610 _canonical_timestamp
backend/apps/grain/vehicle_weight_capture.py:617 _persist_ai_success
backend/apps/grain/vehicle_weight_capture.py:673 _apply_capture
backend/apps/grain/vehicle_weight_capture.py:755 _recognize_and_apply
backend/apps/grain/vehicle_weight_capture.py:866 capture_passage_weight_and_plate
backend/apps/grain/weighing_photos.py:22 _photo_target
backend/apps/grain/weighing_photos.py:37 attach_photo
backend/apps/grain/services.py:57 _error
backend/apps/grain/services.py:61 _log
backend/apps/grain/services.py:76 ensure_transition
backend/apps/grain/services.py:86 _set_status
backend/apps/grain/services.py:97 publish_supply
backend/apps/grain/services.py:115 prepare_simple_supply
backend/apps/grain/services.py:168 add_wagon_numbers
backend/apps/grain/services.py:193 register_arrival
backend/apps/grain/services.py:279 approve_unplanned
backend/apps/grain/services.py:299 _record_weighing
backend/apps/grain/services.py:359 _whole_scale_weight_kg
backend/apps/grain/services.py:369 _ensure_scale_action_ready
backend/apps/grain/services.py:391 record_scale_weight
backend/apps/grain/services.py:411 _read_and_store_scale_weight
backend/apps/grain/services.py:430 _store_scale_weight
backend/apps/grain/services.py:486 record_gross
backend/apps/grain/services.py:499 record_simple_entry_weight
backend/apps/grain/services.py:535 record_lab_check
backend/apps/grain/services.py:556 suggest_silos
backend/apps/grain/services.py:597 assign_silo
backend/apps/grain/services.py:657 change_silo
backend/apps/grain/services.py:697 start_unloading
backend/apps/grain/services.py:712 set_unloading_paused
backend/apps/grain/services.py:729 finish_unloading
backend/apps/grain/services.py:749 _discrepancy_percent
backend/apps/grain/services.py:759 record_tare
backend/apps/grain/services.py:790 _complete_simple_wagon
backend/apps/grain/services.py:802 record_simple_exit_weight
backend/apps/grain/services.py:842 resolve_simple_discrepancy
backend/apps/grain/services.py:875 resolve_discrepancy
backend/apps/grain/services.py:907 _apply_income
backend/apps/grain/services.py:938 inventory_wagon
backend/apps/grain/services.py:996 register_exit
backend/apps/grain/services.py:1025 adjust_silo
backend/apps/grain/services.py:1089 _vehicle_plate_time_bounds
backend/apps/grain/services.py:1097 vehicle_plate_candidates
backend/apps/grain/services.py:1117 _parse_vehicle_plate_event_id
backend/apps/grain/services.py:1127 _locked_vehicle_plate_event
backend/apps/grain/services.py:1158 normalize_passage_number
backend/apps/grain/services.py:1166 _reset_automatic_passage_lane
backend/apps/grain/services.py:1195 _lock_automatic_passage_lane
backend/apps/grain/services.py:1256 _assert_automatic_passage_lane_allows_manual_operation
backend/apps/grain/services.py:1280 _fence_automatic_passage_lane_for_manual_mutation
backend/apps/grain/services.py:1294 _prepare_manual_passage_scale_operation
backend/apps/grain/services.py:1301 create_passage
backend/apps/grain/services.py:1400 record_passage_entry_weight
backend/apps/grain/services.py:1430 record_passage_exit_weight
backend/apps/grain/services.py:1448 _finish_passage_exit
backend/apps/grain/services.py:1504 VehiclePlateAutomationResult
backend/apps/grain/services.py:1527 _AutomationClaim
backend/apps/grain/services.py:1533 _auto_event_is_fresh
backend/apps/grain/services.py:1543 _auto_processing_lease
backend/apps/grain/services.py:1548 _lock_auto_lane_mutex
backend/apps/grain/services.py:1563 _event_wagon
backend/apps/grain/services.py:1572 _terminal_automation_result
backend/apps/grain/services.py:1606 _finish_auto_event
backend/apps/grain/services.py:1631 _plate_parts
backend/apps/grain/services.py:1636 _plates_compatible
backend/apps/grain/services.py:1651 _locked_similar_passage
backend/apps/grain/services.py:1674 _locked_auto_intent
backend/apps/grain/services.py:1751 _resolve_unassigned_automatically
backend/apps/grain/services.py:1779 _locked_open_unassigned
backend/apps/grain/services.py:1785 _single_parked
backend/apps/grain/services.py:1799 _other_orientation
backend/apps/grain/services.py:1807 _locked_missed_entry_candidate
backend/apps/grain/services.py:1821 _locked_missed_exit_candidate
backend/apps/grain/services.py:1832 _close_passage_after_missed_exit
backend/apps/grain/services.py:1867 _passage_for_exit_without_entry
backend/apps/grain/services.py:1939 _is_missed_entry_for
backend/apps/grain/services.py:1953 _swap_missed_entry
backend/apps/grain/services.py:1998 _begin_vehicle_plate_automation
backend/apps/grain/services.py:2164 _safe_scale_error
backend/apps/grain/services.py:2173 _record_auto_scale_failure
backend/apps/grain/services.py:2207 _auto_scale_kwargs
backend/apps/grain/services.py:2217 _apply_vehicle_plate_automation
backend/apps/grain/services.py:2412 process_vehicle_plate_event
backend/apps/grain/services.py:2461 apply_automatic_passage_scale_sample
backend/apps/grain/services.py:2501 apply_unidentified_passage_scale_sample
backend/apps/grain/services.py:2650 _unassigned_scale_kwargs
backend/apps/grain/services.py:2662 _move_unassigned_photo
backend/apps/grain/services.py:2676 assign_unassigned_weighing
backend/apps/grain/services.py:2730 create_passage_from_unassigned_weighing
backend/apps/grain/services.py:2751 discard_unassigned_weighing
backend/apps/grain/services.py:2778 set_passage_number
backend/apps/grain/services.py:2823 _normalized_delete_reason
backend/apps/grain/services.py:2835 _is_active_deletable_wagon
backend/apps/grain/services.py:2840 delete_wagon
backend/apps/grain/services.py:3052 _open_camera_wagon
backend/apps/grain/services.py:3067 register_detected_arrival
backend/apps/grain/services.py:3146 _arrive_expected_wagon
backend/apps/grain/tasks.py:7 export_orientation_samples
backend/apps/grain/models.py:30 GrainSettings
backend/apps/grain/models.py:49 SiloType
backend/apps/grain/models.py:73 Silo
backend/apps/grain/models.py:120 GrainSupply
backend/apps/grain/models.py:164 Wagon
backend/apps/grain/models.py:302 weighing_photo_path
backend/apps/grain/models.py:306 unassigned_weighing_photo_path
backend/apps/grain/models.py:310 WeighingRecord
backend/apps/grain/models.py:344 UnassignedWeighing
backend/apps/grain/models.py:416 PassageWeightCapture
backend/apps/grain/models.py:526 AutomaticPassageCapture
backend/apps/grain/models.py:681 PassageScaleAutomationState
backend/apps/grain/models.py:749 LabCheck
backend/apps/grain/models.py:785 SiloReservation
backend/apps/grain/models.py:799 SiloAllocation
backend/apps/grain/models.py:826 GrainMovement
backend/apps/grain/models.py:880 VehicleOrientationSample
backend/apps/grain/models.py:947 VehicleOrientationDatasetState
backend/apps/grain/passage_scale_automation.py:76 MonitorIteration
backend/apps/grain/passage_scale_automation.py:85 _Work
backend/apps/grain/passage_scale_automation.py:90 _CaptureRejected
backend/apps/grain/passage_scale_automation.py:98 _processing_lease
backend/apps/grain/passage_scale_automation.py:106 _retry_delay
backend/apps/grain/passage_scale_automation.py:112 _is_empty
backend/apps/grain/passage_scale_automation.py:121 _is_occupied
backend/apps/grain/passage_scale_automation.py:130 _reset_candidate
backend/apps/grain/passage_scale_automation.py:137 _save_state
backend/apps/grain/passage_scale_automation.py:142 _discard_unobserved_candidate
backend/apps/grain/passage_scale_automation.py:162 _rearm_lane
backend/apps/grain/passage_scale_automation.py:180 _mark_interrupted_claim
backend/apps/grain/passage_scale_automation.py:217 _claim_existing_work
backend/apps/grain/passage_scale_automation.py:334 _mark_plate_unresolved
backend/apps/grain/passage_scale_automation.py:365 _claim_pending_work
backend/apps/grain/passage_scale_automation.py:400 _disarm_lane
backend/apps/grain/passage_scale_automation.py:418 _reset_idle_lane
backend/apps/grain/passage_scale_automation.py:438 prepare_monitor_start
backend/apps/grain/passage_scale_automation.py:451 _prepare_disabled_lane
backend/apps/grain/passage_scale_automation.py:544 _advance_lane
backend/apps/grain/passage_scale_automation.py:725 _reading_from_capture
backend/apps/grain/passage_scale_automation.py:743 _persist_scale_sample
backend/apps/grain/passage_scale_automation.py:797 _attempt_request_id
backend/apps/grain/passage_scale_automation.py:801 _attempt_stable_weight_at
backend/apps/grain/passage_scale_automation.py:806 _persist_next_attempt
backend/apps/grain/passage_scale_automation.py:857 _store_orientation
backend/apps/grain/passage_scale_automation.py:870 _persist_recognition
backend/apps/grain/passage_scale_automation.py:975 _finish_error
backend/apps/grain/passage_scale_automation.py:1035 _resolve_recognition_failure
backend/apps/grain/passage_scale_automation.py:1101 _finish_success
backend/apps/grain/passage_scale_automation.py:1153 _apply_recognized_capture
backend/apps/grain/passage_scale_automation.py:1251 _after_recognition_failure
backend/apps/grain/passage_scale_automation.py:1262 _recognize_capture
backend/apps/grain/passage_scale_automation.py:1339 _recognize_again
backend/apps/grain/passage_scale_automation.py:1380 _defer_next_attempt
backend/apps/grain/passage_scale_automation.py:1423 _capture_new_episode
backend/apps/grain/passage_scale_automation.py:1459 _attach_capture_photo
backend/apps/grain/passage_scale_automation.py:1470 _run_work
backend/apps/grain/passage_scale_automation.py:1485 _active_runtime_payload
backend/apps/grain/passage_scale_automation.py:1500 _public_state
backend/apps/grain/passage_scale_automation.py:1528 _store_runtime
backend/apps/grain/passage_scale_automation.py:1548 _load_runtime
backend/apps/grain/passage_scale_automation.py:1565 _publish_runtime
backend/apps/grain/passage_scale_automation.py:1584 _durable_lane
backend/apps/grain/passage_scale_automation.py:1597 _stable_weight_seconds
backend/apps/grain/passage_scale_automation.py:1605 scale_automation_settings
backend/apps/grain/passage_scale_automation.py:1617 update_scale_automation_settings
backend/apps/grain/passage_scale_automation.py:1662 _durable_runtime_fallback
backend/apps/grain/passage_scale_automation.py:1676 acknowledge_failure
backend/apps/grain/passage_scale_automation.py:1753 scale_automation_runtime
backend/apps/grain/passage_scale_automation.py:1865 monitor_once
backend/apps/grain/serializers.py:28 SiloSerializer
backend/apps/grain/serializers.py:95 SiloTypeSerializer
backend/apps/grain/serializers.py:160 WeighingRecordSerializer
backend/apps/grain/serializers.py:186 UnassignedWeighingSerializer
backend/apps/grain/serializers.py:220 VehicleOrientationSampleSerializer
backend/apps/grain/serializers.py:293 PassageNumberSerializer
backend/apps/grain/serializers.py:297 UnassignedAssignSerializer
backend/apps/grain/serializers.py:301 UnassignedCreatePassageSerializer
backend/apps/grain/serializers.py:310 UnassignedDiscardSerializer
backend/apps/grain/serializers.py:316 LabCheckSerializer
backend/apps/grain/serializers.py:338 SiloAllocationSerializer
backend/apps/grain/serializers.py:353 PassageWeightCaptureSerializer
backend/apps/grain/serializers.py:384 WagonSerializer
backend/apps/grain/serializers.py:503 WagonBriefSerializer
backend/apps/grain/serializers.py:543 AutomaticPassageScaleSettingsSerializer
backend/apps/grain/serializers.py:566 VehiclePlateCandidateSerializer
backend/apps/grain/serializers.py:585 GrainSupplySerializer
backend/apps/grain/serializers.py:657 GrainMovementSerializer
backend/apps/grain/apps.py:4 GrainConfig
backend/apps/grain/orientation_dataset.py:50 Label
backend/apps/grain/orientation_dataset.py:55 label_for_weight
backend/apps/grain/orientation_dataset.py:65 _trip_completed
backend/apps/grain/orientation_dataset.py:76 label_weighing
backend/apps/grain/orientation_dataset.py:91 label_unassigned
backend/apps/grain/orientation_dataset.py:102 _upsert
backend/apps/grain/orientation_dataset.py:160 _collect_since
backend/apps/grain/orientation_dataset.py:174 _advance_watermark
backend/apps/grain/orientation_dataset.py:184 collect
backend/apps/grain/orientation_dataset.py:242 load_records
backend/apps/grain/orientation_dataset.py:258 _photo_bytes
backend/apps/grain/orientation_dataset.py:271 set_manual_label
backend/apps/grain/orientation_dataset.py:306 exclude_sample
backend/apps/grain/orientation_dataset.py:329 export_removals
backend/apps/grain/orientation_dataset.py:361 _on_camera_pc
backend/apps/grain/orientation_dataset.py:371 _delete_rows
backend/apps/grain/orientation_dataset.py:378 purge_samples
backend/apps/grain/orientation_dataset.py:450 purge_all
backend/apps/grain/orientation_dataset.py:481 export_pending
backend/apps/grain/orientation_dataset.py:525 run
backend/apps/grain/admin.py:19 _SampleChangeList
backend/apps/grain/admin.py:32 VehicleOrientationSampleAdmin
backend/apps/grain/statuses.py:148 can_transition
backend/apps/grain/scale_preview.py:27 _preview_cache_key
backend/apps/grain/scale_preview.py:33 _preview_lock_key
backend/apps/grain/scale_preview.py:39 _redis_release_owned_lock
backend/apps/grain/scale_preview.py:59 _release_owned_lock
backend/apps/grain/scale_preview.py:67 _empty_payload
backend/apps/grain/scale_preview.py:84 _serialize
backend/apps/grain/scale_preview.py:115 _read_uncached
backend/apps/grain/scale_preview.py:126 get_scale_preview
backend/apps/grain/photos.py:27 photo_token
backend/apps/grain/photos.py:33 photo_url
backend/apps/grain/photos.py:41 _photo_from_token
backend/apps/grain/photos.py:61 WeighingPhotoView
backend/apps/grain/scale.py:69 TruckScaleDisabled
backend/apps/grain/scale.py:75 TruckScaleUnavailable
backend/apps/grain/scale.py:81 TruckScaleNotReady
backend/apps/grain/scale.py:87 TruckScaleCaptureBusy
backend/apps/grain/scale.py:93 TruckScaleApplyUnavailable
backend/apps/grain/scale.py:99 TruckScaleMalformedResponse
backend/apps/grain/scale.py:106 ScaleReading
backend/apps/grain/scale.py:113 ScaleObservation
backend/apps/grain/scale.py:130 authoritative_capture_lock_key
backend/apps/grain/scale.py:136 _capture_lock_seconds
backend/apps/grain/scale.py:146 authoritative_db_timeout_ms
backend/apps/grain/scale.py:169 configure_authoritative_db_timeouts
backend/apps/grain/scale.py:182 _claim_database_capture
backend/apps/grain/scale.py:205 _release_database_capture
backend/apps/grain/scale.py:224 _redis_release_owned_capture
backend/apps/grain/scale.py:238 _claim_capture_lock
backend/apps/grain/scale.py:249 _release_capture_lock
backend/apps/grain/scale.py:262 authoritative_capture
backend/apps/grain/scale.py:295 _api_url
backend/apps/grain/scale.py:304 enabled
backend/apps/grain/scale.py:309 _validated_api_url
backend/apps/grain/scale.py:334 _NoRedirectHandler
backend/apps/grain/scale.py:341 _open_request
backend/apps/grain/scale.py:351 _configuration_decimal
backend/apps/grain/scale.py:361 _configured_timeout
backend/apps/grain/scale.py:371 _timeout
backend/apps/grain/scale.py:375 _preview_timeout
backend/apps/grain/scale.py:379 _invalid_json_constant
backend/apps/grain/scale.py:383 _read_payload
backend/apps/grain/scale.py:429 _required_flag
backend/apps/grain/scale.py:436 _required_decimal
backend/apps/grain/scale.py:443 _optional_decimal
backend/apps/grain/scale.py:452 _normalized_preview_weight
backend/apps/grain/scale.py:466 read_truck_scale_observation
backend/apps/grain/scale.py:536 read_truck_scale
backend/apps/grain/views.py:67 TruckScaleReadingView
backend/apps/grain/views.py:85 AutomaticPassageScaleAcknowledgeView
backend/apps/grain/views.py:136 AutomaticPassageScaleRuntimeView
backend/apps/grain/views.py:147 AutomaticPassageScaleSettingsView
backend/apps/grain/views.py:189 _get_supply
backend/apps/grain/views.py:200 _require_empty_scale_command
backend/apps/grain/views.py:216 _passage_capture_idempotency_key
backend/apps/grain/views.py:244 _filter_wagon_search
backend/apps/grain/views.py:260 _record_stage_weight
backend/apps/grain/views.py:271 GrainSupplyViewSet
backend/apps/grain/views.py:337 WagonViewSet
backend/apps/grain/views.py:682 UnassignedWeighingViewSet
backend/apps/grain/views.py:781 _camera_pc_orientation
backend/apps/grain/views.py:800 VehicleOrientationSampleViewSet
backend/apps/grain/views.py:978 SiloViewSet
backend/apps/grain/views.py:1034 SiloTypeViewSet
backend/apps/grain/management/commands/monitor_passage_scale.py:24 _write_heartbeat
backend/apps/grain/management/commands/monitor_passage_scale.py:54 Command
backend/apps/grain/management/commands/purge_orientation_samples.py:22 Command
backend/apps/grain/management/commands/export_orientation_samples.py:8 Command
```

### notifications

```text
backend/apps/notifications/services.py:4 notify
backend/apps/notifications/models.py:4 Notification
backend/apps/notifications/serializers.py:5 NotificationSerializer
backend/apps/notifications/apps.py:4 NotificationsConfig
backend/apps/notifications/views.py:9 NotificationViewSet
```

### orders

```text
backend/apps/orders/apipay.py:41 ApiPayConfigurationError
backend/apps/orders/apipay.py:46 ApiPayAPIError
backend/apps/orders/apipay.py:114 _invoice_transition_allowed
backend/apps/orders/apipay.py:123 _invoice_issue_mutex
backend/apps/orders/apipay.py:155 _provider_scope_fence
backend/apps/orders/apipay.py:169 normalize_phone
backend/apps/orders/apipay.py:182 _credentials
backend/apps/orders/apipay.py:189 api_request
backend/apps/orders/apipay.py:250 _invoice_payload_from_error
backend/apps/orders/apipay.py:279 create_invoice
backend/apps/orders/apipay.py:551 _save_invoice_issue_error
backend/apps/orders/apipay.py:596 _merge_invoice_create_response
backend/apps/orders/apipay.py:650 recover_invoice_mapping_from_payload
backend/apps/orders/apipay.py:696 _money_payload_has_matching_amount
backend/apps/orders/apipay.py:710 _hydrate_money_response
backend/apps/orders/apipay.py:730 _apply_create_response_safely
backend/apps/orders/apipay.py:759 start_order_payment
backend/apps/orders/apipay.py:795 get_invoice
backend/apps/orders/apipay.py:799 _quarantine_invoice_issue_mapping
backend/apps/orders/apipay.py:838 recover_invoice_issue_mapping
backend/apps/orders/apipay.py:1048 recover_qr_invoice_mapping
backend/apps/orders/apipay.py:1059 check_invoice_statuses
backend/apps/orders/apipay.py:1065 get_invoice_refunds
backend/apps/orders/apipay.py:1081 cancel_invoice
backend/apps/orders/apipay.py:1099 _cancel_invoice_locked
backend/apps/orders/apipay.py:1167 _validated_refund_amount
backend/apps/orders/apipay.py:1191 _sync_refund_totals
backend/apps/orders/apipay.py:1200 _fail_reserved_refund
backend/apps/orders/apipay.py:1215 create_cash_refund
backend/apps/orders/apipay.py:1263 create_refund
backend/apps/orders/apipay.py:1530 _select_unlinked_local_refund
backend/apps/orders/apipay.py:1589 apply_refund_status
backend/apps/orders/apipay.py:1769 _parsed_datetime
backend/apps/orders/apipay.py:1778 _normalized_datetime
backend/apps/orders/apipay.py:1784 _invoice_payload_observed_at
backend/apps/orders/apipay.py:1807 apply_invoice_status
backend/apps/orders/labels.py:25 order_payment_method_label
backend/apps/orders/labels.py:29 payment_method_label
backend/apps/orders/labels.py:41 payment_status_label
backend/apps/orders/labels.py:45 transport_label
backend/apps/orders/services.py:21 assert_order_user_scope
backend/apps/orders/services.py:42 lock_live_order
backend/apps/orders/services.py:67 _locked_payment_order
backend/apps/orders/services.py:78 _locked_payment_with_order
backend/apps/orders/services.py:87 _sync_payment_instance
backend/apps/orders/services.py:94 _positive_money
backend/apps/orders/services.py:111 _status_message
backend/apps/orders/services.py:118 _validate_payment_open
backend/apps/orders/services.py:139 _set_payment_stage
backend/apps/orders/services.py:165 add_payment
backend/apps/orders/services.py:213 add_mixed_payments
backend/apps/orders/services.py:309 create_client_payment
backend/apps/orders/services.py:408 release_client_payment
backend/apps/orders/services.py:463 request_client_debt
backend/apps/orders/services.py:496 _advance_payment
backend/apps/orders/services.py:505 receive_payment
backend/apps/orders/services.py:514 accountant_confirm_payment
backend/apps/orders/services.py:551 record_staff_payment
backend/apps/orders/services.py:594 record_staff_mixed_payments
backend/apps/orders/services.py:616 confirm_received_staff_payments
backend/apps/orders/services.py:628 receive_and_confirm_payment
backend/apps/orders/services.py:651 reopen_confirmed_payment
backend/apps/orders/services.py:701 reject_payment
backend/apps/orders/services.py:729 restore_rejected_payment
backend/apps/orders/services.py:806 _payment_status_for
backend/apps/orders/services.py:815 sync_payment_status
backend/apps/orders/services.py:828 _apply_payment_status
backend/apps/orders/services.py:848 transition
backend/apps/orders/services.py:865 confirm_order
backend/apps/orders/services.py:885 repeat_order
backend/apps/orders/services.py:959 _apply_prices
backend/apps/orders/services.py:988 apply_item_prices
backend/apps/orders/services.py:998 correct_order_prices
backend/apps/orders/services.py:1147 _edit_item_payload
backend/apps/orders/services.py:1158 _shipped_edit_reason
backend/apps/orders/services.py:1173 _validate_payment_exposure
backend/apps/orders/services.py:1206 replace_items
backend/apps/orders/services.py:1380 reject_order
backend/apps/orders/services.py:1391 can_set_truck_number
backend/apps/orders/services.py:1405 set_truck_number
backend/apps/orders/services.py:1437 _can_edit_status
backend/apps/orders/services.py:1441 _validate_manual_status
backend/apps/orders/services.py:1457 _assert_no_open_ai_session
backend/apps/orders/services.py:1471 _has_open_ai_session
backend/apps/orders/services.py:1480 _assert_not_active_loading
backend/apps/orders/services.py:1489 set_transport_type
backend/apps/orders/services.py:1521 set_order_department
backend/apps/orders/services.py:1548 _force_set_status
backend/apps/orders/services.py:1593 request_status_change
backend/apps/orders/services.py:1626 approve_status_change
backend/apps/orders/services.py:1646 reject_status_change
backend/apps/orders/services.py:1665 soft_delete_order
backend/apps/orders/services.py:1694 restore_order
backend/apps/orders/services.py:1719 purge_order
backend/apps/orders/reconciliation_runner.py:43 _env_int
backend/apps/orders/reconciliation_runner.py:51 _request_budget_per_iteration
backend/apps/orders/reconciliation_runner.py:70 _backoff_delay
backend/apps/orders/reconciliation_runner.py:82 _write_heartbeat
backend/apps/orders/reconciliation_runner.py:97 ApiPayReconciliationOptions
backend/apps/orders/reconciliation_runner.py:213 ApiPayReconciliationResult
backend/apps/orders/reconciliation_runner.py:263 run_apipay_reconciliation_iteration
backend/apps/orders/querysets.py:29 _client_name_query
backend/apps/orders/querysets.py:38 for_post_board
backend/apps/orders/querysets.py:95 post_board_params
backend/apps/orders/querysets.py:103 with_payment_api_relations
backend/apps/orders/querysets.py:128 with_order_api_relations
backend/apps/orders/tasks.py:40 RetryableApiPayIterationError
backend/apps/orders/tasks.py:44 _task_owner
backend/apps/orders/tasks.py:51 _redis_compare_owned_lease
backend/apps/orders/tasks.py:88 _refresh_owned_lease
backend/apps/orders/tasks.py:100 _claim_lease
backend/apps/orders/tasks.py:108 _retain_lease
backend/apps/orders/tasks.py:112 _release_lease
backend/apps/orders/tasks.py:121 _seed_worker_heartbeat_if_missing
backend/apps/orders/tasks.py:133 _retry_iteration
backend/apps/orders/tasks.py:164 _run_reconciliation_task
backend/apps/orders/tasks.py:209 reconcile_apipay_task
backend/apps/orders/models.py:8 OrderQuerySet
backend/apps/orders/models.py:22 LiveOrderManager
backend/apps/orders/models.py:32 Order
backend/apps/orders/models.py:197 OrderItem
backend/apps/orders/models.py:250 Payment
backend/apps/orders/models.py:313 ApiPayInvoice
backend/apps/orders/models.py:347 ApiPayRefund
backend/apps/orders/models.py:369 PaymentRefund
backend/apps/orders/models.py:395 ApiPayWebhookEvent
backend/apps/orders/models.py:418 StatusChangeRequest
backend/apps/orders/references.py:12 build_order_form_options
backend/apps/orders/references.py:134 _product_stock_projection
backend/apps/orders/references.py:172 _product_option
backend/apps/orders/reconciliation.py:49 ReconciliationStats
backend/apps/orders/reconciliation.py:64 _response_invoices
backend/apps/orders/reconciliation.py:75 _payloads_by_id
backend/apps/orders/reconciliation.py:105 reconcile_apipay_invoices
backend/apps/orders/serializers.py:18 OrderItemSerializer
backend/apps/orders/serializers.py:81 StatusChangeRequestSerializer
backend/apps/orders/serializers.py:107 _username
backend/apps/orders/serializers.py:111 PaymentSerializer
backend/apps/orders/serializers.py:285 DepartmentLabelMixin
backend/apps/orders/serializers.py:304 PaymentQueueSerializer
backend/apps/orders/serializers.py:324 OrderSerializer
backend/apps/orders/apps.py:4 OrdersConfig
backend/apps/orders/statuses.py:31 is_financial
backend/apps/orders/statuses.py:41 is_in_progress
backend/apps/orders/statuses.py:50 public_status_key
backend/apps/orders/statuses.py:54 statuses_in_group
backend/apps/orders/statuses.py:59 public_status_label
backend/apps/orders/reports.py:43 _day_bounds
backend/apps/orders/reports.py:51 _payment_events_by_day
backend/apps/orders/reports.py:77 _refund_events_by_day
backend/apps/orders/reports.py:104 _computed_payment_status
backend/apps/orders/reports.py:113 _period_shipped_snapshots
backend/apps/orders/reports.py:155 _clients_breakdown
backend/apps/orders/reports.py:217 _debt_now
backend/apps/orders/reports.py:254 _shipping_currency_row
backend/apps/orders/reports.py:263 _income_currency_row
backend/apps/orders/reports.py:272 summary_report
backend/apps/orders/invoices.py:43 build_payment_receipt_pdf
backend/apps/orders/invoices.py:143 _escape_paragraph_text
backend/apps/orders/invoices.py:148 _plural
backend/apps/orders/invoices.py:160 _triplet_words
backend/apps/orders/invoices.py:176 amount_in_words
backend/apps/orders/invoices.py:205 _font_paths
backend/apps/orders/invoices.py:218 _register_fonts
backend/apps/orders/invoices.py:226 build_invoice_pdf
backend/apps/orders/webhooks.py:52 WebhookPayloadError
backend/apps/orders/webhooks.py:58 verify_signature
backend/apps/orders/webhooks.py:67 _event_observed_at
backend/apps/orders/webhooks.py:81 _defer_locked_event
backend/apps/orders/webhooks.py:96 _defer_event
backend/apps/orders/webhooks.py:103 _positive_int
backend/apps/orders/webhooks.py:117 _event_metadata
backend/apps/orders/webhooks.py:166 _apply_event
backend/apps/orders/webhooks.py:187 _replay_one_webhook
backend/apps/orders/webhooks.py:256 replay_pending_apipay_webhooks
backend/apps/orders/webhooks.py:315 _replay_safely
backend/apps/orders/webhooks.py:336 replay_webhooks_after_invoice_mapping
backend/apps/orders/webhooks.py:359 apipay_webhook
backend/apps/orders/refund_reconciliation.py:43 RefundReconciliationStats
backend/apps/orders/refund_reconciliation.py:54 _provider_datetime
backend/apps/orders/refund_reconciliation.py:67 _provider_id
backend/apps/orders/refund_reconciliation.py:92 _provider_amount
backend/apps/orders/refund_reconciliation.py:102 _provider_status
backend/apps/orders/refund_reconciliation.py:109 _local_sort_key
backend/apps/orders/refund_reconciliation.py:113 _provider_sort_key
backend/apps/orders/refund_reconciliation.py:121 _pair_groups
backend/apps/orders/refund_reconciliation.py:152 _correlation_matches
backend/apps/orders/refund_reconciliation.py:202 _validated_snapshot
backend/apps/orders/refund_reconciliation.py:251 _release_absent_orphans
backend/apps/orders/refund_reconciliation.py:322 _oldest_refund_candidates
backend/apps/orders/refund_reconciliation.py:333 _refund_reconciliation_candidates
backend/apps/orders/refund_reconciliation.py:408 reconcile_apipay_refunds
backend/apps/orders/debt.py:12 order_remaining
backend/apps/orders/debt.py:16 debt_orders
backend/apps/orders/debt.py:20 financial_orders
backend/apps/orders/debt.py:24 debt_by_currency
backend/apps/orders/views.py:77 _provider_error
backend/apps/orders/views.py:88 _notify_document_invoice
backend/apps/orders/views.py:103 _issue_provider_payment
backend/apps/orders/views.py:139 _provider_issue_is_unresolved
backend/apps/orders/views.py:147 _reject_created_payments
backend/apps/orders/views.py:160 _issue_mixed_provider_payments
backend/apps/orders/views.py:209 _restore_payment_and_provider
backend/apps/orders/views.py:225 _reject_payment_with_provider
backend/apps/orders/views.py:281 ReportSummaryView
backend/apps/orders/views.py:303 PaymentTransactionListView
backend/apps/orders/views.py:455 PaymentReceiptView
backend/apps/orders/views.py:478 PaymentRefundView
backend/apps/orders/views.py:544 PaymentProviderIssueView
backend/apps/orders/views.py:575 PaymentRestoreView
backend/apps/orders/views.py:595 PaymentRejectView
backend/apps/orders/views.py:634 OrderViewSet
backend/apps/orders/management/commands/sync_payment_status.py:6 Command
backend/apps/orders/management/commands/reconcile_apipay_invoices.py:24 Command
```

### portal

```text
backend/apps/portal/registration.py:16 RegisterSerializer
backend/apps/portal/registration.py:84 RegisterView
backend/apps/portal/serializers.py:17 PortalOrderItemListSerializer
backend/apps/portal/serializers.py:37 CatalogProductSerializer
backend/apps/portal/serializers.py:61 PortalOrderItemSerializer
backend/apps/portal/serializers.py:79 PortalOrderSerializer
backend/apps/portal/apps.py:4 PortalConfig
backend/apps/portal/exceptions.py:4 Conflict
backend/apps/portal/exceptions.py:9 PaymentProviderError
backend/apps/portal/views.py:35 PortalStoreViewSet
backend/apps/portal/views.py:48 PortalCatalogViewSet
backend/apps/portal/views.py:100 PortalOrderViewSet
```

### sales

```text
backend/apps/sales/models.py:4 Department
backend/apps/sales/serializers.py:9 DepartmentSerializer
backend/apps/sales/access.py:1 assigned_department_id
backend/apps/sales/access.py:8 scope_by_client_department
backend/apps/sales/apps.py:4 SalesConfig
backend/apps/sales/views.py:14 DepartmentViewSet
```

### shipments

```text
backend/apps/shipments/services.py:15 _is_loading_camera_conflict
backend/apps/shipments/services.py:21 _camera_busy_error
backend/apps/shipments/services.py:28 _locked
backend/apps/shipments/services.py:37 _device_for
backend/apps/shipments/services.py:41 _assert_device_order_camera
backend/apps/shipments/services.py:48 assert_device_camera_change
backend/apps/shipments/services.py:59 _validate_loading_camera_available
backend/apps/shipments/services.py:87 _set_loading_camera_locked
backend/apps/shipments/services.py:120 set_loading_camera
backend/apps/shipments/services.py:130 _require_shipment
backend/apps/shipments/services.py:140 _require_transport
backend/apps/shipments/services.py:148 estimated_load_kg
backend/apps/shipments/services.py:155 _lock_stock_rows
backend/apps/shipments/services.py:180 begin_camera_loading
backend/apps/shipments/services.py:242 record_arrival
backend/apps/shipments/services.py:275 record_count
backend/apps/shipments/services.py:297 _assert_no_open_ai_session
backend/apps/shipments/services.py:313 finish_loading
backend/apps/shipments/services.py:331 _valid_ai_total
backend/apps/shipments/services.py:337 finish_ai_counting
backend/apps/shipments/services.py:392 manual_complete_order
backend/apps/shipments/services.py:458 rewind_loading
backend/apps/shipments/services.py:515 rollback_shipment
backend/apps/shipments/services.py:637 _do_ship
backend/apps/shipments/services.py:693 record_shipment
backend/apps/shipments/services.py:713 start_train_loading
backend/apps/shipments/services.py:732 finish_train_loading
backend/apps/shipments/models.py:4 Shipment
backend/apps/shipments/serializers.py:5 ArrivalSerializer
backend/apps/shipments/serializers.py:11 LoadSerializer
backend/apps/shipments/serializers.py:15 ShipmentSerializer
backend/apps/shipments/apps.py:4 ShipmentsConfig
backend/apps/shipments/views.py:21 ShipmentViewSet
```

### sys_permissions

```text
backend/apps/sys_permissions/models.py:4 Permission
backend/apps/sys_permissions/migration_data.py:72 _section_codes
backend/apps/sys_permissions/serializers.py:6 PermissionSerializer
backend/apps/sys_permissions/apps.py:4 SysPermissionsConfig
backend/apps/sys_permissions/views.py:8 PermissionViewSet
```

### tasks

```text
backend/apps/tasks/signals.py:9 delete_attachment_file_after_commit
backend/apps/tasks/services.py:20 _kind_for
backend/apps/tasks/services.py:30 notify_assignee
backend/apps/tasks/services.py:39 create_task
backend/apps/tasks/services.py:72 add_attachments
backend/apps/tasks/services.py:139 _stored_file_refs
backend/apps/tasks/services.py:147 _delete_unreferenced_files
backend/apps/tasks/services.py:164 add_attachment
backend/apps/tasks/services.py:169 complete_task
backend/apps/tasks/services.py:191 reopen_task
backend/apps/tasks/services.py:209 reassign_task
backend/apps/tasks/models.py:5 Task
backend/apps/tasks/models.py:54 attachment_path
backend/apps/tasks/models.py:58 TaskAttachment
backend/apps/tasks/models.py:85 TaskNotification
backend/apps/tasks/serializers.py:12 _person
backend/apps/tasks/serializers.py:18 TaskAttachmentSerializer
backend/apps/tasks/serializers.py:34 TaskSerializer
backend/apps/tasks/serializers.py:76 TaskNotificationSerializer
backend/apps/tasks/apps.py:4 TasksConfig
backend/apps/tasks/attachments.py:7 detected_media_type
backend/apps/tasks/attachments.py:49 signed_attachment_token
backend/apps/tasks/attachments.py:57 attachment_id_from_token
backend/apps/tasks/views.py:35 TaskViewSet
backend/apps/tasks/views.py:164 TaskNotificationView
backend/apps/tasks/views.py:184 TaskAssigneeListView
backend/apps/tasks/views.py:205 TaskAttachmentDownloadView
```

### warehouse

```text
backend/apps/warehouse/services.py:20 get_default_warehouse
backend/apps/warehouse/services.py:33 get_compatibility_warehouse
backend/apps/warehouse/services.py:46 resolve_warehouse
backend/apps/warehouse/services.py:77 _product_in_other_warehouse
backend/apps/warehouse/services.py:91 _locked_stock_item
backend/apps/warehouse/services.py:143 lock_stock_item
backend/apps/warehouse/services.py:159 _apply
backend/apps/warehouse/services.py:173 ensure_products_available
backend/apps/warehouse/services.py:211 adjust_stock
backend/apps/warehouse/services.py:260 delete_stock_item
backend/apps/warehouse/services.py:281 receive_stock
backend/apps/warehouse/services.py:325 transfer_stock
backend/apps/warehouse/services.py:475 deduct_stock
backend/apps/warehouse/services.py:526 reconcile_shipment_stock
backend/apps/warehouse/models.py:7 Warehouse
backend/apps/warehouse/models.py:40 StockItem
backend/apps/warehouse/models.py:71 StockReceipt
backend/apps/warehouse/models.py:87 StockMovement
backend/apps/warehouse/serializers.py:10 _lock_warehouse_configuration
backend/apps/warehouse/serializers.py:17 WarehouseSerializer
backend/apps/warehouse/serializers.py:122 EffectiveWarehouseRepresentationMixin
backend/apps/warehouse/serializers.py:135 StockAdjustmentSerializer
backend/apps/warehouse/serializers.py:145 StockTransferSerializer
backend/apps/warehouse/serializers.py:153 StockItemSerializer
backend/apps/warehouse/serializers.py:195 StockReceiptSerializer
backend/apps/warehouse/serializers.py:225 StockMovementSerializer
backend/apps/warehouse/apps.py:6 ensure_compatibility_warehouse
backend/apps/warehouse/apps.py:43 WarehouseConfig
backend/apps/warehouse/admin.py:14 StockMovementAdmin
backend/apps/warehouse/views.py:31 WarehouseViewSet
backend/apps/warehouse/views.py:70 StockViewSet
```
