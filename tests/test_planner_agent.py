"""
tests/test_planner_agent.py — Planner Agent 단위 테스트

실행 방법:
    python -m pytest tests/test_planner_agent.py -v

주의:
    LLM 호출 테스트(test_llm_*)는 실제 API 키가 필요합니다.
    오프라인 구조 테스트는 API 키 없이 실행 가능합니다.
"""

import json
import re
import sys
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import planner_agent


# ── 공통 픽스처 ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_analyzer_context() -> dict:
    """정상적인 Analyzer 출력 샘플 (IfcWallStandardCase MODIFY 시나리오)"""
    return {
        "targets": [
            {
                "globalId": "2O2Fr$t4X7Zf8NOew3FLOH",
                "ifcClass": "IfcWallStandardCase",
                "name": "Basic Wall:Interior - Partition:187578",
                "attributes": {
                    "Width": 200,
                    "Height": 2800,
                    "Length": 4500,
                },
                "relationships": {
                    "CONTAINED_IN": "Level 2",
                    "AGGREGATES": None,
                },
                "properties_json": json.dumps({
                    "Pset_WallCommon": {"LoadBearing": False, "IsExternal": False},
                    "BaseQuantities": {"Width": 200, "Height": 2800, "Length": 4500},
                }),
            }
        ],
        "query_summary": "2층 복도의 내부 파티션 벽체 1개 식별",
        "cypher_used": "MATCH (w:IfcWallStandardCase) WHERE w.name CONTAINS 'Partition' RETURN w",
    }


@pytest.fixture
def sample_user_request() -> str:
    return "2층 복도에 있는 내부 파티션 벽의 두께를 200mm에서 300mm로 변경해줘"


@pytest.fixture
def mock_task_spec() -> str:
    """코드 없이 Step 구조를 올바르게 갖춘 가짜 LLM 응답"""
    return """=== TASK SPECIFICATION ===
Request: "2층 복도 파티션 벽 두께 200mm → 300mm 변경"
Target Count: 1
Operation Type: MODIFY

--- Step 1: 대상 객체 확인 ---
- 대상: IfcWallStandardCase (GlobalId: 2O2Fr$t4X7Zf8NOew3FLOH)
- 현재 Width: 200mm
- 위치: Level 2 복도

--- Step 2: 사전 무결성 확인 ---
- 호스팅된 IfcOpeningElement 여부 확인

--- Step 3: Width 속성 수정 ---
- OverallWidth 속성을 200에서 300으로 변경 (단위: mm)

--- Step 4: 기하학적 표현 갱신 ---
- IfcRectangleProfileDef의 YDim을 300으로 갱신

--- Step 5: 결과 검증 ---
- 수정 후 OverallWidth == 300임을 확인
=== END SPECIFICATION ==="""


@pytest.fixture
def mock_intent_json() -> str:
    """Chain 1이 반환하는 가짜 Intent JSON"""
    return json.dumps({
        "operation_type": "MODIFY",
        "user_intent_summary": "2층 복도 파티션 벽 두께를 200mm에서 300mm로 변경",
        "targets": [
            {
                "globalId": "2O2Fr$t4X7Zf8NOew3FLOH",
                "ifcClass": "IfcWallStandardCase",
                "name": "Basic Wall:Interior - Partition:187578",
                "current_state": {"Width": 200, "Height": 2800},
                "spatial_location": "Level 2, 복도",
            }
        ],
        "parameters": [
            {
                "attribute_name": "Width",
                "ifc_pset_hint": "Pset_WallCommon.Width 또는 OverallWidth",
                "current_value": "200mm",
                "target_value": "300mm",
                "operation": "SET",
            }
        ],
        "constraints": ["인접 Door/Window와의 간섭 확인 필요"],
        "assumptions": [],
    }, ensure_ascii=False)


# ── 유틸리티 함수 테스트 ─────────────────────────────────────────────────────

class TestLoadPrompt:
    def test_loads_existing_file(self, tmp_path):
        prompt_file = tmp_path / "test_prompt.txt"
        prompt_file.write_text("Hello <<variable>>", encoding="utf-8")
        result = planner_agent._load_prompt(prompt_file)
        assert result == "Hello <<variable>>"

    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            planner_agent._load_prompt(tmp_path / "nonexistent.txt")


