"""
E2B Code Interpreter service for sandboxed code execution.

Provides secure, isolated sandbox environments for running candidate
assessment code and test suites via the E2B platform.
"""

import logging
import re

from e2b_code_interpreter import Sandbox

logger = logging.getLogger(__name__)


class E2BService:
    """Service for executing code in E2B sandboxed environments."""

    def __init__(self, api_key: str):
        """
        Initialise the E2B service.

        Args:
            api_key: E2B platform API key.
        """
        self.api_key = api_key

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
            sandbox = Sandbox(api_key=self.api_key)
            logger.info("E2B sandbox created successfully (id=%s)", sandbox.id)
            return sandbox
        except Exception as e:
            logger.error("Failed to create E2B sandbox: %s", str(e))
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

            stdout = execution.logs.stdout if execution.logs.stdout else ""
            stderr = execution.logs.stderr if execution.logs.stderr else ""

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

            stdout = execution.logs.stdout if execution.logs.stdout else ""
            stderr = execution.logs.stderr if execution.logs.stderr else ""
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
            sandbox_id = getattr(sandbox, "id", "unknown")
            logger.info("Closing E2B sandbox (id=%s)", sandbox_id)
            sandbox.close()
            logger.info("E2B sandbox closed successfully (id=%s)", sandbox_id)
        except Exception as e:
            logger.error("Failed to close E2B sandbox: %s", str(e))
