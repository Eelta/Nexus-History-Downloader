"""Local HTTP integration check; build Downloader.Host before running this file."""

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

HOST_DLL = Path(__file__).resolve().parents[1] / "src/Downloader.Host/bin/Debug/net8.0/Downloader.Host.dll"
CONTENT = b"test archive bytes\n" * 128


class ArchiveServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(CONTENT)))
        self.send_header("Content-Disposition", 'attachment; filename="update.zip"')
        self.end_headers()
        self.wfile.write(CONTENT)

    def log_message(self, *args):
        pass


@unittest.skipUnless(HOST_DLL.is_file(), "build src/Downloader.Host first")
class HostLifecycleTests(unittest.TestCase):
    def api(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def start_host(self, root, max_threads=None):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env["CUSTOMDL_HOST_PORT"] = str(port)
        env.pop("CUSTOMDL_MAX_THREADS", None)
        if max_threads is not None:
            env["CUSTOMDL_MAX_THREADS"] = str(max_threads)
        self.base = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            ["dotnet", str(HOST_DLL)], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(self.stop_host, process)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                return process, self.api("/api/status")
            except OSError:
                if process.poll() is not None:
                    self.fail(f"host exited {process.returncode}")
                time.sleep(0.1)
        self.fail("host startup timed out")

    @staticmethod
    def stop_host(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    def wait_done(self, job_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            jobs = self.api("/api/status")["jobs"]
            job = next((j for j in jobs if j["id"] == job_id), None)
            if job and job["state"] in ("done", "error", "canceled"):
                self.assertEqual(job["state"], "done", job)
                return job
            time.sleep(0.1)
        self.fail("download completion timed out")

    def test_metadata_collision_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cache").mkdir()
            archive = root / "cache/update.zip"
            archive.write_bytes(b"existing archive must survive")
            legacy = {"id": "T001", "state": "done", "filename": "legacy.zip",
                      "savePath": str(root / "cache/legacy.zip"), "startedAt": "2020-01-01 00:00:00"}
            (root / "cache/takeover-jobs.jsonl").write_text(json.dumps(legacy) + "\n")
            server = ThreadingHTTPServer(("127.0.0.1", 0), ArchiveServer)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                process, status = self.start_host(root)
                self.assertEqual(status["maxThreads"], 256)
                self.assertTrue(any(j["id"] == "T001" for j in status["jobs"]))
                payload = {"url": f"http://127.0.0.1:{server.server_port}/archive",
                           "filename": "update.zip", "game": "skyrimspecialedition",
                           "modId": 42, "fileId": "81", "modName": "Existing MO2 name",
                           "referrer": "https://www.nexusmods.com/skyrimspecialedition/mods/42",
                           "cookie": "sid=not-for-job-history"}
                first = self.wait_done(self.api("/api/takeover", payload)["jobId"])
                self.assertEqual(first["filename"], "update (1).zip")
                for key in ("game", "modId", "fileId", "modName", "referrer"):
                    self.assertEqual(first[key], payload[key])
                payload["filename"] = "abcdef0123456789abcdef0123456789"
                second = self.wait_done(self.api("/api/takeover", payload)["jobId"])
                self.assertEqual(second["filename"], "update (2).zip")
                self.assertEqual(archive.read_bytes(), b"existing archive must survive")
                self.assertEqual(Path(first["savePath"]).read_bytes(), CONTENT)
                self.assertEqual(Path(second["savePath"]).read_bytes(), CONTENT)
                self.stop_host(process)
                history = (root / "cache/takeover-history.jsonl").read_text()
                self.assertNotIn("not-for-job-history", history)
                self.assertEqual(len(history.splitlines()), 3)
                restarted, status = self.start_host(root, max_threads=8)
                self.assertEqual(status["maxThreads"], 8)
                restored = {j["id"]: j for j in status["jobs"]}
                self.assertEqual(restored[first["id"]]["modId"], 42)
                self.assertEqual(restored[second["id"]]["savePath"], second["savePath"])
                payload["filename"] = "other.zip"
                third = self.wait_done(self.api("/api/takeover", payload)["jobId"])
                self.assertNotIn(third["id"], (first["id"], second["id"]))
                self.stop_host(restarted)
            finally:
                server.shutdown()
                server.server_close()

    def test_saved_threads_reload_and_environment_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = {"MaxThreads": 64, "custom": {"preserve": True}}
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps(settings))
            before = settings_path.read_bytes()
            first, status = self.start_host(root)
            self.assertEqual(status["maxThreads"], 64)
            self.assertEqual(self.api("/api/settings", {"maxThreads": 32})["maxThreads"], 32)
            self.assertEqual(settings_path.read_bytes(), before, "Python remains the only settings writer")
            self.stop_host(first)
            second, status = self.start_host(root)
            self.assertEqual(status["maxThreads"], 64)
            self.stop_host(second)
            third, status = self.start_host(root, max_threads=8)
            self.assertEqual(status["maxThreads"], 8)
            self.stop_host(third)
            settings_path.write_text(json.dumps({"MaxThreads": 99999}))
            fourth, status = self.start_host(root)
            self.assertEqual(status["maxThreads"], 256)
            self.stop_host(fourth)


if __name__ == "__main__":
    unittest.main()
