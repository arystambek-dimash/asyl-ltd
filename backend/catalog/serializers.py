from rest_framework import serializers
from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    color_label = serializers.CharField(source="get_color_display", read_only=True)
    cv_class = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "color", "color_label", "weight_kg",
                  "price", "is_active", "label", "cv_class"]
