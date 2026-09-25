import pytest


@pytest.fixture
def identity_ai(settings, tmp_path):
    """Сверка номера по снимку через ИИ включена; снимки — во временном MEDIA_ROOT."""
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "test-key"
    settings.WEIGHING_AI_ENABLED = True
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 100
    return settings


@pytest.fixture
def orientation_dataset(settings, tmp_path):
    """Датасет ориентации включён; пороги веса — те, на которые рассчитаны тесты."""
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_ORIENTATION_DATASET_ENABLED = True
    settings.VEHICLE_ORIENTATION_EMPTY_MAX_KG = 5000
    settings.VEHICLE_ORIENTATION_LOADED_MIN_KG = 6000
    settings.VEHICLE_ORIENTATION_SAMPLE_MAX_AGE_DAYS = 60
    return settings
