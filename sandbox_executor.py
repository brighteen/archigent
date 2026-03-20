'''
sandbox_executor.py - ArchiGent Sandbox Executor
==============================================
Coder Agent가 생성한 ifcopenshell 코드를 격리된 프로세스로 실행하고 에러를 캡처합니다.
'''

import os
import sys
import tempfile
import subprocess
import logging
import traceback
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class SandboxResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    error_type: str = ""
    generated_file_exists: bool = False

def execute_in_sandbox(
    code_str: str,
    input_ifc_path: str,
    output_ifc_path: str,
    expect_output_file: bool = True,
    timeout: int = 60,
) -> SandboxResult:
    """코드를 임시 파일로 작성 후 서브프로세스로 실행"""
    tmp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix="_archi_script.py",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(code_str)
            tmp_file_path = f.name

        # 환경변수 전달
        child_env = os.environ.copy()
        child_env["IFC_INPUT_PATH"] = str(input_ifc_path)
        child_env["IFC_OUTPUT_PATH"] = str(output_ifc_path)

        proc = subprocess.run(
            [sys.executable, tmp_file_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
        )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        return_code = proc.returncode
        generated_file_exists = os.path.exists(output_ifc_path)

        error_type = ""
        if return_code != 0:
            if "SyntaxError" in stderr or "IndentationError" in stderr:
                error_type = "syntax_error"
            elif "Traceback" in stderr:
                error_type = "runtime_error"
            else:
                error_type = "crash"

        # 수정 요청(MODIFY)일 때만 파일 생성 여부 체크
        success = (return_code == 0)
        if expect_output_file and not generated_file_exists:
            success = False

        return SandboxResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            error_type=error_type,
            generated_file_exists=generated_file_exists,
        )

    except subprocess.TimeoutExpired:
        return SandboxResult(success=False, stderr="TimeoutError: Execution exceeded limit.", error_type="timeout")
    except Exception as e:
        return SandboxResult(success=False, stderr=str(e), error_type="crash")
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.remove(tmp_file_path)
            except:
                pass

def build_error_feedback(result: SandboxResult, attempt: int, max_retries: int) -> str:
    """에러 메시지 포맷팅"""
    feedback = f"### [Attempt {attempt}/{max_retries}] Execution Failed\n"
    feedback += f"- Error Type: {result.error_type}\n"
    if result.stderr:
        feedback += f"#### Stderr (Traceback):\n```python\n{result.stderr[-2000:]}\n```\n"
    if not result.generated_file_exists and result.return_code == 0:
        feedback += "\n⚠️ 스크립트가 성공했으나 출력 파일이 생성되지 않았습니다. `model.write()`를 호출했는지 확인하세요.\n"
    return feedback
