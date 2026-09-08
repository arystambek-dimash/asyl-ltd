# Orders interface restoration

Restores the orders list, status filter, expanded analytics, detail layout and
sidebar to the interface before the review-queue redesign. Server pagination,
query optimizations, payment cancellation and mandatory department selection at
confirmation remain available. Selecting confirmation in the list opens the
order confirmation form instead of bypassing it through a status update.

An empty department is displayed as «Нет отдела» in the list, detail and cashier,
including when an outdated name accompanies an empty department code. The
department column remains visible when any displayed order is unassigned.

Migration 0036 clears the legacy default only for pending portal-shaped requests
with no assigned client department, no creator, no repeated-order source, no
payment and no status/edit/confirmation/shipment event. It records the previous
value in EventLog. Staff-created, edited and confirmed orders keep their existing
department; changing a client's assignment does not rewrite their order history.
The data cleanup is idempotent; rollback does not reinstall the incorrect default.

Validation: 59 targeted backend tests, frontend lint/typecheck and 578 tests,
plus regression tests for the restored list and cashier labels. Local browser
verification used synthetic API data and checked the list, detail and required
department selection; it does not assert production data contents.
