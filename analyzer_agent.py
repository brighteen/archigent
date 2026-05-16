"""
analyzer_agent.py - Stage 2: Analyzer Agent 핵심 모듈
======================================================

역할:
  사용자의 자연어 요청을 받아 Neo4j BIM 그래프에서 LLM이 동적으로
  생성한 Cypher 쿼리를 실행하고, 타겟 IFC 객체를 정밀하게 탐색합니다.
  결과는 Planner 에이전트가 즉시 활용 가능한 Context Summary로 반환합니다.
"""

from __future__ import annotations

import os
import json
import logging
import re
from textwrap import dedent
from typing import Any, Dict, List, Optional
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from db.neo4j_client import Neo4jClient
from rag_manager import rag_manager

logger = logging.getLogger(__name__)


# ======================================================================= #
#  BIM 그래프 DB 스키마 (Cypher 생성 프롬프트에 주입)                     #
# ======================================================================= #

_BIM_SCHEMA = dedent("""\
    ## Neo4j BIM 그래프 DB 스키마 (멀티유저 격리 모드)
    ### 노드 레이블 (중요)
    - 모든 요소는 다음 3가지 레이블을 가집니다: `:Element:<IfcClass>:<TaskLabel>`
    - 예: (e:Element:IfcWall:{task_label})
    - `IFCFile` 노드 또한 `{task_label}` 레이블을 가집니다.

    ### 속성 필터링
    - 모든 노드는 `taskId` 속성을 가집니다. 반드시 `WHERE e.taskId = $tid`를 사용하여 다른 사용자의 데이터를 침범하지 마세요.

    ### 주요 관계 (Relationships)
    - 모든 관계 또한 `taskId` 속성을 보유하고 있으며, 동일한 `{task_label}`을 가진 노드 사이에만 존재합니다.
    - (e1)-[:AGGREGATES]->(e2)
    - (e1)-[:CONNECTS_TO]->(e2)
    - (e1)-[:CONTAINED_IN]->(structure)
    - (e1)-[:ASSIGNED_TO]->(group)

    ### 중요 검색 규칙
    1. **레이블 활용**: 검색 시 반드시 `(e:Element:IfcWall:{task_label})`와 같이 작업 전용 레이블을 포함하여 검색 성능을 최적화하세요.
    2. **데이터 반환**: RETURN 절에 `e.globalId`, `e.ifcClass`, `e.name`, `e.propertiesJson`을 반드시 포함하세요.
    3. **기본 LIMIT**: 50
""")

