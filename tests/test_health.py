from rest_framework.test import APIClient


def test_health_check_pings_the_database(db):
    response = APIClient().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
