class SerializerViewSetMixin:
    """Select a serializer by DRF action, falling back to serializer_class."""

    serializer_action_classes = {}

    def get_serializer_class(self):
        action = getattr(self, "action", None)
        serializer_class = self.serializer_action_classes.get(action)
        if serializer_class is not None:
            return serializer_class
        return super().get_serializer_class()


class NoStoreMixin:
    """Ни один ответ вью, включая 403/405 и ошибки, не кэшируется."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
