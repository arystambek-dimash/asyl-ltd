from decimal import Decimal
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from apps.clients.models import Client
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem
from apps.tasks.models import Task, TaskNotification
from apps.grain.models import Silo, GrainSupply, Wagon, VehicleOrientationSample
from apps.grain import services, orientation_dataset

pytestmark = pytest.mark.django_db

@pytest.fixture
def actor():
    return get_user_model().objects.create_superuser('audit_admin', password='testing-password')

@pytest.fixture
def http(actor):
    client = APIClient()
    client.force_authenticate(actor)
    return client

def test_portal_mutation_response_is_current():
    user = get_user_model().objects.create_user('audit_portal', is_client=True)
    client = Client.objects.create(user=user, phone='87000000000')
    order = Order.objects.create(client=client, status='shipped', settlement_intent='pending', payment_method='pending')
    OrderItem.objects.create(order=order, quantity=1, unit_price=Decimal('100'))
    api = APIClient()
    api.force_authenticate(user)
    response = api.post(f'/api/portal/orders/{order.pk}/pay/', {'method': 'debt'}, format='json')
    order.refresh_from_db()
    assert response.status_code == 201
    assert response.data['payment_method'] == order.payment_method

def test_task_patch_cannot_assign_to_client(http, actor):
    client_user = get_user_model().objects.create_user('task_client', is_client=True)
    task = Task.objects.create(title='Check', assignee=actor, created_by=actor)
    response = http.patch(f'/api/tasks/{task.pk}/', {'assignee': client_user.pk}, format='json')
    assert response.status_code == 400

def test_task_patch_notifies_new_assignee(http, actor):
    from apps.employees.models import Employee
    worker = get_user_model().objects.create_user('task_worker')
    Employee.objects.create(user=worker)
    task = Task.objects.create(title='Check', assignee=actor, created_by=actor)
    response = http.patch(f'/api/tasks/{task.pk}/', {'assignee': worker.pk}, format='json')
    assert response.status_code == 200
    assert TaskNotification.objects.filter(task=task, user=worker).exists()

def test_stale_unloading_cannot_restart_finished_wagon(actor):
    wagon = Wagon.objects.create(number='11223344', status='silo_assigned')
    stale = Wagon.objects.get(pk=wagon.pk)
    services.start_unloading(wagon, actor)
    services.finish_unloading(wagon, actor)
    with pytest.raises(ValidationError):
        services.start_unloading(stale, actor)

def test_silo_assignment_rejects_wrong_culture(actor):
    supply = GrainSupply.objects.create(supplier='Supply', culture='wheat', expected_total_kg=100)
    wagon = Wagon.objects.create(supply=supply, number='22334455', status='unloading_allowed', expected_weight_kg=100)
    silo = Silo.objects.create(name='Barley', grain_culture='barley', total_capacity_kg=1000)
    with pytest.raises(ValidationError):
        services.assign_silo(wagon, silo, actor)

def test_inventory_does_not_truncate_fractional_kilograms(http):
    silo = Silo.objects.create(name='Inventory', total_capacity_kg=1000)
    wagon = Wagon.objects.create(number='33445566', status='tare_weighed', net_weight_kg=100, assigned_silo=silo)
    response = http.post(f'/api/grain/wagons/{wagon.pk}/inventory/', {'allocations': [{'silo_id': silo.pk, 'amount_kg':100.9}]}, format='json')
    assert response.status_code == 400

def test_silo_list_has_bounded_queries(http):
    Silo.objects.create(name='One', total_capacity_kg=1000, sensor_estimated_kg=0)
    with CaptureQueriesContext(connection) as one:
        assert http.get('/api/grain/silos/').status_code == 200
    for i in range(4):
        Silo.objects.create(name=f'More {i}', total_capacity_kg=1000, sensor_estimated_kg=0)
    with CaptureQueriesContext(connection) as many:
        assert http.get('/api/grain/silos/').status_code == 200
    assert len(many) <= len(one) + 2, (len(one), len(many))

def test_orientation_edit_during_export_is_not_marked_delivered(actor):
    sample = VehicleOrientationSample.objects.create(record_kind='weighing', record_id=99, label='front', label_source='weight', weight_kg=1000, captured_at=timezone.now())
    def relabel(**kwargs):
        current = VehicleOrientationSample.objects.get(pk=sample.pk)
        orientation_dataset.set_manual_label(current, 'rear', actor)
    with patch.object(orientation_dataset, '_photo_bytes', return_value=b'jpeg'), patch.object(orientation_dataset.camera_ai, 'post_orientation_sample', side_effect=relabel):
        orientation_dataset.export_pending(limit=10)
    sample.refresh_from_db()
    assert sample.label == 'rear'
    assert sample.sent_at is None