class TestValidateTaskSpec:
    def test_valid_spec_passes(self, mock_task_spec):
        # 유효한 spec은 예외 없이 통과해야 함
        planner_agent._validate_task_spec(mock_task_spec)

    def test_rejects_python_import(self):
        bad_spec = "Step 1: import ifcopenshell\n--- 완료 ---"
        with pytest.raises(ValueError, match="코드 패턴"):
            planner_agent._validate_task_spec(bad_spec)

    def test_rejects_def_statement(self):
        bad_spec = "Step 1: 아래 함수를 실행하세요.\ndef modify_wall(ifc_file, gid):\n    pass"
        with pytest.raises(ValueError, match="코드 패턴"):
            planner_agent._validate_task_spec(bad_spec)

    def test_rejects_ifcopenshell_call(self):
        bad_spec = "Step 1: ifcopenshell.open('model.ifc')를 호출하세요."
        with pytest.raises(ValueError, match="코드 패턴"):
            planner_agent._validate_task_spec(bad_spec)

    def test_rejects_exec_call(self):
        bad_spec = "Step 1: exec('import os')를 실행하세요."
        with pytest.raises(ValueError, match="코드 패턴"):
            planner_agent._validate_task_spec(bad_spec)

    def test_rejects_markdown_code_block(self):
        bad_spec = "Step 1:\n```python\nifc = ifcopenshell.open('file.ifc')\n```"
        with pytest.raises(ValueError, match="코드 패턴"):
            planner_agent._validate_task_spec(bad_spec)

    def test_rejects_spec_without_steps(self):
        no_step_spec = "그냥 벽 두께를 바꾸면 됩니다."
        with pytest.raises(ValueError, match="Step 구조"):
            planner_agent._validate_task_spec(no_step_spec)

    def test_korean_step_word_accepted(self):
        korean_step_spec = "단계 1: 확인\n단계 2: 수정"
        # "단계"가 포함되어 있으므로 통과해야 함
        planner_agent._validate_task_spec(korean_step_spec)


# ── generate_task_specification 통합 테스트 (LLM Mock) ──────────────────────

