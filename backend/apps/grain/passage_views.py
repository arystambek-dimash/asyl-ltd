"""Outbound trips: an isolated resource over the existing trip ledger.

The legacy /wagons API remains compatible. This resource never resolves an
intake ID and does not expose laboratory, silo or grain receipt commands.
"""

from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from . import services
from .models import Wagon
from .serializers import PassageNumberSerializer, VehiclePlateCandidateSerializer
from .views import GrainTripViewSet, _record_stage_weight, _require_empty_scale_command


class PassageCreateSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=30, allow_blank=True, default="")
    cargo_name = serializers.CharField(max_length=100)
    note = serializers.CharField(allow_blank=True, default="")
    vehicle_plate_event_id = serializers.UUIDField(required=False, allow_null=True)


class PassageViewSet(mixins.CreateModelMixin, GrainTripViewSet):
    queryset = GrainTripViewSet.queryset.filter(direction=Wagon.PASSAGE)
    required_perms = {
        **GrainTripViewSet.required_perms,
        "create": "grain.arrive",
        "set_number": "grain.arrive",
        "vehicle_plate_candidates": "grain.arrive",
        "entry_weight": "grain.weigh",
        "exit_weight": "grain.weigh",
    }

    def create(self, request, *args, **kwargs):
        serializer = PassageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wagon = services.create_passage(request.user, **serializer.validated_data)
        return Response(self.get_serializer(wagon).data, status=201)

    @action(detail=True, methods=["patch", "post"], url_path="number")
    def set_number(self, request, pk=None):
        serializer = PassageNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(services.set_passage_number(
            self.get_object(), serializer.validated_data["number"], request.user,
        ))

    @action(detail=False, methods=["get"], url_path="vehicle-plate-candidates")
    def vehicle_plate_candidates(self, request):
        response = Response(VehiclePlateCandidateSerializer(
            services.vehicle_plate_candidates(), many=True,
        ).data)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["post"], url_path="entry-weight")
    def entry_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        return self._done(_record_stage_weight(request, self.get_object(), "entry"))

    @action(detail=True, methods=["post"], url_path="exit-weight")
    def exit_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        return self._done(_record_stage_weight(request, self.get_object(), "exit"))
