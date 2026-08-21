import json
import time
import paho.mqtt.client as mqtt

class MQTTPublisher:
    def __init__(self, broker="localhost", port=1883, topic="ppe/alerts", client_id=None):
        self.topic  = topic
        # Unique per instance by default -- a fixed shared client_id
        # across multiple running Pro-tier apps (PPE + Driver + Healthcare
        # each connecting as "ppe_system") causes the broker to evict
        # whichever connected first every time another instance with the
        # same ID connects, producing an endless connect/disconnect flap.
        # Same bug already fixed once in the Lite tier; this is the
        # Pro tier'''s own separate copy of the file, so it needed the
        # same fix applied here too.
        if client_id is None:
            import uuid
            client_id = f"edge_ai_pro_{uuid.uuid4().hex[:8]}"
        self.client = mqtt.Client(client_id=client_id)
        self.connected = False

        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            time.sleep(0.5)  # give it a moment to connect
        except Exception as e:
            print(f"[MQTT] Could not connect to broker at {broker}:{port} — {e}")
            print("[MQTT] Alerts will be logged locally only")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print("[MQTT] Connected to broker")
        else:
            print(f"[MQTT] Connection failed with code {rc}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        print("[MQTT] Disconnected from broker")

    def publish(self, alert: dict, topic: str = None):
        # topic overrides self.topic for this call only -- lets a
        # single shared MQTTPublisher (one per process) still publish
        # under the correct per-solution topic even after a camera
        # slot has been live-swapped to a different solution via
        # ModelManager (see StreamManager.swap_solution), since
        # self.topic alone would otherwise stay frozen at whatever the
        # process booted with.
        publish_topic = topic or self.topic
        payload = json.dumps({
            "person_id":      alert["person_id"],
            "violation_type": alert["violation_type"],
            "timestamp":      alert["timestamp"],
            "bbox":           alert["bbox"]
        })
        if self.connected:
            result = self.client.publish(publish_topic, payload, qos=1)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[MQTT] Publish failed: {result.rc}")
        else:
            # fallback — just log it
            print(f"[MQTT] (offline) Alert: {payload}")

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