def test_staff_order_transport_is_validated(http, actor):
    from apps.warehouse.models import StockItem, Warehouse
    client_user = get_user_model().objects.create_user('order_client', is_client=True)
    client = Client.objects.create(user=client_user, phone='1')
    product = Product.objects.create(name='Flour', color='Red', weight_kg=50)
    StockItem.objects.create(product=product, warehouse=Warehouse.objects.get(code='main'), bags=100)
    response = http.post('/api/orders/', {'client': client.pk, 'transport_type':'plane', 'items':[{'product': product.pk, 'quantity':1}]}, format='json')
    assert response.status_code == 400


def test_positive_stock_adjustment_can_partially_restore_overdrawn_stock(actor):
    from apps.warehouse.models import StockItem, Warehouse
    from apps.warehouse.services import adjust_stock
    product = Product.objects.create(name='Flour', color='Red', weight_kg=50)
    warehouse = Warehouse.objects.get(code='main')
    stock = StockItem.objects.create(product=product, warehouse=warehouse, bags=-100)
    adjust_stock(product, 10, actor, warehouse=warehouse)
    stock.refresh_from_db()
    assert stock.bags == -90
    with pytest.raises(ValidationError):
        adjust_stock(product, -1, actor, warehouse=warehouse)


def test_duplicate_wagon_batch_is_fully_rolled_back(actor):
    supply = GrainSupply.objects.create(supplier='Supply', culture='wheat')
    with pytest.raises(ValidationError):
        services.add_wagon_numbers(supply, ['112233', '112233'], actor)
    assert not supply.wagons.exists()


def test_change_silo_cannot_escape_quarantine(actor):
    from apps.grain.models import LabCheck, SiloReservation
    quarantine = Silo.objects.create(name='Quarantine', total_capacity_kg=100, is_quarantine=True)
    ordinary = Silo.objects.create(name='Ordinary', total_capacity_kg=100)
    wagon = Wagon.objects.create(number='445566', status='silo_assigned', assigned_silo=quarantine)
    LabCheck.objects.create(wagon=wagon, decision='quarantine', checked_by=actor)
    SiloReservation.objects.create(wagon=wagon, silo=quarantine, amount_kg=100)
    with pytest.raises(ValidationError):
        services.change_silo(wagon, ordinary, 'move', actor)
    # A repeated selection must not count the wagon's own reservation twice.
    services.change_silo(wagon, quarantine, 'same destination', actor)


def test_old_assignee_cannot_complete_after_reassignment(actor):
    from apps.employees.models import Employee
    from apps.tasks.services import complete_task, reassign_task
    from rest_framework.exceptions import PermissionDenied
    former = get_user_model().objects.create_user('former')
    replacement = get_user_model().objects.create_user('replacement')
    Employee.objects.create(user=former)
    Employee.objects.create(user=replacement)
    stale = Task.objects.create(title='Work', assignee=former, created_by=actor)
    reassign_task(stale, replacement, actor)
    with pytest.raises(PermissionDenied):
        complete_task(stale, former)


def test_editing_stale_task_preserves_concurrent_completion(actor):
    from apps.tasks.services import complete_task, update_task
    stale = Task.objects.create(title='Old', assignee=actor, created_by=actor)
    complete_task(stale, actor)
    result = update_task(stale, {'title': 'New'}, actor)
    assert result.title == 'New'
    assert result.status == Task.DONE
    assert result.done_at is not None


def test_exclusion_during_first_export_queues_remote_removal(actor):
    sample = VehicleOrientationSample.objects.create(
        record_kind='weighing', record_id=100, label='front', label_source='weight',
        weight_kg=1000, captured_at=timezone.now(),
    )
    def exclude(**kwargs):
        orientation_dataset.exclude_sample(sample, actor)
    with patch.object(orientation_dataset, '_photo_bytes', return_value=b'jpeg'), patch.object(
        orientation_dataset.camera_ai, 'post_orientation_sample', side_effect=exclude,
    ):
        orientation_dataset.export_pending(limit=10)
    sample.refresh_from_db()
    assert sample.excluded
    assert sample.removal_pending
    assert sample.sent_at is None


def test_unknown_export_outcome_keeps_remote_removal_obligation(actor):
    sample = VehicleOrientationSample.objects.create(
        record_kind='weighing', record_id=101, label='front', label_source='weight',
        weight_kg=1000, captured_at=timezone.now(),
    )
    with patch.object(orientation_dataset, '_photo_bytes', return_value=b'jpeg'), patch.object(
        orientation_dataset.camera_ai, 'post_orientation_sample',
        side_effect=orientation_dataset.camera_ai.AiUnavailable('timeout'),
    ):
        orientation_dataset.export_pending(limit=10)
    orientation_dataset.set_manual_label(sample, 'rear', actor)
    orientation_dataset.exclude_sample(sample, actor)
    assert sample.delivered_at is None
    assert sample.removal_pending


