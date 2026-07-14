from rest_framework import serializers
from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    color_label = serializers.CharField(source="get_color_display", read_only=True)
    cv_class = serializers.CharField(read_only=True)
    available_bags = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "color", "color_label", "weight_kg",
                  "price", "is_active", "ask_truck_weight",
                  "label", "cv_class", "available_bags"]

    def get_available_bags(self, obj):
        # Остаток склада: заказ доступен только по товару в наличии.
        stock = getattr(obj, "stock", None)
        return stock.bags if stock else 0
