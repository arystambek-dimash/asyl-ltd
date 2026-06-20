from rest_framework import serializers
from .models import Grade, Packaging, Product


class GradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grade
        fields = ["id", "name", "is_active"]


class PackagingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Packaging
        fields = ["id", "name", "weight_kg", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Product
        fields = ["id", "grade", "packaging", "price", "is_active", "label", "weight_kg"]
