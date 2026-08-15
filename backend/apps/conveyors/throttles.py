from rest_framework.throttling import SimpleRateThrottle


class ConveyorDeviceRateThrottle(SimpleRateThrottle):
    scope = "conveyor_device"

    def get_cache_key(self, request, view):
        device = getattr(request, "auth", None)
        public_id = getattr(device, "public_id", None)
        if public_id is None or self.rate is None:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": str(public_id),
        }


class ConveyorAiRateThrottle(SimpleRateThrottle):
    scope = "conveyor_ai"

    def get_cache_key(self, request, view):
        if self.rate is None:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }
