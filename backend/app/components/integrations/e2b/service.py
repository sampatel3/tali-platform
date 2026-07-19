"""
E2B Code Interpreter service for sandboxed code execution.

Provides secure, isolated sandbox environments for running candidate
assessment code and test suites via the E2B platform.
"""

import logging
import math
import os
import re

import httpx
from e2b.api.metadata import default_headers as e2b_default_headers
from e2b.connection_config import ConnectionConfig
from e2b_code_interpreter import Sandbox  # v1.x
from e2b.sandbox.commands.command_handle import PtySize

from ....services.provider_error_evidence import safe_provider_error_code

logger = logging.getLogger(__name__)


class E2BProviderError(RuntimeError):
    """Secret-safe failure raised after an E2B SDK operation fails."""


class E2BService:
    """Service for executing code in E2B sandboxed environments."""

    def __init__(
        self,
        api_key: str,
        template: str | None = None,
        proxy: str | None = None,
    ):
        """
        Initialise the E2B service.

        Args:
            api_key: E2B platform API key.
        """
        self.api_key = api_key
        self.template = template or os.getenv("E2B_TEMPLATE")
        self.proxy = proxy
        # Egress switch for candidate sandboxes. Default True because the task
        # bootstrap pip-installs deps from PyPI — a hard block additionally
        # requires pre-baking deps into the E2B template. Flip the env to
        # "false" once the template carries the deps to fully cut internet.
        self.allow_internet_access = (
            os.getenv("E2B_SANDBOX_ALLOW_INTERNET", "true").strip().lower()
            not in {"0", "false", "no", "off"}
        )
        try:
            requested_timeout = int(os.getenv("E2B_SANDBOX_TIMEOUT_SECONDS", "3600"))
            # E2B enforces maximum timeout of 1 hour.
            self.sandbox_timeout_seconds = max(300, min(3600, requested_timeout))
        except Exception:
            self.sandbox_timeout_seconds = 3600

    def verify_access(self, *, request_timeout_seconds: float = 10.0) -> bool:
        """Verify API-key access through one bounded, read-only list request.

        The pinned SDK does not apply ``request_timeout`` to ``Sandbox.list``,
        so this path uses its connection/domain conventions with an explicit
        HTTPX timeout instead. ``GET /v2/sandboxes?limit=1`` does not create,
        connect to, extend, or stop a sandbox; an empty result still proves that
        E2B accepted the credential. The response body is intentionally ignored.
        """
        api_key = str(self.api_key or "").strip()
        if (
            not api_key
            or api_key.lower() in {"skip", "changeme"}
            or api_key.lower().startswith("your-")
        ):
            raise ValueError("E2B_API_KEY is not configured")
        timeout_seconds = float(request_timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive and finite")

        config = ConnectionConfig(api_key=api_key, proxy=self.proxy)
        timeout = httpx.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        response_status: int | None = None
        request_error: str | None = None
        try:
            with httpx.Client(
                base_url=config.api_url,
                headers={
                    **e2b_default_headers,
                    **config.headers,
                    "X-API-KEY": api_key,
                },
                timeout=timeout,
                follow_redirects=False,
                proxy=config.proxy,
            ) as client:
                with client.stream(
                    "GET",
                    "/v2/sandboxes",
                    params={"limit": 1},
                ) as response:
                    response_status = int(response.status_code)
        except httpx.TimeoutException:
            request_error = "E2B credential verification timed out"
        except Exception:
            # Do not preserve SDK/HTTP exception text or context: it may contain
            # credential headers, proxy credentials, or a provider response.
            request_error = "E2B credential verification request failed"

        if request_error is not None:
            raise RuntimeError(request_error)
        if response_status != httpx.codes.OK:
            raise RuntimeError(
                f"E2B credential verification failed (HTTP {response_status})"
            )
        return True

    def _apply_sandbox_timeout(self, sandbox: Sandbox) -> None:
        timeout_seconds = int(getattr(self, "sandbox_timeout_seconds", 0) or 0)
        if timeout_seconds <= 0:
            return
        try:
            sandbox.set_timeout(timeout_seconds)
        except Exception as exc:
            logger.debug(
                "Failed to extend E2B sandbox timeout error_type=%s",
                type(exc).__name__,
            )

    def get_sandbox_id(self, sandbox: Sandbox) -> str:
        """Return the identifier exposed by the pinned E2B SDK."""

        value = sandbox.sandbox_id
        if not value:
            raise AttributeError("Sandbox object has no sandbox_id")
        return str(value)

    def create_sandbox(self) -> Sandbox:
        """
        Create a new E2B sandbox instance.

        Returns:
            A running Sandbox instance ready for code execution.

        Raises:
            Exception: If sandbox creation fails.
        """
        try:
            logger.info("Creating new E2B sandbox")
            sandbox_options = {
                "api_key": self.api_key,
                "timeout": self.sandbox_timeout_seconds,
                "allow_internet_access": self.allow_internet_access,
            }
            if self.template:
                sandbox_options["template"] = self.template
            sandbox = Sandbox(**sandbox_options)
            logger.info("E2B sandbox created successfully (id=%s)", self.get_sandbox_id(sandbox))
            return sandbox
        except Exception as exc:
            error_code = safe_provider_error_code(exc, operation="e2b_create_sandbox")
            logger.error("Failed to create E2B sandbox error_code=%s", error_code)
        raise E2BProviderError(error_code)

    def connect_sandbox(self, sandbox_id: str) -> Sandbox:
        """
        Connect to an existing E2B sandbox by ID (e.g. from assessment.e2b_session_id).

        Returns:
            A Sandbox instance connected to the existing sandbox.

        Raises:
            Exception: If connection fails (e.g. sandbox no longer running).
        """
        try:
            logger.info("Connecting to existing E2B sandbox (id=%s)", sandbox_id)
            sandbox = Sandbox(api_key=self.api_key, sandbox_id=sandbox_id)
            self._apply_sandbox_timeout(sandbox)
            logger.info("Connected to E2B sandbox (id=%s)", sandbox_id)
            return sandbox
        except Exception as exc:
            error_code = safe_provider_error_code(exc, operation="e2b_connect_sandbox")
            logger.error(
                "Failed to connect to E2B sandbox (id=%s) error_code=%s",
                sandbox_id,
                error_code,
            )
        raise E2BProviderError(error_code)

    def touch_sandbox(self, sandbox: Sandbox) -> None:
        """
        Best-effort keepalive by extending sandbox timeout.
        """
        self._apply_sandbox_timeout(sandbox)

    def execute_code(self, sandbox: Sandbox, code: str) -> dict:
        """
        Execute arbitrary code inside an E2B sandbox.

        Args:
            sandbox: An active E2B Sandbox instance.
            code: The Python code string to execute.

        Returns:
            Dict with keys: success, stdout, stderr, error, results.
        """
        try:
            logger.info("Executing code in sandbox (length=%d chars)", len(code))
            execution = sandbox.run_code(code)

            # v1.x: logs.stdout/stderr may be lists of strings
            raw_stdout = execution.logs.stdout if execution.logs.stdout else []
            raw_stderr = execution.logs.stderr if execution.logs.stderr else []
            stdout = "\n".join(raw_stdout) if isinstance(raw_stdout, list) else str(raw_stdout)
            stderr = "\n".join(raw_stderr) if isinstance(raw_stderr, list) else str(raw_stderr)

            # Collect rich results (charts, dataframes, etc.)
            results = []
            if execution.results:
                for result in execution.results:
                    results.append(str(result))

            error = None
            if execution.error:
                error = f"{execution.error.name}: {execution.error.value}"
                logger.warning("Code execution produced an error")

            success = execution.error is None
            logger.info(
                "Code execution completed (success=%s, stdout_len=%d)",
                success,
                len(stdout),
            )

            return {
                "success": success,
                "stdout": stdout,
                "stderr": stderr,
                "error": error,
                "results": results,
            }
        except Exception as exc:
            code = safe_provider_error_code(exc, operation="e2b_execute_code")
            logger.error("Failed to execute code in sandbox error_code=%s", code)
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": code,
                "results": [],
            }

    def create_pty(
        self,
        sandbox: Sandbox,
        *,
        rows: int = 30,
        cols: int = 120,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
    ):
        """
        Create an interactive PTY session in the sandbox.
        """
        return sandbox.pty.create(
            size=PtySize(rows=rows, cols=cols),
            cwd=cwd,
            envs=envs,
            timeout=0,
        )

    def connect_process(self, sandbox: Sandbox, pid: int):
        """
        Connect to an existing running process/PTY by PID.
        """
        return sandbox.commands.connect(pid=pid, timeout=0)

    def send_pty_input(self, sandbox: Sandbox, pid: int, data: str | bytes) -> None:
        """
        Send input bytes to an active PTY session.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        sandbox.pty.send_stdin(pid=pid, data=payload)

    def resize_pty(self, sandbox: Sandbox, pid: int, *, rows: int, cols: int) -> None:
        """
        Resize an active PTY session.
        """
        sandbox.pty.resize(pid=pid, size=PtySize(rows=rows, cols=cols))

    def kill_process(self, sandbox: Sandbox, pid: int) -> bool:
        """
        Kill a running process/PTY by PID.
        """
        try:
            return bool(sandbox.pty.kill(pid=pid))
        except Exception:
            try:
                return bool(sandbox.commands.kill(pid=pid))
            except Exception:
                return False

    def run_command(
        self,
        sandbox: Sandbox,
        command: str,
        *,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        timeout: float = 30,
    ):
        """
        Run a shell command in the sandbox and wait for completion.
        """
        return sandbox.commands.run(
            command,
            cwd=cwd,
            envs=envs,
            timeout=timeout,
        )

    def run_tests(self, sandbox: Sandbox, test_code: str) -> dict:
        """
        Write a test file into the sandbox and run it with pytest.

        Args:
            sandbox: An active E2B Sandbox instance.
            test_code: Python test code (pytest-compatible) to run.

        Returns:
            Dict with keys: success, stdout, stderr, error, passed, failed, total.
        """
        try:
            logger.info("Running tests in sandbox (length=%d chars)", len(test_code))

            # Write test file to sandbox filesystem
            sandbox.files.write("/tmp/test_assessment.py", test_code)
            logger.debug("Test file written to /tmp/test_assessment.py")

            # Execute pytest
            execution = sandbox.run_code(
                "import subprocess\n"
                "result = subprocess.run(\n"
                "    ['python', '-m', 'pytest', '/tmp/test_assessment.py', '-v'],\n"
                "    capture_output=True, text=True\n"
                ")\n"
                "print(result.stdout)\n"
                "print(result.stderr)\n"
            )

            raw_stdout = execution.logs.stdout if execution.logs.stdout else []
            raw_stderr = execution.logs.stderr if execution.logs.stderr else []
            stdout = "\n".join(raw_stdout) if isinstance(raw_stdout, list) else str(raw_stdout)
            stderr = "\n".join(raw_stderr) if isinstance(raw_stderr, list) else str(raw_stderr)
            full_output = stdout + stderr

            # Parse test results
            parsed = self._parse_pytest_results(full_output)

            error = None
            if execution.error:
                error = f"{execution.error.name}: {execution.error.value}"
                logger.warning("Test execution produced an error")

            success = parsed["failed"] == 0 and parsed["passed"] > 0
            logger.info(
                "Test run completed: %d passed, %d failed",
                parsed["passed"],
                parsed["failed"],
            )

            return {
                "success": success,
                "stdout": stdout,
                "stderr": stderr,
                "error": error,
                "passed": parsed["passed"],
                "failed": parsed["failed"],
                "total": parsed["total"],
            }
        except Exception as exc:
            code = safe_provider_error_code(exc, operation="e2b_run_tests")
            logger.error("Failed to run tests in sandbox error_code=%s", code)
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": code,
                "passed": 0,
                "failed": 0,
                "total": 0,
            }

    def _parse_pytest_results(self, output: str) -> dict:
        """
        Extract passed/failed counts from pytest output.

        Args:
            output: Raw pytest console output.

        Returns:
            Dict with keys: passed, failed, total.
        """
        passed = 0
        failed = 0

        try:
            # Match pytest summary line, e.g. "2 passed, 1 failed" or "5 passed"
            passed_match = re.search(r"(\d+)\s+passed", output)
            failed_match = re.search(r"(\d+)\s+failed", output)

            if passed_match:
                passed = int(passed_match.group(1))
            if failed_match:
                failed = int(failed_match.group(1))

            logger.debug("Parsed pytest results: passed=%d, failed=%d", passed, failed)
        except Exception as e:
            logger.error("Failed to parse pytest results error_type=%s", type(e).__name__)

        return {
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
        }

    def close_sandbox(self, sandbox: Sandbox) -> None:
        """
        Safely close an E2B sandbox, releasing resources.

        Args:
            sandbox: The Sandbox instance to close.
        """
        try:
            try:
                sandbox_id = self.get_sandbox_id(sandbox)
            except Exception:
                sandbox_id = "unknown"
            logger.info("Closing E2B sandbox (id=%s)", sandbox_id)
            sandbox.kill()
            logger.info("E2B sandbox closed successfully (id=%s)", sandbox_id)
        except Exception as e:
            logger.error("Failed to close E2B sandbox error_type=%s", type(e).__name__)
