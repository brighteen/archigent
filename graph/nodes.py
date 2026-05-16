"""
노드 함수 정의 - LangGraph의 각 노드는 (AgentState) -> AgentState 서명을 가집니다.
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .state import AgentState

ROOT = Path(__file__).parent.parent
from analyzer_agent import AnalyzerAgent
from planner_agent import generate_task_specification
from coder_agent import generate_ifc_code
from deterministic_executor import execute_in_deterministic_engine, build_error_feedback
from langchain_core.runnables import RunnableConfig
from verifier_agent.verifier_agent import verify_modifications
from reviewer_agent import review_code
import bim_util
import ifcopenshell

logger = logging.getLogger(__name__)

# ── 0. Starter Node ──────────────────────────────────────────────────────────

def starter_node(state: AgentState) -> AgentState:
    logger.info("[Node: Starter] 파이프라인 엔진 가동")
    return state

# ── 1. Analyzer Node ─────────────────────────────────────────────────────────

def analyzer_node(state: AgentState, config: RunnableConfig) -> AgentState:
    logger.info("[Node: Analyzer] 시작")
    conf = config.get("configurable", {})
    client = conf.get("neo4j_client")
    task_id = conf.get("thread_id", "default_task")
    
    if not client:
        logger.error("[Node: Analyzer] Neo4jClient가 Config에 없습니다.")

    user_request = state.get("user_request", "")
    analyzer = AnalyzerAgent(neo4j_client=client)
    analysis_result = analyzer.analyze(user_request, task_id=task_id)

    # [중요] 기하 정보 및 공간 위상 정보 풍부화 (Enrichment)
    # Neo4j에서 찾은 요소들에 대해 실제 IFC 파일에서 상세 좌표, 바운딩 박스, 공간 관계를 추출합니다.
    ifc_path = state.get("ifc_path")
    if ifc_path and Path(ifc_path).exists():
        try:
            model = ifcopenshell.open(ifc_path)
            
            # 1. 모델 전체의 공간 그래프(방-요소 관계) 추출
            analysis_result["spatial_graph"] = bim_util.extract_spatial_graph(model)
            
            # 2. 개별 타겟 요소의 상세 기하 정보(AABB 포함) 추출
            for obj in analysis_result.get("target_objects", []):
                gid = obj.get("globalId")
                element = model.by_guid(gid)
                if element:
                    # 기존 geometry_info (start/end points) - model 전달
                    geo_info = bim_util.get_element_geometry_info(element, model=model)
                    # 새 bounding_box (AABB) - model 전달
                    bbox = bim_util.extract_bounding_box(element, model=model)
                    
                    obj["geometry_info"] = geo_info
                    obj["bounding_box"] = bbox
                    
            logger.info(f"[Node: Analyzer] {len(analysis_result['target_objects'])}개 객체의 BBox 및 모델 공간 그래프 주입 완료")
        except Exception as e:
            logger.error(f"[Node: Analyzer] 기하/공간 데이터 주입 중 오류: {e}")

    logger.info(f"[Node: Analyzer] 완료 (타겟 {len(analysis_result['target_objects'])}개 발견)")
    
    return {
        **state,
        "graph_summary": analysis_result["markdown_summary"],
        "analyzer_context_json": analysis_result,
    }

# ── 2. Planner Node ──────────────────────────────────────────────────────────

def planner_node(state: AgentState) -> AgentState:
    logger.info("[Node: Planner] 시작")
    analyzer_context = state.get("analyzer_context_json", {})
    user_request = state.get("user_request", "")

    # 단일 시안 생성 후 리스트로 감싸서 반환 (Coder 호환성)
    plan = generate_task_specification(
        analyzer_context=analyzer_context,
        user_request=user_request,
        model="qwen"
    )
    
    logger.info(f"[Node: Planner] 단일 설계 시안 생성 완료")
    return {
        **state,
        "plan_options": [plan],
    }

# ── 3. Coder Node ────────────────────────────────────────────────────────────

def coder_node(state: AgentState) -> AgentState:
    logger.info("[Node: Coder] 시작")
    
    options = state.get("plan_options", [])
    if options and isinstance(options, list) and len(options) > 0:
        opt = options[0]
        task_spec = opt.get("task_spec", "")
        title = opt.get("title", "Proposed Modification")
    else:
        task_spec = state.get("user_request", "")
        title = "Direct Modification"

    ifc_path = state.get("ifc_path", "")
    ifc_name = Path(ifc_path).name
    task_id = state.get("task_id", "unknown")
    
    output_path = ROOT / "modified" / f"mod_{task_id[:8]}_{ifc_name}"
    (ROOT / "modified").mkdir(exist_ok=True)
    
    logger.info(f"[Node: Coder] 작업 실행 중: {title}")
    
    generated_code = generate_ifc_code(
        task_spec=task_spec,
        user_request=state.get("user_request", ""),
        error_feedback=state.get("last_coder_error", "")
    )
    
    result = execute_in_deterministic_engine(
        json_action_str=generated_code,
        input_ifc_path=ifc_path,
        output_ifc_path=str(output_path),
        expect_output_file=True
    )
    
    if not result.success:
        logger.warning(f"[Node: Coder] 1차 실행 실패. 재시도 중...")
        error_feedback = build_error_feedback(result, 1, 2)
        generated_code = generate_ifc_code(
            task_spec=task_spec,
            user_request=state.get("user_request", ""),
            error_feedback=error_feedback
        )
        result = execute_in_deterministic_engine(
            json_action_str=generated_code,
            input_ifc_path=ifc_path,
            output_ifc_path=str(output_path),
            expect_output_file=True
        )

    if result.success:
        logger.info(f"[Node: Coder] 실행 성공. JSON 파싱 결과:\n{generated_code[:500]}...")
        debug_path = Path(ROOT) / "logs" / f"last_executed_json_{task_id[:8]}.json"
        debug_path.parent.mkdir(exist_ok=True)
        debug_path.write_text(generated_code, encoding="utf-8")
        logger.info(f"[Node: Coder] 전체 JSON이 {debug_path}에 저장되었습니다.")
    
    return {
        **state,
        "output_ifc_path": str(output_path),
        "generated_code": generated_code,
        "code_output": result.stdout if result.success else result.stderr,
        "iteration_success": result.success,
        "iteration": state.get("iteration", 0) + 1,
        "last_coder_error": result.stderr if not result.success else ""
    }

# ── 4. Verifier Node ─────────────────────────────────────────────────────────

def verifier_node(state: AgentState) -> AgentState:
    logger.info("[Node: Verifier] 시작")
    
    if not state.get("iteration_success", False):
        return {
            **state,
            "verification_result": f"FAIL: Code Execution Error\n{state.get('last_coder_error')}"
        }

    original_ifc = state.get("ifc_path")
    modified_ifc = state.get("output_ifc_path")
    
    # plan은 plan_options[0]에서 가져옴
    options = state.get("plan_options", [])
    plan = options[0].get("task_spec", "") if options else state.get("user_request", "")

    if not Path(modified_ifc).exists():
        return {**state, "verification_result": "FAIL: Output file not created."}

    target_objects = state.get("analyzer_context_json", {}).get("target_objects", [])
    
    # 1. 샌드박스 출력물에서 [AUDIT] 태그로 생성된 GlobalId 파싱
    import re
    code_output = state.get("code_output", "")
    audit_match = re.search(r"\[AUDIT\] Created GlobalId: (\S+)", code_output)
    
    action = "modify"
    if audit_match:
        gid = audit_match.group(1)
        action = "create" # 라이브러리를 통해 생성이 확인됨
        logger.info(f"[Node: Verifier] 생성 확인됨 (AUDIT): {gid}")
    else:
        # 생성 확인이 안 되면 원본 분석 단계에서 찾은 첫 번째 대상을 수정 대상으로 간주
        gid = target_objects[0].get("globalId") if target_objects else None
        logger.info(f"[Node: Verifier] 수정 대상 확인 (Fallback): {gid}")
    
    verify_res = verify_modifications(
        original_ifc_path=original_ifc,
        modified_ifc_path=modified_ifc,
        modification_plan=[{"GlobalId": gid, "action": action, "description": plan}] 
    )

    if verify_res["success"]:
        return {**state, "verification_result": "PASS: Rule-based verification successful."}
    else:
        return {
            **state,
            "verification_result": f"FAIL: {verify_res.get('reason', 'Unknown verification failure')}"
        }

# ── 5. Reviewer Node ─────────────────────────────────────────────────────────

def reviewer_node(state: AgentState) -> AgentState:
    logger.info("[Node: Reviewer] 시작")
    iteration = state.get("iteration", 0)
    
    plan = state.get("plan_options", [{}])[0].get("task_spec", "") if state.get("plan_options") else ""
    code = state.get("generated_code", "")
    error = state.get("last_coder_error", "")
    
    review_result = review_code(plan=plan, code=code, execution_error=error)
    logger.info(f"[Node: Reviewer] 결과: {review_result}")

    if not review_result.startswith("APPROVED") or not state.get("iteration_success", False):
        return {
            **state, 
            "iteration_success": False,
            "last_coder_error": f"Reviewer Rejected or Execution Failed: {review_result}"
        }
    
    return {**state, "iteration_success": True}

# ── 6. Rollback Node ─────────────────────────────────────────────────────────

def rollback_node(state: AgentState) -> AgentState:
    logger.info("[Node: Rollback] 시작")
    original_ifc = state.get("original_ifc_path")
    output_path = state.get("output_ifc_path")
    
    if original_ifc and Path(original_ifc).exists() and output_path:
        shutil.copy(original_ifc, output_path)
        logger.info(f"[Node: Rollback] {output_path}를 원본으로 복구 완료")
    
    return {
        **state,
        "iteration": state.get("iteration", 0) + 1,
        "last_coder_error": "Verification failed, rolling back and retrying..."
    }

# ── 7. Responder Node ────────────────────────────────────────────────────────

def responder_node(state: AgentState) -> AgentState:
    logger.info("[Node: Responder] 대화형 최종 응답 생성 시작")
    from responder_agent import generate_conversational_response
    
    user_req = state.get("user_request", "")
    
    # Analyzer 정보
    ctx_json = state.get("analyzer_context_json", {})
    # analyzer_agent.py의 build_context_summary는 top-level에 query_summary를 두지 않고 
    # markdown_summary 등을 반환하므로, summary dict 전체에서 키를 찾습니다.
    # 만약 analyzer_context_json이 summary 객체라면:
    analyzer_summary = ctx_json.get("query_meta", {}).get("status", "분석 완료") 
    if "markdown_summary" in ctx_json:
        # 분석 요약을 더 구체적으로 추출
        analyzer_summary = ctx_json["markdown_summary"].split("\n")[2] if len(ctx_json["markdown_summary"].split("\n")) > 2 else "객체 분석 완료"
    
    # Planner 정보
    options = state.get("plan_options", [])
    plan_text = options[0].get("task_spec", "기본 작업") if options else "작업 명세"
    
    # Verifier 정보
    verification_result = state.get("verification_result", "")
    
    final_resp = generate_conversational_response(
        user_request=user_req,
        analyzer_summary=analyzer_summary,
        plan_text=plan_text,
        verification_result=verification_result
    )
    
    logger.info("[Node: Responder] 최종 응답 생성 완료")
    
    return {
        **state,
        "final_chat_response": final_resp
    }
