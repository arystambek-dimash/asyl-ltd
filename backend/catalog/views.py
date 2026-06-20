from rest_framework import viewsets
from accounts.permissions import IsStaff, IsManager
from .models import Grade, Packaging, Product
from .serializers import GradeSerializer, PackagingSerializer, ProductSerializer


class _StaffReadManagerWrite(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [IsManager()]


class GradeViewSet(_StaffReadManagerWrite):
    queryset = Grade.objects.all()
    serializer_class = GradeSerializer


class PackagingViewSet(_StaffReadManagerWrite):
    queryset = Packaging.objects.all()
    serializer_class = PackagingSerializer


class ProductViewSet(_StaffReadManagerWrite):
    queryset = Product.objects.select_related("grade", "packaging").all()
    serializer_class = ProductSerializer