class TestGenerateTaskSpecification:
    """LLM 호출을 Mock하여 함수 흐름만 테스트합니다 (API 키 불필요)."""

    def test_returns_task_spec_string(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec
    ):
        """2-chain 흐름이 완료되고 Task Spec 문자열을 반환하는지 확인"""
        with patch.object(
            planner_agent, "_call_llm",
            side_effect=[mock_intent_json, mock_task_spec]
        ):
            result = planner_agent.generate_task_specification(
                analyzer_context=sample_analyzer_context,
                user_request=sample_user_request,
                model="claude",
            )
        assert isinstance(result, str)
        assert "Step" in result
        assert "=== TASK SPECIFICATION ===" in result

    def test_chain1_receives_user_request_in_prompt(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec
    ):
        """Chain 1 프롬프트에 user_request가 포함되는지 확인"""
        captured_prompts = []

        def capture_and_return(prompt, model):
            captured_prompts.append(prompt)
            return [mock_intent_json, mock_task_spec][len(captured_prompts) - 1]

        with patch.object(planner_agent, "_call_llm", side_effect=capture_and_return):
            planner_agent.generate_task_specification(
                analyzer_context=sample_analyzer_context,
                user_request=sample_user_request,
                model="claude",
            )

        # Chain 1 프롬프트에 사용자 요청이 포함되어야 함
        assert sample_user_request in captured_prompts[0]

    def test_chain2_receives_intent_document(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec
    ):
        """Chain 2 프롬프트에 Chain 1의 출력(intent_document)이 포함되는지 확인"""
        captured_prompts = []

        def capture_and_return(prompt, model):
            captured_prompts.append(prompt)
            return [mock_intent_json, mock_task_spec][len(captured_prompts) - 1]

        with patch.object(planner_agent, "_call_llm", side_effect=capture_and_return):
            planner_agent.generate_task_specification(
                analyzer_context=sample_analyzer_context,
                user_request=sample_user_request,
                model="claude",
            )

        # Chain 2 프롬프트에 Chain 1 결과가 전달되어야 함
        assert "MODIFY" in captured_prompts[1]  # intent_json 내용 확인

    def test_available_api_list_included_in_chain2(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec
    ):
        """available_api_list가 Chain 2 프롬프트에 포함되는지 확인"""
        api_list = ["ifc_modify_attribute(globalId, attr, value)", "ifc_get_hosted_elements(globalId)"]
        captured_prompts = []

        def capture_and_return(prompt, model):
            captured_prompts.append(prompt)
            return [mock_intent_json, mock_task_spec][len(captured_prompts) - 1]

        with patch.object(planner_agent, "_call_llm", side_effect=capture_and_return):
            planner_agent.generate_task_specification(
                analyzer_context=sample_analyzer_context,
                user_request=sample_user_request,
                model="claude",
                available_api_list=api_list,
            )

        assert "ifc_modify_attribute" in captured_prompts[1]

    def test_raises_on_code_in_output(
        self, sample_analyzer_context, sample_user_request, mock_intent_json
    ):
        """LLM이 코드를 포함한 응답을 반환하면 ValueError 발생"""
        bad_spec = "Step 1: import ifcopenshell\nmodel = ifcopenshell.open('test.ifc')"
        with patch.object(
            planner_agent, "_call_llm",
            side_effect=[mock_intent_json, bad_spec]
        ):
            with pytest.raises(ValueError, match="코드 패턴"):
                planner_agent.generate_task_specification(
                    analyzer_context=sample_analyzer_context,
                    user_request=sample_user_request,
                    model="claude",
                )

    def test_unsupported_model_raises(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec
    ):
        """지원하지 않는 모델 명은 ValueError를 발생시켜야 함"""
        with patch.object(planner_agent, "_load_prompt", return_value="prompt <<analyzer_context>> <<user_request>>"):
            with pytest.raises(ValueError, match="지원하지 않는 모델"):
                planner_agent.generate_task_specification(
                    analyzer_context=sample_analyzer_context,
                    user_request=sample_user_request,
                    model="unsupported_model_xyz",
                )

    def test_multiple_targets_in_context(
        self, sample_user_request, mock_intent_json, mock_task_spec
    ):
        """복수 객체 컨텍스트도 정상 처리되는지 확인"""
        multi_target_context = {
            "targets": [
                {"globalId": "AAA111", "ifcClass": "IfcDoor", "name": "Door 01",
                 "attributes": {"Height": 2100}, "relationships": {}, "properties_json": "{}"},
                {"globalId": "BBB222", "ifcClass": "IfcDoor", "name": "Door 02",
                 "attributes": {"Height": 2100}, "relationships": {}, "properties_json": "{}"},
            ],
            "query_summary": "2층 복도의 문 2개 식별",
            "cypher_used": "MATCH (d:IfcDoor) RETURN d LIMIT 2",
        }
        with patch.object(
            planner_agent, "_call_llm",
            side_effect=[mock_intent_json, mock_task_spec]
        ):
            result = planner_agent.generate_task_specification(
                analyzer_context=multi_target_context,
                user_request=sample_user_request,
                model="claude",
            )
        assert isinstance(result, str)


# ── run_planner 래퍼 테스트 ──────────────────────────────────────────────────

class TestRunPlanner:
    def test_run_planner_returns_same_as_generate(
        self, sample_analyzer_context, sample_user_request,
        mock_intent_json, mock_task_spec, capsys
    ):
        with patch.object(
            planner_agent, "_call_llm",
            side_effect=[mock_intent_json, mock_task_spec]
        ):
            result = planner_agent.run_planner(
                analyzer_context=sample_analyzer_context,
                user_request=sample_user_request,
                model="claude",
                verbose=True,
            )
        # verbose 모드: 콘솔에 출력되어야 함
        captured = capsys.readouterr()
        assert "TASK SPECIFICATION" in captured.out
        assert isinstance(result, str)
