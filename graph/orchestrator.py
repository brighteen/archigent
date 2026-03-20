"""
오케스트레이터 - LangGraph StateGraph로 4개 노드를 연결합니다.

그래프 흐름:
    START → analyzer → planner → coder → verifier
                                    ↑          |
                                    └──────────┘ (FAIL & iteration < MAX_RETRIES)
"""
import logging
from typing import Literal

from langgraph.graph import StateGraph, START, END

from .state import AgentState
from .nodes import (
    analyzer_node, planner_node, coder_node, verifier_node,
    selection_node, reviewer_node, rollback_node
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def _should_retry(state: AgentState) -> Literal["retry", "end"]:
    """검증 실패 시 롤백 후 재시도 여부 결정"""
    result = state.get("verification_result", "")
    iteration = state.get("iteration", 0)

    if result.startswith("PASS"):
        return "end"

    if iteration < MAX_RETRIES:
        return "retry"

    return "end"

def _review_check(state: AgentState) -> Literal["approved", "denied"]:
    """리뷰어 승인 여부에 따른 분기"""
    if state.get("iteration_success", False):
        return "approved"
    return "denied"


def build_graph(checkpointer=None) -> StateGraph:
    builder = StateGraph(AgentState)

    # 노드 등록
    builder.add_node("analyzer",  analyzer_node)
    builder.add_node("planner",   planner_node)
    builder.add_node("selection", selection_node)
    builder.add_node("coder",     coder_node)
    builder.add_node("reviewer",  reviewer_node)
    builder.add_node("verifier",  verifier_node)
    builder.add_node("rollback",  rollback_node)

    # 기본 엣지 연결
    builder.add_edge(START,       "analyzer")
    builder.add_edge("analyzer",  "planner")
    builder.add_edge("planner",   "selection")
    builder.add_edge("selection", "coder")
    builder.add_edge("coder",     "reviewer")
    
    # 리뷰어 체크 후 샌드박스로 실행하거나(이미 실행됨 여부 확인) 코더로 다시 보내기
    builder.add_conditional_edges(
        "reviewer",
        _review_check,
        {
            "approved": "verifier",
            "denied":   "coder"
        }
    )

    # 검증 후 롤백 및 재시도 판단
    builder.add_conditional_edges(
        "verifier",
        _should_retry,
        {
            "retry": "rollback",
            "end":   END
        }
    )
    
    builder.add_edge("rollback", "planner")

    # [중요] 시안 선택 단계에서 사용자 개입을 위해 인터럽트 설정
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["selection"]
    )
