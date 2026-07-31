import importlib.util
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runtime.py"
SPEC = importlib.util.spec_from_file_location("supergemma_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime)


class RuntimeControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        runtime.RUNTIME_DIR = Path(self.temporary.name)
        runtime.STATE_PATH = runtime.RUNTIME_DIR / "server-state.json"
        runtime.START_LOCK_PATH = runtime.RUNTIME_DIR / "start.lock"
        runtime.REQUEST_LOCK_PATH = runtime.RUNTIME_DIR / "request.lock"
        runtime.LOG_PATH = runtime.RUNTIME_DIR / "server.log"

    def tearDown(self):
        self.temporary.cleanup()

    @mock.patch.object(runtime, "_port_open", return_value=False)
    @mock.patch.object(runtime, "_memory_free_percent", return_value=80)
    @mock.patch.object(runtime, "_process_rows", return_value=[])
    @mock.patch.object(runtime, "_listener_pids", return_value=[])
    @mock.patch.object(runtime, "_probe_server", return_value={"kind": "stopped"})
    def test_status_is_read_only_when_stopped(
        self,
        _probe,
        _listeners,
        _processes,
        _memory,
        _port,
    ):
        runtime.RUNTIME_DIR = Path(self.temporary.name) / "not-created"
        runtime.STATE_PATH = runtime.RUNTIME_DIR / "server-state.json"
        runtime.START_LOCK_PATH = runtime.RUNTIME_DIR / "start.lock"
        runtime.REQUEST_LOCK_PATH = runtime.RUNTIME_DIR / "request.lock"
        runtime.LOG_PATH = runtime.RUNTIME_DIR / "server.log"
        result = runtime.status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "stopped")
        self.assertFalse(result["managed"])
        self.assertFalse(runtime.RUNTIME_DIR.exists())
        _probe.assert_not_called()

    @mock.patch.object(runtime, "_port_open", return_value=False)
    @mock.patch.object(runtime, "_start_server")
    @mock.patch.object(
        runtime,
        "_process_rows",
        return_value=[
            {
                "pid": 4242,
                "executable": "TurboFieldfareMac",
                "command": "/tmp/TurboFieldfareMac",
                "tokens": ["/tmp/TurboFieldfareMac"],
            }
        ],
    )
    @mock.patch.object(runtime, "_listener_pids", return_value=[])
    @mock.patch.object(runtime, "_probe_server", return_value={"kind": "stopped"})
    def test_ensure_refuses_gui_conflict(
        self,
        _probe,
        _listeners,
        _processes,
        start,
        _port,
    ):
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime.ensure_server()
        self.assertEqual(caught.exception.code, "model_process_conflict")
        blocker = caught.exception.details["blockers"][0]
        self.assertEqual(blocker, {"pid": 4242, "executable": "TurboFieldfareMac"})
        self.assertNotIn("command", blocker)
        start.assert_not_called()

    @mock.patch.object(runtime, "_read_state", return_value=None)
    @mock.patch.object(runtime, "_listener_pids", return_value=[999])
    @mock.patch.object(runtime, "_probe_server", return_value={"kind": "target"})
    def test_stop_refuses_external_server(self, _probe, _listeners, _state):
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime.stop_server()
        self.assertEqual(caught.exception.code, "external_server")

    def test_rejects_non_loopback_base_url(self):
        original = runtime.BASE_URL
        runtime.BASE_URL = "http://example.com:8080"
        try:
            with self.assertRaises(runtime.RuntimeFailure) as caught:
                runtime._base_parts()
            self.assertEqual(caught.exception.code, "unsafe_base_url")
        finally:
            runtime.BASE_URL = original

    def test_rejects_invalid_max_context(self):
        original = runtime.MAX_CONTEXT
        runtime.MAX_CONTEXT = 0
        try:
            with self.assertRaises(runtime.RuntimeFailure) as caught:
                runtime._validated_max_context()
            self.assertEqual(caught.exception.code, "invalid_max_context")
        finally:
            runtime.MAX_CONTEXT = original

    def test_rejects_invalid_memory_threshold(self):
        original = runtime.MIN_FREE_PERCENT
        runtime.MIN_FREE_PERCENT = -1
        try:
            with self.assertRaises(runtime.RuntimeFailure) as caught:
                runtime._validated_min_free_percent()
            self.assertEqual(caught.exception.code, "invalid_memory_threshold")
        finally:
            runtime.MIN_FREE_PERCENT = original

    @mock.patch.object(runtime, "_start_server")
    @mock.patch.object(runtime, "_process_rows", return_value=[])
    @mock.patch.object(runtime, "_listener_pids", return_value=[7331])
    @mock.patch.object(runtime, "_probe_server", return_value={"kind": "target"})
    def test_ensure_never_trusts_unmanaged_target(
        self,
        _probe,
        _listeners,
        _processes,
        start,
    ):
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime.ensure_server()
        self.assertEqual(caught.exception.code, "external_server")
        start.assert_not_called()
        _probe.assert_not_called()

    @mock.patch.object(runtime, "_wait_until_ready", return_value={"kind": "target"})
    @mock.patch.object(runtime, "_wait_start_identity", return_value="identity")
    @mock.patch.object(
        runtime,
        "_preflight",
        return_value={"free_memory_percent": 90},
    )
    def test_start_uses_private_bearer_and_minimal_environment(
        self,
        _preflight,
        _identity,
        _ready,
    ):
        captured = {}

        class FakeProcess:
            pid = 8123

        def fake_popen(arguments, **kwargs):
            captured["arguments"] = arguments
            captured["environment"] = kwargs["env"]
            return FakeProcess()

        def capture_state(state):
            captured["state"] = state

        with (
            mock.patch.object(runtime.subprocess, "Popen", side_effect=fake_popen),
            mock.patch.object(runtime, "_write_state", side_effect=capture_state),
            mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "must-not-leak",
                    "HF_TOKEN": "must-not-leak",
                },
            ),
        ):
            result = runtime._start_server()

        state = captured["state"]
        environment = captured["environment"]
        self.assertGreaterEqual(len(state["auth_token"]), 32)
        self.assertEqual(
            environment["TURBOFIELDFARE_BEARER_TOKEN"],
            state["auth_token"],
        )
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("HF_TOKEN", environment)
        self.assertIn("--max-context", captured["arguments"])
        self.assertNotIn("auth_token", result)
        self.assertNotIn("launch_token", result)

    @mock.patch.object(runtime, "_verified_managed_probe", return_value={"kind": "target"})
    @mock.patch.object(runtime, "_managed_state")
    def test_request_posts_with_private_auth_and_returns_no_token(
        self,
        managed_state,
        _probe,
    ):
        state = {
            "pid": 7001,
            "auth_token": "a" * 43,
        }
        managed_state.return_value = state
        captured = {}

        def fake_request(path, **kwargs):
            captured["path"] = path
            captured.update(kwargs)
            return {
                "id": "completion",
                "choices": [{"message": {"content": "ready"}}],
            }

        with mock.patch.object(runtime, "_request_json", side_effect=fake_request):
            result = runtime.request_completion(
                {"model": runtime.MODEL_ID, "messages": []}
            )

        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(captured["auth_token"], state["auth_token"])
        self.assertLessEqual(len(captured["body"]), runtime.MAX_REQUEST_BYTES)
        self.assertEqual(result["payload"]["id"], "completion")
        self.assertNotIn("auth_token", result)
        self.assertNotIn(state["auth_token"], json.dumps(result))

    @mock.patch.object(runtime, "_request_json")
    @mock.patch.object(runtime, "_managed_state", return_value=None)
    @mock.patch.object(runtime, "_listener_pids", return_value=[9001])
    @mock.patch.object(
        runtime,
        "_probe_server",
        return_value={"kind": "unauthorized_server"},
    )
    def test_request_never_posts_to_unmanaged_listener(
        self,
        _probe,
        _listeners,
        _state,
        request_json,
    ):
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime.request_completion({"messages": []})
        self.assertEqual(caught.exception.code, "external_server")
        request_json.assert_not_called()

    def test_request_rejects_oversized_json_before_locking(self):
        with mock.patch.object(runtime, "_RequestLock") as request_lock:
            with self.assertRaises(runtime.RuntimeFailure) as caught:
                runtime.request_completion({"prompt": "x" * runtime.MAX_REQUEST_BYTES})
        self.assertEqual(caught.exception.code, "request_too_large")
        request_lock.assert_not_called()

    def test_request_stdin_is_bounded(self):
        oversized = io.BytesIO(b"x" * (runtime.MAX_REQUEST_BYTES + 1))
        fake_stdin = types.SimpleNamespace(buffer=oversized)
        with mock.patch.object(runtime.sys, "stdin", fake_stdin):
            with self.assertRaises(runtime.RuntimeFailure) as caught:
                runtime._read_request_body()
        self.assertEqual(caught.exception.code, "request_too_large")

    def test_bounded_response_rejects_excess_bytes(self):
        response = mock.Mock()
        response.read.return_value = b"x" * (runtime.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime._read_bounded_response(response, runtime.MAX_RESPONSE_BYTES)
        self.assertEqual(caught.exception.code, "response_too_large")

    def test_state_with_auth_token_is_written_privately(self):
        state = {
            "pid": 123,
            "launch_token": "l" * 64,
            "auth_token": "a" * 43,
        }
        runtime._write_state(state)
        self.assertEqual(runtime.STATE_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(runtime._read_state(), state)

    def test_request_lock_is_a_private_flock_file(self):
        with runtime._RequestLock():
            mode = runtime.REQUEST_LOCK_PATH.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    @mock.patch.object(runtime, "_clear_state_if_token")
    @mock.patch.object(runtime, "_pid_alive", return_value=False)
    @mock.patch.object(
        runtime,
        "_read_state",
        return_value={"pid": 4040, "launch_token": "l" * 64},
    )
    @mock.patch.object(runtime, "_listener_pids", return_value=[5050])
    @mock.patch.object(runtime, "_probe_server", return_value={"kind": "target"})
    def test_stop_stale_state_reports_external_service(
        self,
        _probe,
        _listeners,
        _state,
        _alive,
        clear_state,
    ):
        with self.assertRaises(runtime.RuntimeFailure) as caught:
            runtime.stop_server()
        self.assertEqual(caught.exception.code, "external_server")
        clear_state.assert_called_once_with("l" * 64)

    def test_managed_match_requires_auth_and_max_context(self):
        command = (
            f"{runtime.SERVER_BIN} --model {runtime.MODEL_PATH} "
            f"--model-id {runtime.MODEL_ID} --port 8080 "
            f"--max-context {runtime.MAX_CONTEXT}"
        )
        state = {
            "pid": 123,
            "start_identity": "same",
            "launch_token": "l" * 64,
            "server_bin": str(runtime.SERVER_BIN),
            "model_path": str(runtime.MODEL_PATH),
            "model_id": runtime.MODEL_ID,
            "base_url": runtime.BASE_URL,
            "max_context": runtime.MAX_CONTEXT,
        }
        with (
            mock.patch.object(runtime, "_command_for_pid", return_value=command),
            mock.patch.object(runtime, "_start_identity", return_value="same"),
        ):
            self.assertFalse(runtime._matches_managed_server(123, state))
            state["auth_token"] = "a" * 43
            self.assertTrue(runtime._matches_managed_server(123, state))
            state["max_context"] += 1
            self.assertFalse(runtime._matches_managed_server(123, state))

    def test_managed_match_requires_exact_launch_arguments(self):
        command = (
            f"{runtime.SERVER_BIN} --model {runtime.MODEL_PATH} "
            f"--model-id {runtime.MODEL_ID} --port 8080 "
            f"--max-context {runtime.MAX_CONTEXT} --queue-limit 9"
        )
        with mock.patch.object(runtime, "_command_for_pid", return_value=command):
            self.assertFalse(runtime._matches_managed_server(123))

    @mock.patch.object(runtime, "_wait_start_identity", return_value="identity")
    @mock.patch.object(
        runtime,
        "_preflight",
        return_value={"free_memory_percent": 90},
    )
    def test_start_terminates_child_if_private_state_cannot_be_written(
        self,
        _preflight,
        _identity,
    ):
        class FakeProcess:
            pid = 8124
            terminated = False

            def poll(self):
                return 0 if self.terminated else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                return 0

        process = FakeProcess()
        with (
            mock.patch.object(runtime.subprocess, "Popen", return_value=process),
            mock.patch.object(
                runtime,
                "_write_state",
                side_effect=OSError("state write failed"),
            ),
        ):
            with self.assertRaises(OSError):
                runtime._start_server()
        self.assertTrue(process.terminated)

    @mock.patch.object(
        runtime,
        "_preflight",
        return_value={"free_memory_percent": 90},
    )
    def test_start_terminates_child_if_controller_is_interrupted_after_spawn(
        self,
        _preflight,
    ):
        class FakeProcess:
            pid = 8125
            terminated = False

            def poll(self):
                return 0 if self.terminated else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout):
                return 0

        process = FakeProcess()
        with (
            mock.patch.object(runtime.subprocess, "Popen", return_value=process),
            mock.patch.object(
                runtime,
                "_wait_start_identity",
                side_effect=KeyboardInterrupt(),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                runtime._start_server()
        self.assertTrue(process.terminated)

    def test_verified_probe_checks_listener_before_sending_bearer(self):
        state = {"pid": 8126, "auth_token": "a" * 43}
        with (
            mock.patch.object(runtime, "_listener_pids", return_value=[9000]),
            mock.patch.object(runtime, "_probe_server") as probe,
        ):
            result = runtime._verified_managed_probe(state)
        self.assertEqual(result["kind"], "listener_mismatch")
        probe.assert_not_called()

    def test_verified_probe_rechecks_listener_after_authentication(self):
        state = {"pid": 8127, "auth_token": "a" * 43}
        with (
            mock.patch.object(
                runtime,
                "_listener_pids",
                side_effect=[[8127], [8127], [8127]],
            ),
            mock.patch.object(
                runtime,
                "_probe_server",
                side_effect=[
                    {"kind": "unauthorized_server"},
                    {"kind": "target"},
                ],
            ) as probe,
        ):
            result = runtime._verified_managed_probe(state)
        self.assertEqual(result, {"kind": "target"})
        self.assertEqual(
            probe.call_args_list,
            [mock.call(), mock.call(state["auth_token"])],
        )

    def test_verified_probe_rejects_server_that_does_not_enforce_authentication(
        self,
    ):
        state = {"pid": 8128, "auth_token": "a" * 43}
        with (
            mock.patch.object(
                runtime,
                "_listener_pids",
                side_effect=[[8128], [8128]],
            ),
            mock.patch.object(
                runtime,
                "_probe_server",
                return_value={"kind": "target"},
            ) as probe,
        ):
            result = runtime._verified_managed_probe(state)
        self.assertEqual(result, {"kind": "authentication_not_enforced"})
        probe.assert_called_once_with()

    def test_failed_startup_force_kills_only_its_direct_child_after_grace(self):
        class FakeProcess:
            pid = 8129
            killed = False
            waits = 0

            def poll(self):
                return None

            def terminate(self):
                pass

            def kill(self):
                self.killed = True

            def wait(self, timeout):
                self.waits += 1
                if self.waits == 1:
                    raise runtime.subprocess.TimeoutExpired("server", timeout)
                return -runtime.signal.SIGKILL

        process = FakeProcess()
        runtime._terminate_direct_child(process)
        self.assertTrue(process.killed)
        self.assertEqual(process.waits, 2)

    @mock.patch.object(runtime, "_clear_state_if_token")
    @mock.patch.object(runtime.os, "kill")
    @mock.patch.object(
        runtime,
        "_verified_managed_probe",
        return_value={"kind": "stopped"},
    )
    @mock.patch.object(runtime, "_matches_managed_server", return_value=True)
    @mock.patch.object(runtime, "_pid_alive", side_effect=[True, False, False])
    @mock.patch.object(
        runtime,
        "_read_state",
        return_value={"pid": 8130, "launch_token": "l" * 64},
    )
    def test_stop_can_terminate_an_exact_owned_server_while_it_is_starting(
        self,
        _state,
        _alive,
        _matches,
        _probe,
        kill,
        clear,
    ):
        result = runtime.stop_server()
        self.assertTrue(result["stopped"])
        kill.assert_called_once_with(8130, runtime.signal.SIGTERM)
        clear.assert_called_once_with("l" * 64)

    def test_mlx_module_and_script_detection(self):
        self.assertTrue(
            runtime._looks_like_mlx_process(
                "python3",
                ["python3", "-m", "mlx_lm.server"],
            )
        )
        self.assertTrue(
            runtime._looks_like_mlx_process(
                "python3",
                ["python3", "/tmp/mlx_lm/server.py"],
            )
        )
        self.assertFalse(
            runtime._looks_like_mlx_process(
                "python3",
                ["python3", "/tmp/unrelated.py"],
            )
        )


if __name__ == "__main__":
    unittest.main()
