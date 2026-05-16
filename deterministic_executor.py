'''
deterministic_executor.py - ArchiGent Executor
==============================================
Replaces sandbox_executor.py. Instead of running generated python scripts in an
isolated subprocess, this module parses the generated JSON action list, safely 
executes them using bim_actions, and captures results/exceptions.
'''

import os
import json
import logging
import traceback
from dataclasses import dataclass
from typing import Optional
import ifcopenshell

from bim_actions import execute_actions_from_json

logger = logging.getLogger(__name__)

@dataclass
class ExecutorResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    error_type: str = ""
    generated_file_exists: bool = False

def execute_in_deterministic_engine(
    json_action_str: str,
    input_ifc_path: str,
    output_ifc_path: str,
    expect_output_file: bool = True,
) -> ExecutorResult:
    """
    Parses JSON actions, loads the IFC, applies actions, writes the IFC.
    Returns ExecutionResult with logs and success status.
    """
    stdout_logs = []
    
    try:
        # 1. Parse JSON
        try:
            actions = json.loads(json_action_str)
        except json.JSONDecodeError as e:
            return ExecutorResult(
                success=False, 
                stderr=f"JSON Parse Error: Make sure only valid JSON is outputted.\n{str(e)}\nInput was:\n{json_action_str}", 
                error_type="json_error"
            )
            
        if not isinstance(actions, list):
            return ExecutorResult(
                success=False, 
                stderr="Root JSON object must be an array of action objects.", 
                error_type="json_structure_error"
            )
            
        if not actions:
            stdout_logs.append("No actions to execute.")
            return ExecutorResult(success=True, stdout="\n".join(stdout_logs), generated_file_exists=True) # Maybe just return early?
            
        # 2. Load IFC
        try:
            model = ifcopenshell.open(str(input_ifc_path))
        except Exception as e:
            return ExecutorResult(
                success=False, 
                stderr=f"Failed to open IFC model at {input_ifc_path}: {e}", 
                error_type="ifc_load_error"
            )
            
        # 3. Apply Actions
        try:
            action_logs = execute_actions_from_json(model, actions)
            stdout_logs.extend(action_logs)
        except Exception as e:
            trace = traceback.format_exc()
            return ExecutorResult(
                success=False, 
                stderr=f"Action Execution Error: {str(e)}\n\n{trace}", 
                error_type="action_execution_error"
            )
            
        # 4. Save Modified IFC
        if expect_output_file:
            try:
                # Ensure output directory exists
                os.makedirs(os.path.dirname(output_ifc_path), exist_ok=True)
                model.write(str(output_ifc_path))
            except Exception as e:
                return ExecutorResult(
                    success=False, 
                    stderr=f"Failed to save modified IFC to {output_ifc_path}: {e}", 
                    error_type="ifc_save_error"
                )
                
        return ExecutorResult(
            success=True,
            stdout="\n".join(stdout_logs),
            generated_file_exists=os.path.exists(output_ifc_path)
        )

    except Exception as e:
        trace = traceback.format_exc()
        return ExecutorResult(success=False, stderr=f"Unknown Error: {str(e)}\n\n{trace}", error_type="crash")


def build_error_feedback(result: ExecutorResult, attempt: int, max_retries: int) -> str:
    """Formats the error message to be sent back to the LLM"""
    feedback = f"### [Attempt {attempt}/{max_retries}] Action Parsing/Execution Failed\n"
    feedback += f"- Error Type: {result.error_type}\n"
    if result.stderr:
        feedback += f"#### Detailed Error:\n```\n{result.stderr[-2000:]}\n```\n"
    return feedback