_CYPHER_SYSTEM = dedent("""\
    당신은 Neo4j BIM/IFC 그래프 데이터베이스 전문가입니다.
    {schema}
    
    ## ⚠️ 에이전트의 역할 (필독 - 절대 금지 사항)
     - 당신은 LLM 기반의 BIM 데이터 조회 전문가입니다. 당신의 목표는 사용자의 요청을 수행하기 위해 필요한 **"기존(existing)" 요소**들을 Neo4j DB에서 찾아내는 것입니다.
     - **절대 금지: `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE` 구문은 절대로 사용하지 마세요.** 
     - 당신은 DB를 수정할 권한이 없으며, 오직 **조회(SELECT/MATCH)**만 가능합니다.
     - 사용자가 "새로 만들어줘", "추가해줘"라고 요청하더라도, 당신은 DB에서 **아직 존재하지 않는 새로운 요소를 생성하려 해서는 안 됩니다.** 대신, 새로운 요소를 배치할 위치 근처의 **기존 요소**나 수치 참고용 **기존 요소**를 검색하세요.
    
    ## ⚠️ 절대 지켜야 할 생성 규칙 (중요)
    1. **조회 전용(READ-ONLY)**: 오직 `MATCH`, `OPTIONAL MATCH`, `WITH`, `RETURN` 구문만 사용하세요.
    2. **출력 형식**: **오직 Cypher 쿼리 문자열 하나만** 출력하세요. 설명은 생략합니다.
    3. **데이터 반환 필수**: 모든 쿼리는 반드시 `RETURN` 절로 끝나야 하며, `e.globalId`, `e.ifcClass`, `e.name`, `e.propertiesJson`을 포함해야 합니다.
    4. **속성 필터링**: `propertiesJson`은 문자열 타입이므로 `CONTAINS`를 이용해 필터링하세요.

    ## ❌ 잘못된 예시 (BAD EXAMPLES - DO NOT DO THIS)
    - 잘못된 요청 응답 (DB 수정 시도): `MATCH (e:IfcWall) SET e.x = 100 RETURN e` (❌ SET 사용 금지)
    - 잘못된 요청 응답 (새 노드 생성): `CREATE (n:IfcWall {{name: 'New'}}) RETURN n` (❌ CREATE 사용 금지)
    - 잘못된 요청 응답 (복잡한 절차 기술): "쿼리를 생성하겠습니다: MATCH..." (❌ 서술형 금지)

    ## ✅ 올바른 예시 (Good Few-shot)
    - 요청: "모든 벽 정보를 찾아줘"
      출력: MATCH (e:`Element`:`IfcWall`:`{task_label}`) WHERE e.`taskId` = $tid RETURN e.`globalId`, e.`ifcClass`, e.`name`, e.`propertiesJson` LIMIT 50
    - 요청: "특정 객체(이름: 'Wall_01') 주변의 요소를 찾아줘"
      출력: MATCH (e:`Element`:`{task_label}` {{name: 'Wall_01'}})-[r]-(neighbor) WHERE e.`taskId` = $tid AND neighbor.`taskId` = $tid RETURN e.`globalId`, neighbor.`globalId`, neighbor.`ifcClass`, neighbor.`name`, neighbor.`propertiesJson`
    - 요청: "두께가 200인 벽 옆에 새 벽을 만들 수 있게 근처 객체들을 찾아줘"
      출력: MATCH (e:`Element`:`IfcWall`:`{task_label}`) WHERE e.`propertiesJson` CONTAINS '"Thickness": 200' AND e.`taskId` = $tid RETURN e.`globalId`, e.`ifcClass`, e.`name`, e.`propertiesJson` LIMIT 50
""")


