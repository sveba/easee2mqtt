import os
import sys
import types
import unittest

os.environ.setdefault("EASEE_CHARGERS", "EH123")
sys.modules.setdefault("requests", types.SimpleNamespace(request=None, post=None))
sys.modules.setdefault("requests.api", types.SimpleNamespace(request=None))
sys.modules.setdefault("paho", types.SimpleNamespace(mqtt=types.SimpleNamespace(client=None)))
sys.modules.setdefault("paho.mqtt", types.SimpleNamespace(client=None))
sys.modules.setdefault("paho.mqtt.client", types.SimpleNamespace())

import easee2mqtt
from easee2mqtt import find_serial_number, format_latest_pulse, observations_to_state


class ObservationStateTest(unittest.TestCase):
    def test_observations_are_mapped_to_old_state_keys(self):
        state = observations_to_state([
            {"id": 48, "value": "32.0", "timestamp": "2026-09-15T10:00:00Z"},
            {"id": 102, "value": "true", "timestamp": "2026-09-15T10:00:01Z"},
            {"id": 103, "value": "false", "timestamp": "2026-09-15T10:00:02Z"},
            {"id": 109, "value": "3", "timestamp": "2026-09-15T10:00:03Z"},
            {"id": 120, "value": "7.4", "timestamp": "2026-09-15T10:00:04Z"},
            {"id": 121, "value": "2.5", "timestamp": "2026-09-15T10:00:05Z"},
            {"id": 124, "value": "100.25", "timestamp": "2026-09-15T10:00:06Z"},
            {"id": 202, "value": "230.5", "timestamp": "2026-09-15T10:00:07Z"},
        ])

        self.assertEqual(state["dynamicChargerCurrent"], 32.0)
        self.assertEqual(state["smartCharging"], True)
        self.assertEqual(state["cableLocked"], False)
        self.assertEqual(state["chargerOpMode"], 3)
        self.assertEqual(state["totalPower"], 7.4)
        self.assertEqual(state["sessionEnergy"], 2.5)
        self.assertEqual(state["lifetimeEnergy"], 100.25)
        self.assertEqual(state["voltage"], 230.5)
        self.assertEqual(state["latestPulse"], "2026-09-15T10:00:07Z")

    def test_latest_pulse_accepts_observation_timestamp_format(self):
        self.assertTrue(format_latest_pulse("2026-09-15T10:00:07Z"))

    def test_get_state_uses_serial_number_for_observations(self):
        calls = []

        class Response:
            status_code = 200

            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        def fake_request(method, url, headers=None, params=None):
            calls.append(url)
            if url.endswith("/details"):
                return Response({"serialNumber": "EH123456"})
            return Response([])

        old_requests = easee2mqtt.requests
        easee2mqtt.access_token = "token"
        easee2mqtt.token_expiration = 9999999999
        easee2mqtt.charger_serial_numbers = {}
        easee2mqtt.requests = types.SimpleNamespace(request=fake_request)
        try:
            easee2mqtt.get_state("charger-id")
        finally:
            easee2mqtt.requests = old_requests

        self.assertIn("https://api.easee.com/api/chargers/charger-id/details", calls)
        self.assertIn("https://api.easee.com/state/EH123456/observations", calls)

    def test_find_serial_number_accepts_nested_details(self):
        self.assertEqual(find_serial_number({"charger": {"serialNo": "EH654321"}}), "EH654321")

    def test_mqtt_topic_supports_root_with_slash(self):
        old_root = easee2mqtt.mqtt_root_topic
        easee2mqtt.mqtt_root_topic = "easee2mqtt/vila"
        try:
            topic = easee2mqtt.mqtt_topic("cable_lock/set")
            self.assertEqual(topic, "easee2mqtt/vila/cable_lock/set")
            self.assertEqual(easee2mqtt.parse_mqtt_topic(topic), "cable_lock")
        finally:
            easee2mqtt.mqtt_root_topic = old_root


if __name__ == "__main__":
    unittest.main()
