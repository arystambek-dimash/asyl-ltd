from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras import analytics
from apps.cameras.models import AlwaysOnDailyAnalytics, MonoblockCameraSettings, ShippingDailyAnalytics

pytestmark = pytest.mark.django_db


def test_range_covers_old_days_zero_fills_and_keeps_today_and_lifetime_separate():
    today = timezone.localdate()
    start = today - timedelta(days=60)
    MonoblockCameraSettings.objects.create(always_on_camera_sources=['cam3'])
    for day, total, color in [(start-timedelta(days=1), 90, 'Blue_50'), (start, 10, 'Red_50'), (today, 5, 'Blue_50')]:
        AlwaysOnDailyAnalytics.objects.create(camera='cam3', day=day, model_total=total, model_per_color={color: total})
    AlwaysOnDailyAnalytics.objects.create(camera='cam3', day=start+timedelta(days=1), model_total=100, archived_at=timezone.now())
    result = analytics.today_payload(date_from=start, date_to=start+timedelta(days=2))
    camera = result['cameras'][0]
    assert camera['period_total'] == 10
    assert camera['total'] == 5
    assert camera['all_time_total'] == 105
    assert [row['total'] for row in camera['history']] == [10, 0, 0]
    assert camera['colors'] == [{'color':'Red_50', 'total':10, 'percent':100.0}]
    assert result['period_total'] == 10
    assert result['model_all_time_total'] == 105
    assert camera['date_from'] == start.isoformat()


@pytest.mark.parametrize('query', ['date_from=oops', 'date_from=2026-09-08&date_to=2026-09-07', 'date_from=2020-01-01&date_to=2026-01-01', 'camera=../cam3'])
def test_range_endpoint_rejects_invalid_input(auth_client, user_with_perms, query):
    user = user_with_perms('analytics-range-reader', codes=['shipping.load'])
    for endpoint in ['always-on-analytics', 'shipping-continuous-analytics']:
        response = auth_client(user).get(f'/api/cameras/{endpoint}/?{query}')
        assert response.status_code == 400


def test_range_api_preserves_contour_isolation(auth_client, user_with_perms):
    today = timezone.localdate()
    MonoblockCameraSettings.objects.create(camera_sources=['cam2'], always_on_camera_sources=['cam3'])
    AlwaysOnDailyAnalytics.objects.create(camera='cam3', day=today, model_total=20)
    ShippingDailyAnalytics.objects.create(camera='cam2', day=today, model_total=7)
    user = user_with_perms('analytics-range-reader', codes=['shipping.load'])
    client = auth_client(user)
    query = f'?date_from={today}&date_to={today}'
    response = client.get('/api/cameras/shipping-continuous-analytics/'+query)
    assert response.data['period_total'] == 7
    assert [row['camera'] for row in response.data['cameras']] == ['cam2']
    hidden = client.get('/api/cameras/always-on-analytics/'+query+'&camera=cam2')
    assert hidden.data['cameras'] == []
    assert hidden.data['period_total'] == 0