class AnalyzerAgent:
    def __init__(
        self,
        neo4j_client: Neo4jClient,
        llm_model: str = "qwen",
        temperature: float = 0.0,
        mock_mode: bool = False,
    ):
        self.db = neo4j_client
        self.mock_mode = mock_mode
        self._llm = None
        self._prompt_template = None

        if not mock_mode:
            try:
                self._llm = ChatOpenAI(
                    model=os.getenv("LLM_MODEL_NAME", "qwen-30b"),
                    openai_api_key=os.getenv("OPENAI_API_KEY", "empty"),
                    base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1"),
                    temperature=temperature,
                    max_tokens=1024
                )
                # 스키마 주입은 analyze 시점에 task_id와 함께 수행
                self._prompt_template = ChatPromptTemplate.from_messages([
                    ("system", _CYPHER_SYSTEM),
                    ("user", "다음 요청에 맞는 Cypher 쿼리를 생성하세요.\n\n{query}"),
                ])
                logger.info(f"[AnalyzerAgent] LLM 초기화 완료: {llm_model}")
            except Exception as exc:
                logger.warning(f"[AnalyzerAgent] LLM 초기화 실패로 MOCK_MODE 전환: {exc}")
                self.mock_mode = True

    def _generate_cypher(self, user_query: str) -> str:
        if self.mock_mode or self._chain is None:
            return "MATCH (e:Element:IfcWall) RETURN e.globalId, e.ifcClass, e.name, e.propertiesJson LIMIT 10"
        return self._chain.invoke({"query": user_query})

    @staticmethod
    def _clean_cypher(raw: str) -> str:
        c = re.sub(r"```(?:cypher|sql)?\s*\n?", "", raw, flags=re.IGNORECASE)
        c = re.sub(r"```\s*", "", c).strip()
        return c

    def analyze(self, user_request: str, task_id: str) -> Dict[str, Any]:
        logger.info(f"[AnalyzerAgent] 분석 시작 (taskId: {task_id}): {user_request}")
        cypher = ""
        task_label = f"Task_{task_id.replace('-', '_')}"
        formatted_schema = _BIM_SCHEMA.format(task_label=task_label)
        
        # 2. Cypher 생성
        if self.mock_mode or self._llm is None:
            cypher = f"MATCH (e:Element:IfcWall:{task_label}) WHERE e.taskId = $tid RETURN e.globalId, e.ifcClass, e.name, e.propertiesJson LIMIT 10"
        else:
            # 7B 모델을 위한 더욱 강력한 제약사항 추가
            strict_query = f"{user_request}\n(중요: 반드시 MATCH로 시작하여 RETURN으로 끝나는 Cypher 쿼리만 출력하세요. 한글 설명이나 인사는 절대 포함하지 마세요.)"
            
            chain = self._prompt_template | self._llm | StrOutputParser()
            raw_cypher = chain.invoke({
                "schema": formatted_schema,
                "query": strict_query,
                "task_label": task_label
            })
            
            # 7B 모델 대응: MATCH/WITH/OPTIONAL MATCH 등으로 시작하는 부분만 정교하게 추출
            match_keywords = ["MATCH", "WITH", "OPTIONAL", "RETURN"]
            upper_raw = raw_cypher.upper()
            
            start_idx = -1
            for kw in match_keywords:
                idx = upper_raw.find(kw)
                if idx != -1 and (start_idx == -1 or idx < start_idx):
                    start_idx = idx
            
            if start_idx != -1:
                cypher = raw_cypher[start_idx:].strip()
            else:
                cypher = raw_cypher.strip()
            
            # 후행 설명 제거 (마지막 세미콜론이나 줄바꿈 이후 컷)
            cypher = cypher.split("\n")[0].split(";")[0].strip()
            # 백틱 및 마크다운 제거
            cypher = self._clean_cypher(cypher)

            # [보정] RETURN 절이 누락되었는지 확인 (7B 모델이 WITH 등에서 끊기는 경우 대응)
            if "RETURN " not in cypher.upper():
                logger.warning(f"[AnalyzerAgent] RETURN 절 누락 감지. 기본 RETURN을 추가합니다. 원본: {cypher}")
                # WITH로 끝나는 경우 그 변수들을 RETURN 하도록 유도, 아니면 기본 e 반환
                if "WITH " in cypher.upper():
                    # 마지막 WITH 절 뒤의 변수들을 추출하여 RETURN에 붙여주면 좋으나, 복잡하므로 
                    # 안전하게 전체 객체(e)를 다시 MATCH하여 반환하는 fallback 느낌으로 처리하거나
                    # 단순히 e가 정의되어 있다면 e를 반환. 
                    # 여기서는 가장 빈번한 (e:...) 패턴을 고려하여 e를 반환하도록 시도.
                    cypher += " RETURN e.`globalId`, e.`ifcClass`, e.`name`, e.`propertiesJson`"
                else:
                    cypher += " RETURN e.`globalId`, e.`ifcClass`, e.`name`, e.`propertiesJson`"

        # [보완] 생성된 쿼리 검증: READ-ONLY 여부 확인 (7B 모델 할루시네이션 방지)
        forbidden_keywords = ["CREATE ", "MERGE ", "SET ", "DELETE ", "REMOVE "]
        is_safe = all(kw not in cypher.upper() for kw in forbidden_keywords)
        
        if not is_safe:
            logger.warning(f"[AnalyzerAgent] 부적절한(비조회) 키워드 감지. 안전한 쿼리로 대체합니다. 원본: {cypher}")
            cypher = f"MATCH (e:Element:IfcWall:{task_label}) WHERE e.taskId = $tid RETURN e.globalId, e.ifcClass, e.name, e.propertiesJson LIMIT 10"

        logger.info(f"[AnalyzerAgent] 실행 쿼리: {cypher}")
        
        # RAG를 통해 관련 법규만 동기적으로 추출
        regulations = rag_manager.retrieve_regulations(user_request, top_k=2)
        if regulations and len(regulations) > 1000:
            regulations = regulations[:1000] + "...(truncated)"

        rows = []
        error = None
        try:
            # 쿼리에 taskId 파라미터 강제 주입 (보안 및 격리 강화)
            rows = self.db.query_elements(cypher, {"tid": task_id})
            
            # 7B 모델 대응: 만약 검색 결과가 0개라면, 'fallback'으로 모든 요소 일부를 가져와서 최소한의 가이드 제공
            if not rows or len(rows) == 0:
                logger.warning(f"[AnalyzerAgent] 검색 결과 0개. 모든 요소 중 일부를 fallback으로 조회합니다.")
                fallback_query = f"MATCH (e:Element:{task_label}) WHERE e.taskId = $tid RETURN e.globalId, e.ifcClass, e.name, e.propertiesJson LIMIT 5"
                rows = self.db.query_elements(fallback_query, {"tid": task_id})

        except Exception as e:
            rows = []
            error = str(e)

        # 3. 결과 요약
        summary = self.build_context_summary(user_request, cypher, rows, error)
        if regulations:
            summary["markdown_summary"] += f"\n\n## 📝 관련 법규 정보 (RAG)\n{regulations}"
        
        # 전체 markdown이 너무 길어지지 않게 함 (8192 모델 대응)
        if len(summary["markdown_summary"]) > 3000:
            summary["markdown_summary"] = summary["markdown_summary"][:3000] + "...(truncated)"
            
        return summary

    def build_context_summary(self, user_query: str, cypher: str, rows: List[Dict], error: str = None) -> Dict[str, Any]:
        count = len(rows)
        target_objects = [self._normalize_row(r) for r in rows]
        
        markdown_summary = dedent(f"""\
            ## 📊 Analyzer Agent Context Summary
            **사용자 요청:** {user_query}
            **실행 쿼리:** `{cypher}`
            **탐색 결과:** 총 {count}개 객체를 발견했습니다.
        """)

        return {
            "target_objects": target_objects,
            "markdown_summary": markdown_summary,
            "query_meta": {"matched_count": count, "status": "success" if not error else "error"},
            "analyzer_context_json": {"target_objects": target_objects} # compatibility with nodes.py
        }

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Neo4j 결과 레코드를 일관된 딕셔너리로 변환합니다."""
        # Helper to truncate properties string
        def _truncate_properties(props_str: str) -> str:
            if len(props_str) > 1000:
                return props_str[:1000] + "...(truncated)"
            return props_str

        # 1. 'e'라는 이름의 노드 자체가 반환된 경우 처리
        node = row.get("e")
        if node and hasattr(node, "get"):
            properties_json_str = node.get("propertiesJson", "{}")
            truncated_properties_json_str = _truncate_properties(properties_json_str)
            return {
                "globalId": node.get("globalId"),
                "ifc_class": node.get("ifcClass"),
                "name": node.get("name"),
                "properties_snapshot": json.loads(truncated_properties_json_str) if isinstance(truncated_properties_json_str, str) else truncated_properties_json_str
            }

        # 2. 개별 속성이 'e.globalId' 등의 키로 반환된 경우 처리
        properties_json_str = row.get("e.propertiesJson") or row.get("propertiesJson", "{}")
        truncated_properties_json_str = _truncate_properties(properties_json_str)
        return {
            "globalId": row.get("e.globalId") or row.get("globalId"),
            "ifc_class": row.get("e.ifcClass") or row.get("ifcClass"),
            "name": row.get("e.name") or row.get("name"),
            "properties_snapshot": json.loads(row.get("e.propertiesJson", "{}")) if isinstance(row.get("e.propertiesJson"), str) else row.get("e.propertiesJson", {})
        }

def _mock_rows() -> List[Dict[str, Any]]:
    return [
        {
            "e.globalId": "MOCK_ID_01",
            "e.ifcClass": "IfcWall",
            "e.name": "Mock Wall",
            "e.propertiesJson": json.dumps({"Pset_WallCommon": {"Width": 200}})
        }
    ]
