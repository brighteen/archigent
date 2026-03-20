"""
AgentState - LangGraph 파이프라인 전체를 관통하는 상태 스키마
각 노드는 AgentState를 받아 필요한 필드를 추가/수정한 뒤 반환합니다.
"""
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────────────────
    user_request: str         # 사용자 자연어 요청 (예: "모든 벽의 이름을 조회해줘")
    ifc_path: str             # 원본 IFC 파일 경로
    output_ifc_path: str      # 수정된 IFC 저장 경로 (Coder 생성)

    # ── Analyzer ──────────────────────────────────────────────────────
    graph_summary: str        # Neo4j 조회 결과 + LLM 구조 분석 요약

    # ── Planner ───────────────────────────────────────────────────────
    modification_plan: str    # 단계별 수정 계획 (자연어 + 의사코드)

    # ── Coder ─────────────────────────────────────────────────────────
    generated_code: str       # ifcopenshell Python 코드
    code_output: str          # 코드 실행 stdout/stderr

    # ── Verifier ──────────────────────────────────────────────────────
    verification_result: str  # "PASS" | "FAIL: <사유>"

    # ── 공통 제어 ─────────────────────────────────────────────────────
    iteration: int            # 현재 재시도 횟수 (최대 MAX_RETRIES)
    error: Optional[str]      # 파이프라인 중 발생한 에러 메시지
    iteration_success: bool   # 현재 이터레이션의 성공 여부 (Coder 실행 결과 등)

    # ── 내부 데이터 (객체/JSON) ──────────────────────────────────────
    # neo4j_client: Any         # Neo4jClient 인스턴스 (Configurable을 통해 전달받도록 수정)
    query_results: List[Dict] # Analyzer가 수집한 Cypher 조회 원본 결과
    analyzer_context_json: Dict[str, Any] # AnalyzerAgent가 생성한 정밀 컨텍스트
    planner_intent_json: str  # Planner가 생성한 구조화된 의도 JSON 문자열
    last_coder_error: str     # Coder 실행 실패 시의 에러 피드백 (Traceback 포함)

    # ── Advanced Features (Rollback, HITL, Preference) ──────────────
    version_history: List[str]      # 파일 상태 이력 (경로 목록)
    original_ifc_path: str          # 최초 원본 파일 경로 (복구용)
    plan_options: List[Dict[str, Any]] # Planner가 제안한 다중 시안 리스트
    selected_option_index: int      # 사용자가 선택한 시안 인덱스
    preference_profile: Dict[str, Any] # 사용자의 주관적 선호도 가중치 프로필
