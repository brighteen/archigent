"""
노드 함수 정의 - LangGraph의 각 노드는 (AgentState) -> AgentState 서명을 가집니다.
고도화된 Analyzer, Planner, Coder(Sandbox), Verifier 모듈을 통합합니다.
"""

import logging
import os
from pathlib import Path

from .state import AgentState
from analyzer_agent import AnalyzerAgent
from planner_agent import generate_task_specification, generate_task_specification_multi
from coder_agent import generate_ifc_code
from sandbox_executor import execute_in_sandbox, build_error_feedback
from langchain_core.runnables import RunnableConfig
from verifier_agent.verifier_agent import verify_modifications
from reviewer_agent import review_code

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 1. Analyzer Node
# ──────────────────────────────────────────────────────────────────────────────

def analyzer_node(state: AgentState, config: RunnableConfig) -> AgentState:
    """
    고도화된 AnalyzerAgent를 사용하여 IFC 그래프를 정밀 분석합니다.
    """
    logger.info("[Node: Analyzer] 시작")
    # Configurable에서 Client를 가져옵니다. (상태 직렬화 문제 해결)
    client = config.get("configurable", {}).get("neo4j_client")
    if not client:
        logger.error("[Node: Analyzer] Neo4jClient가 Config에 없습니다.")
        # fallback 또는 error 처리
    user_request = state.get("user_request", "")

    analyzer = AnalyzerAgent(neo4j_client=client)
    analysis_result = analyzer.analyze(user_request)

    logger.info(f"[Node: Analyzer] 완료 (타겟 {len(analysis_result['target_objects'])}개 발견)")
    
    return {
        **state,
        "graph_summary": analysis_result["markdown_summary"],
        "analyzer_context_json": analysis_result, # Planner가 사용할 원본 데이터
    }


# ──────────────────────────────────────────────────────────────────────────────
# 2. Planner Node
# ──────────────────────────────────────────────────────────────────────────────

