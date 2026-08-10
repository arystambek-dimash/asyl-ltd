import os

DEBUG = os.environ.get("DEBUG", "1") == "1"

if DEBUG:
    from ._settings.local import *
else:
    from ._settings.production import *