def test_dataset_purge_stops_starting_calls_at_deadline():
    for record_id in [102, 103]:
        VehicleOrientationSample.objects.create(
            record_kind='weighing', record_id=record_id, label='front',
            label_source='weight', weight_kg=1000, captured_at=timezone.now(),
            delivered_at=timezone.now(),
        )
    with patch.object(orientation_dataset.time, 'monotonic', side_effect=[0, 1, 10]), patch.object(
        orientation_dataset.camera_ai, 'delete_orientation_sample',
    ) as remove:
        result = orientation_dataset.purge_samples(VehicleOrientationSample.objects.all())
    assert remove.call_count == 1
    assert result['deleted'] == 1
    assert result['remaining'] == 1


@pytest.mark.django_db(transaction=True)
def test_dataset_purge_cannot_race_export():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from django.db import connections
    entered, release = Event(), Event()
    @orientation_dataset._serialized_dataset_operation
    def hold_export():
        entered.set()
        assert release.wait(timeout=10)
    def worker():
        try:
            hold_export()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(orientation_dataset.OrientationSyncBusy):
                orientation_dataset.purge_all(remove_from_pc=False)
        finally:
            release.set()
        future.result(timeout=10)


@pytest.mark.parametrize('body', [
    {'items': []}, {'prices': []}, {'prices': '100'},
])
def test_invalid_order_shape_is_rejected_without_writes(http, body):
    from apps.warehouse.models import StockItem, Warehouse
    client_user = get_user_model().objects.create_user('shape_client', is_client=True)
    client = Client.objects.create(user=client_user, phone='1')
    product = Product.objects.create(name='Flour', color='Red', weight_kg=50)
    StockItem.objects.create(product=product, warehouse=Warehouse.objects.get(code='main'), bags=100)
    payload = {'client': client.pk, 'items': [{'product': product.pk, 'quantity': 1}], **body}
    response = http.post('/api/orders/', payload, format='json')
    assert response.status_code == 400
    assert not Order.objects.exists()


def test_order_total_can_exceed_a_single_unit_price_field(http):
    client_user = get_user_model().objects.create_user('large_total_client', is_client=True)
    client = Client.objects.create(user=client_user, phone='1')
    order = Order.objects.create(client=client, status='shipped')
    OrderItem.objects.create(order=order, quantity=3, unit_price=Decimal('9999999999.99'))
    response = http.get(f'/api/orders/{order.pk}/')
    assert response.status_code == 200
    assert response.data['total_amount'] == '29999999999.97'


def test_new_order_cannot_add_an_archived_product_with_stock(http):
    from apps.warehouse.models import StockItem, Warehouse
    user = get_user_model().objects.create_user('archived_product_client', is_client=True)
    client = Client.objects.create(user=user, phone='1')
    product = Product.objects.create(name='Archived', color='Red', weight_kg=50, is_active=False)
    StockItem.objects.create(product=product, warehouse=Warehouse.objects.get(code='main'), bags=100)
    response = http.post('/api/orders/', {
        'client': client.pk, 'items': [{'product': product.pk, 'quantity': 1}],
    }, format='json')
    assert response.status_code == 400
    assert not Order.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_exits_close_the_supply(actor):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections
    supply = GrainSupply.objects.create(supplier='Parallel', culture='wheat', status='expected')
    wagons = [Wagon.objects.create(supply=supply, number=str(9000+i), status='exit_allowed') for i in range(2)]
    barrier = Barrier(2)
    def finish(wagon):
        try:
            barrier.wait(timeout=10)
            services.register_exit(wagon, actor)
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish, wagon) for wagon in wagons]
        for future in futures:
            future.result(timeout=15)
    supply.refresh_from_db()
    assert supply.status == 'closed'


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_unloading_starts_have_one_winner(actor):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections, connections
    wagon = Wagon.objects.create(number='778899', status='silo_assigned')
    barrier = Barrier(2)
    def start():
        close_old_connections()
        try:
            stale = Wagon.objects.get(pk=wagon.pk)
            barrier.wait(timeout=10)
            try:
                services.start_unloading(stale, actor)
                return 'started'
            except ValidationError:
                return 'rejected'
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(start), pool.submit(start)
        assert sorted([first.result(timeout=15), second.result(timeout=15)]) == ['rejected', 'started']