def planner_node(state: AgentState) -> AgentState:
    """
    2단계 CoT Planner를 사용하여 상세 작업 명세를 생성합니다.
    HITL을 위해 여러 개의 시안을 생성합니다.
    """
    logger.info("[Node: Planner] 시작")
    analyzer_context = state.get("analyzer_context_json", {})
    user_request = state.get("user_request", "")

    # 여러 개의 시안 생성
    plan_options = generate_task_specification_multi(
        analyzer_context=analyzer_context,
        user_request=user_request,
        model="gemini-2.5-flash",
        num_options=3
    )

    logger.info(f"[Node: Planner] {len(plan_options)}개의 시안 생성 완료")
    return {
        **state,
        "plan_options": plan_options,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 2.5 Selection Node (HITL)
# ──────────────────────────────────────────────────────────────────────────────

def selection_node(state: AgentState) -> AgentState:
    """
    사용자가 선택한 시안을 상태에 반영합니다.
    (실제 중단 및 입력은 orchestrator/main.py에서 제어)
    """
    logger.info("[Node: Selection] 시작")
    idx = state.get("selected_option_index", 0)
    options = state.get("plan_options", [])
    
    if not options:
        logger.warning("[Node: Selection] 선택 가능한 시안이 없습니다.")
        return state

    selected = options[idx]
    logger.info(f"[Node: Selection] 시안 {idx}번 선택됨: {selected.get('title')}")

    # 인텐트 정보에서 작업 유형 추출 (QUERY 등)
    intent_json = selected.get("intent_json", "{}")
    operation_type = "MODIFY" # 기본값
    try:
        if isinstance(intent_json, str):
            import json
            clean_json = intent_json.replace("```json", "").replace("```", "").strip()
            intent_data = json.loads(clean_json)
            operation_type = intent_data.get("operation_type", "MODIFY")
    except:
        pass

    return {
        **state,
        "modification_plan": selected.get("task_spec"),
        "planner_intent_json": intent_json,
        "operation_type": operation_type,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3. Coder Node (Sandbox Executor + Self-Correction)
# ──────────────────────────────────────────────────────────────────────────────

def coder_node(state: AgentState) -> AgentState:
    """
    코드 생성 및 샌드박스 실행을 수행합니다.
    """
    logger.info(f"[Node: Coder] 시작 (iteration: {state.get('iteration', 0)})")

    task_spec = state.get("modification_plan", "")
    ifc_path = state.get("ifc_path", "")
    output_path = state.get("output_ifc_path")
    
    # 이전 시도의 에러 피드백이 있다면 가져옴
    error_feedback = state.get("last_coder_error", "")

    # 1. 코드 생성
    generated_code = generate_ifc_code(
        task_spec=task_spec,
        error_feedback=error_feedback
    )

    # 2. 샌드박스 실행
    # 조회(QUERY) 작업인 경우 출력 파일 생성을 기대하지 않음
    expect_file = (state.get("operation_type") != "QUERY")
    
    result = execute_in_sandbox(
        code_str=generated_code,
        input_ifc_path=ifc_path,
        output_ifc_path=output_path,
        expect_output_file=expect_file
    )

    if result.success:
        logger.info("[Node: Coder] 실행 성공")
        return {
            **state,
            "generated_code": generated_code,
            "code_output": result.stdout,
            "last_coder_error": "",
            "iteration_success": True
        }
    else:
        # 실패 시 에러 피드백 구성하여 상태에 저장 (상위 오케스트레이터가 재시도 결정)
        feedback = build_error_feedback(result, state.get("iteration", 0) + 1, 3)
        logger.warning(f"[Node: Coder] 실행 실패: {result.error_type}")
        return {
            **state,
            "generated_code": generated_code,
            "code_output": result.stderr,
            "last_coder_error": feedback,
            "iteration_success": False
        }


# ──────────────────────────────────────────────────────────────────────────────
# 4. Verifier Node
# ──────────────────────────────────────────────────────────────────────────────

def verifier_node(state: AgentState) -> AgentState:
    """
    룰 베이스 검증 모듈을 사용하여 수정 결과를 물리적으로 검증합니다.
    """
    logger.info("[Node: Verifier] 시작")
    
    # 1. Coder 실행 자체에서 에러가 났다면 즉시 FAIL
    if not state.get("iteration_success", False):
        return {
            **state,
            "verification_result": f"FAIL: Code Execution Error\n{state.get('last_coder_error')}"
        }

    # 1.5 조회(QUERY) 모드인 경우 검증 생략
    if state.get("operation_type") == "QUERY":
        logger.info("[Node: Verifier] 조회 작업이므로 검증을 생략하고 통과합니다.")
        return {**state, "verification_result": "PASS: Query operation completed."}

    original_ifc = state.get("ifc_path")
    modified_ifc = state.get("output_ifc_path")
    intent_json_str = state.get("planner_intent_json", "[]")

    # 2. Planner의 의도(JSON) 파싱
    try:
        # Planner의 intent_json은 보통 마크다운 백틱을 포함할 수 있으므로 정제 필요
        import json
        clean_json = intent_json_str.replace("```json", "").replace("```", "").strip()
        intent_data = json.loads(clean_json)
        
        # verifier_agent는 리스트 형태의 modification_plan을 기대함
        # intent_data의 구조에 따라 변환이 필요할 수 있음. 
        # 여기서는 intent_data 자체가 리스트이거나, 특정 키에 리스트가 있다고 가정.
        if isinstance(intent_data, dict) and "modifications" in intent_data:
            plan = intent_data["modifications"]
        elif isinstance(intent_data, list):
            plan = intent_data
        else:
            plan = [intent_data] # 단일 객체 대응
            
    except Exception as exc:
        logger.warning(f"[Node: Verifier] Intent JSON 파싱 실패: {exc}. 기본 파일 체크만 수행합니다.")
        plan = []

    # 3. 물리적 검증 수행 (ifcopenshell 기반)
    if not Path(modified_ifc).exists():
        return {**state, "verification_result": "FAIL: Output file not created."}

    if not plan:
        # 플랜이 없으면 파일 존재만으로 성공 간주 (혹은 최소한의 스키마 체크)
        return {**state, "verification_result": "PASS: File generated (Simple Check)."}

    # 고도화된 검증 실행
    verify_res = verify_modifications(
        original_ifc_path=original_ifc,
        modified_ifc_path=modified_ifc,
        modification_plan=plan
    )

    if verify_res["success"]:
        return {**state, "verification_result": "PASS: Rule-based verification successful."}
    else:
        return {
            **state,
            "verification_result": f"FAIL: {verify_res.get('reason', 'Unknown verification failure')}"
        }


# ──────────────────────────────────────────────────────────────────────────────
# 5. Reviewer Node
# ──────────────────────────────────────────────────────────────────────────────

def reviewer_node(state: AgentState) -> AgentState:
    """
    생성된 코드를 실행 전/후에 논리적으로 검토합니다.
    """
    logger.info("[Node: Reviewer] 시작")
    plan = state.get("modification_plan", "")
    code = state.get("generated_code", "")
    
    review_result = review_code(plan=plan, code=code, model_name="gemini-2.5-flash")
    logger.info(f"[Node: Reviewer] 결과: {review_result}")

    if review_result.startswith("APPROVED"):
        return {**state, "iteration_success": True}
    else:
        return {
            **state, 
            "iteration_success": False,
            "last_coder_error": f"Reviewer Rejected: {review_result}"
        }


# ──────────────────────────────────────────────────────────────────────────────
# 6. Rollback Node
# ──────────────────────────────────────────────────────────────────────────────

def rollback_node(state: AgentState) -> AgentState:
    """
    검증 실패 시 원본 백업 파일로 복원합니다.
    """
    logger.info("[Node: Rollback] 시작")
    original_ifc = state.get("original_ifc_path")
    target_ifc = state.get("ifc_path") # 혹은 output_ifc_path? 
    # main.py에서는 ifc_path를 input으로 씀. 
    # 하지만 coder는 ifc_path에서 읽어서 output_ifc_path에 씀.
    # 재시도 시에는 output_ifc_path를 다시 덮어쓰거나 ifc_path(원본)를 기준으로 다시 해야 함.
    
    import shutil
    if original_ifc and Path(original_ifc).exists():
        # output_ifc_path를 원본으로 되돌림 (다음 이터레이션에서 깨끗한 상태로 시작)
        shutil.copy(original_ifc, state.get("output_ifc_path"))
        logger.info(f"[Node: Rollback] {state.get('output_ifc_path')}를 원본으로 복구 완료")
    
    return {
        **state,
        "iteration": state.get("iteration", 0) + 1, # 재시도 횟수 증가
        "last_coder_error": "Verification failed, rolling back and retrying..."
    }
