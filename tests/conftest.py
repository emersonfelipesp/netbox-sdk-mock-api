import os
from pathlib import Path

OPENAPI_PATH = (
    Path(__file__).resolve().parent.parent
    / "netbox_sdk"
    / "reference"
    / "openapi"
    / "netbox-openapi.json"
)


# Ordinary regression tests never export to operator-configured destinations.
os.environ["OTEL_SDK_DISABLED"] = "true"
