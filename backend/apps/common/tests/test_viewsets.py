from rest_framework import serializers, viewsets

from apps.common.viewsets import SerializerViewSetMixin


class DefaultSerializer(serializers.Serializer):
    pass


class CreateSerializer(serializers.Serializer):
    pass


class ExampleViewSet(SerializerViewSetMixin, viewsets.GenericViewSet):
    serializer_class = DefaultSerializer
    serializer_action_classes = {"create": CreateSerializer}


class MappedOnlyViewSet(SerializerViewSetMixin, viewsets.GenericViewSet):
    serializer_action_classes = {"create": CreateSerializer}


def test_serializer_viewset_mixin_selects_action_serializer():
    view = ExampleViewSet()
    view.action = "create"

    assert view.get_serializer_class() is CreateSerializer


def test_serializer_viewset_mixin_falls_back_to_default_serializer():
    view = ExampleViewSet()
    view.action = "list"

    assert view.get_serializer_class() is DefaultSerializer


def test_mapped_action_does_not_require_default_serializer():
    view = MappedOnlyViewSet()
    view.action = "create"

    assert view.get_serializer_class() is CreateSerializer


def test_serializer_viewset_mixin_works_before_action_is_set():
    view = ExampleViewSet()

    assert view.get_serializer_class() is DefaultSerializer
