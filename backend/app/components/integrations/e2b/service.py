"""
E2B Code Interpreter service for sandboxed code execution.

Provides secure, isolated sandbox environments for running candidate
assessment code and test suites via the E2B platform.
"""

import logging
import os
import re

from e2b_code_interpreter import Sandbox  # v1.x
from e2b.sandbox.commands.command_handle import PtySize

logger = logging.getLogger(__name__)


class E2BService:
    """Service for executing code in E2B sandboxed environments."""

    def __init__(self, api_key: str, template: str | None = None):
        """
        Initialise the E2B service.

        Args:
            api_key: E2B platform API key.
        """
        self.api_key = api_key
        self.template = template or os.getenv("E2B_TEMPLATE")

    def get_sandbox_id(self, sandbox: Sandbox) -> str:
        """
        Resolve sandbox identifier across SDK versions.
        """
        for attr in ("id", "sandbox_id", "sandboxId"):
            value = getattr(sandbox, attr, None)
            if value:
                return str(value)
        raise AttributeError("Sandbox object has no id/sandbox_id attribute")

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
            if self.template:
                sandbox = Sandbox(api_key=self.api_key, template=self.template)
            else:
                sandbox = Sandbox(api_key=self.api_key)
            logger.info("E2B sandbox created successfully (id=%s)", self.get_sandbox_id(sandbox))
            return sandbox
        except Exception as e:
            logger.error("Failed to create E2B sandbox: %s", str(e))
            raise

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
            logger.info("Connected to E2B sandbox (id=%s)", sandbox_id)
            return sandbox
        except TypeError:
            # SDK may not support sandbox_id in constructor; fall back to create and log
            logger.warning(
                "E2B SDK does not support connect by id; creating new sandbox. "
                "Install a version that supports Sandbox(api_key=..., sandbox_id=...) for reuse."
            )
            return self.create_sandbox()
        except Exception as e:
            logger.error("Failed to connect to E2B sandbox (id=%s): %s", sandbox_id, str(e))
            raise

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
                logger.warning("Code execution produced an error: %s", error)

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
        except Exception as e:
            logger.error("Failed to execute code in sandbox: %s", str(e))
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": str(e),
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
                logger.warning("Test execution produced an error: %s", error)

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
        except Exception as e:
            logger.error("Failed to run tests in sandbox: %s", str(e))
            return {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": str(e),
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
            logger.error("Failed to parse pytest results: %s", str(e))

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
            logger.error("Failed to close E2B sandbox: %s", str(e))
