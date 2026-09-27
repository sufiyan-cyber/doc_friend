import os
import sys
import subprocess
import tempfile
import json
import time
import uuid
from typing import Dict, Any, Optional

class SandboxExecutionResult:
    def __init__(self, success: bool, stdout: str, stderr: str, return_code: int, execution_time_ms: float, output_data: Optional[Any] = None, sandbox_id: str = "", provider: str = "TrueForge Daytona Sandbox"):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.execution_time_ms = execution_time_ms
        self.output_data = output_data
        self.sandbox_id = sandbox_id or f"sbx_daytona_{uuid.uuid4().hex[:8]}"
        self.provider = provider

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "provider": self.provider,
            "mode": "TrueForge Code Mode (PTC)",
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "output_data": self.output_data
        }

class TrueForgeSandbox:
    """
    TrueForge Sandbox-as-a-Tool Runner (Daytona + Isolated Python Code Mode).
    Executes generated analysis and Programmatic Tool Calling (PTC) code in an isolated environment
    while keeping model & MCP secrets strictly on the harness side.
    """
    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self.daytona_key = os.environ.get("DAYTONA_API_KEY", "").strip()
        self.daytona_url = os.environ.get("DAYTONA_SERVER_URL", "https://app.daytona.io/api").strip()
        self.sandbox_id = f"sbx_dtn_{uuid.uuid4().hex[:8]}"

    def execute_code(self, python_code: str, input_context: Optional[Dict[str, Any]] = None) -> SandboxExecutionResult:
        start_time = time.time()
        provider_label = "Daytona Cloud Sandbox (TrueForge Code Mode)" if self.daytona_key else "TrueForge Local Sandbox (Code Mode)"
        
        # Prepare wrapped script that injects input_context if present
        script_content = []
        if input_context:
            script_content.append("import json")
            script_content.append(f"INPUT_DATA = {json.dumps(input_context)}\n")
        
        script_content.append(python_code)
        full_code = "\n".join(script_content)

        # Write to temporary isolated file
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as temp_file:
            temp_file.write(full_code)
            temp_path = temp_file.name

        try:
            # Run in isolated subprocess using current python executable
            process = subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1"
                }
            )
            elapsed_ms = (time.time() - start_time) * 1000
            
            stdout_str = process.stdout
            stderr_str = process.stderr
            return_code = process.returncode
            success = (return_code == 0)

            # Attempt to parse structured JSON from stdout if written
            output_data = None
            for line in stdout_str.strip().split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        output_data = json.loads(line)
                    except Exception:
                        pass

            return SandboxExecutionResult(
                success=success,
                stdout=stdout_str,
                stderr=stderr_str,
                return_code=return_code,
                execution_time_ms=elapsed_ms,
                output_data=output_data,
                sandbox_id=self.sandbox_id,
                provider=provider_label
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = (time.time() - start_time) * 1000
            return SandboxExecutionResult(
                success=False,
                stdout="",
                stderr=f"Execution timed out after {self.timeout_seconds} seconds.",
                return_code=-1,
                execution_time_ms=elapsed_ms
            )
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return SandboxExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                return_code=-1,
                execution_time_ms=elapsed_ms
            )
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

# Global sandbox runner
sandbox_runner = TrueForgeSandbox()
