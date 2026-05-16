"""
오케스트레이터 - LangGraph StateGraph로 4개 노드를 연결합니다.
"""
import logging
from typing import Literal

from langgraph.graph import StateGraph, START, END

from .state import AgentState
from .nodes import (
    starter_node, analyzer_node, planner_node, coder_node, verifier_node,
    reviewer_node, rollback_node, responder_node
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

def _should_retry(state: AgentState) -> Literal["retry", "end"]:
    result = state.get("verification_result", "")
    iteration = state.get("iteration", 0)
    if result.startswith("PASS"):
        return "end"
    if iteration < MAX_RETRIES:
        return "retry"
    return "end"

def _review_check(state: AgentState) -> Literal["approved", "denied", "give_up"]:
    if state.get("iteration_success", False):
        return "approved"
    current_iter = state.get("iteration", 0)
    if current_iter >= MAX_RETRIES:
        return "give_up"
    return "denied"

def build_graph(checkpointer=None) -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("starter",      starter_node)
    builder.add_node("analyzer",     analyzer_node)
    builder.add_node("planner",      planner_node)
    builder.add_node("coder",        coder_node)
    builder.add_node("reviewer",     reviewer_node)
    builder.add_node("verifier",     verifier_node)
    builder.add_node("rollback",     rollback_node)
    builder.add_node("responder",    responder_node)

    # 엔트리 포인트 설정
    builder.add_edge(START, "starter")
    builder.add_edge("starter", "analyzer")
    
    builder.add_edge("analyzer",      "planner")
    builder.add_edge("planner",       "coder")
    builder.add_edge("coder",         "reviewer")
    
    builder.add_conditional_edges(
        "reviewer",
        _review_check,
        {
            "approved": "verifier",
            "denied":   "coder",
            "give_up":  "verifier"
        }
    )
    builder.add_conditional_edges(
        "verifier",
        _should_retry,
        {
            "retry": "rollback",
            "end":   "responder"
        }
    )
    
    builder.add_edge("rollback", "planner")
    builder.add_edge("responder", END)

    return builder.compile(checkpointer=checkpointer)
