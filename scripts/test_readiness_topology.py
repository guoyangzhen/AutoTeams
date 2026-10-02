"""Prevent the API/worker readiness cycle on an empty Compose deployment."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKERS = ("compilation-worker", "agent-build-worker", "processing-worker")


class ReadinessTopologyTests(unittest.TestCase):
    def test_workers_can_start_before_api_is_ready(self):
        for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
            services = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))["services"]
            api = services["backend"]
            self.assertIn("WORKERS_ENABLED=true", api["environment"])
            api_data = {v for v in api["volumes"] if v.endswith(":/app/data")}
            self.assertTrue(api_data)
            for name in WORKERS:
                with self.subTest(file=filename, worker=name):
                    worker = services[name]
                    self.assertEqual(worker["depends_on"]["backend"]["condition"], "service_started")
                    self.assertTrue(api_data.intersection(worker["volumes"]))
                    self.assertIn("WORKER_HEARTBEAT_DIR=/app/data", worker["environment"])
                    self.assertIn("scripts.worker_healthcheck", worker["healthcheck"]["test"])


    def test_security_state_has_an_independent_persistent_noeviction_store(self):
        for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
            document = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
            services = document["services"]
            security = services["redis-security"]
            self.assertIn("--maxmemory-policy noeviction", security["command"])
            self.assertIn("--appendonly yes", security["command"])
            self.assertIn("--appendfsync always", security["command"])
            self.assertIn("redis_security_data:/data", security["volumes"])
            self.assertIn("redis_security_data", document["volumes"])
            self.assertNotIn("ports", security)
            for name in ("backend", *WORKERS):
                with self.subTest(file=filename, service=name):
                    self.assertEqual(services[name]["depends_on"]["redis-security"]["condition"], "service_healthy")
                    urls = [v for v in services[name]["environment"] if v.startswith("TOKEN_BLACKLIST_REDIS_URL=")]
                    self.assertEqual(len(urls), 1)
                    self.assertIn("@redis-security:6379/", urls[0])


    def test_service_network_references_exist(self):
        for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
            document = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
            declared = set(document["networks"])
            for name, service in document["services"].items():
                with self.subTest(file=filename, service=name):
                    self.assertLessEqual(set(service.get("networks", [])), declared)

    def test_production_workers_use_production_environment(self):
        document = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
        for name in WORKERS:
            with self.subTest(worker=name):
                service = document["services"][name]
                self.assertEqual(service["env_file"], [".env.prod"])
                self.assertIn("DEBUG=false", service["environment"])


if __name__ == "__main__":
    unittest.main()
