import logging

import pytest
from django.test import override_settings

from overbae.core.telemetry.analytics import capture


@pytest.mark.parametrize("testing", [True, False])
def test_invalid_analytics_properties_never_expose_values(testing, caplog):
    secret = "sensitive-credential-marker"
    with override_settings(TESTING=testing), caplog.at_level(logging.WARNING):
        if testing:
            with pytest.raises(TypeError) as error:
                capture(secret, None, payload={"password": secret})
            assert secret not in str(error.value)
        else:
            capture(secret, None, payload={"password": secret})
            assert "non-scalar properties" in caplog.text
    assert secret not in caplog.text
