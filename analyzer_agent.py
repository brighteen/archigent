"""
analyzer_agent.py - Stage 2: Analyzer Agent 핵심 모듈
======================================================

역할:
  사용자의 자연어 요청을 받아 Neo4j BIM 그래프에서 LLM이 동적으로
  생성한 Cypher 쿼리를 실행하고, 타겟 IFC 객체를 정밀하게 탐색합니다.
  결과는 Planner 에이전트가 즉시 활용 가능한 Context Summary로 반환합니다.
"""

from __future__ import annotations

import json
import logging
import re
from textwrap import dedent
from typing import Any, Dict, List, Optional
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from db.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


# ======================================================================= #
#  BIM 그래프 DB 스키마 (Cypher 생성 프롬프트에 주입)                     #
# ======================================================================= #

_BIM_SCHEMA = dedent("""\
    ## Neo4j BIM 그래프 DB 스키마
    ### 노드 레이블 (IFC 클래스별 독립 레이블)
    - 모든 IFC 요소: `:Element:<IfcClass>` 두 레이블 동시 보유
      예: (e:Element:IfcDoor), (e:Element:IfcWall)
    - IFCFile : IFC 파일 메타데이터
    주요 IFC 클래스:
      IfcSite, IfcBuilding, IfcBuildingStorey, IfcSpace
      IfcWall, IfcWallStandardCase (Wall 검색 시 필수 포함), IfcDoor, IfcWindow
      IfcSlab, IfcBeam, IfcColumn, IfcRoof, IfcStair
      IfcRailing, IfcOpeningElement, IfcFurnishingElement

    ### 공통 노드 속성
    - globalId    (str) : IFC 전역 고유 ID (타겟 특정의 핵심 키)
    - ifcClass    (str) : IFC 클래스명 (예: "IfcDoor", "IfcWallStandardCase")
    - name        (str) : 요소 이름
    - storey      (str) : 직접 저장된 층 이름 (있을 경우)
    - propertiesJson (str) : JSON 직렬화된 PropertySet 전체 덤프

    ### 핵심 쿼리 규칙
    1. 레이블은 반드시 `:Element` + IFC 클래스 동시 사용
       - 예: 벽(Wall) 검색 시 MATCH (e:Element) WHERE e.ifcClass IN ['IfcWall', 'IfcWallStandardCase'] 패턴 권장
    2. 반드시 RETURN 절에 e.globalId, e.ifcClass, e.name, e.propertiesJson 포함
    3. 공간/층 관계는 OPTIONAL MATCH 사용
    4. propertiesJson은 JSON 문자열이므로 CONTAINS로 필터링
    5. 기본 LIMIT 50
""")

_CYPHER_SYSTEM = dedent("""\
    당신은 Neo4j BIM/IFC 그래프 데이터베이스 전문가입니다.
    {schema}
    ## 생성 규칙
    - Cypher 쿼리 외 다른 텍스트 설명, 마크다운 백틱 등을 출력하지 마세요.
    - RETURN 절에 반드시 globalId, ifcClass, name, propertiesJson을 포함하세요.
    - 출력은 반드시 MATCH 또는 OPTIONAL로 시작하는 단일 Cypher 쿼리여야 합니다.
""")


class AnalyzerAgent:
    def __init__(
        self,
        neo4j_client: Neo4jClient,
        llm_model: str = "gemini-2.5-flash",
        temperature: float = 0.0,
        mock_mode: bool = False,
    ):
        self.db = neo4j_client
        self.mock_mode = mock_mode
        self._chain = None

        if not mock_mode:
            try:
                llm = ChatGoogleGenerativeAI(model=llm_model, temperature=temperature)
                prompt = ChatPromptTemplate.from_messages([
                    ("system", _CYPHER_SYSTEM.format(schema=_BIM_SCHEMA)),
                    ("user", "다음 요청에 맞는 Cypher 쿼리를 생성하세요.\n\n{query}"),
                ])
                self._chain = prompt | llm | StrOutputParser()
                logger.info(f"[AnalyzerAgent] LLM 체인 초기화 완료: {llm_model}")
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

    def analyze(self, user_request: str) -> Dict[str, Any]:
        logger.info(f"[AnalyzerAgent] 분석 시작: {user_request}")
        
        # 1. 법규 정보 로드
        regulations = self._load_regulations()

        # 2. Cypher 생성 및 실행
        raw_cypher = self._generate_cypher(user_request)
        cypher = self._clean_cypher(raw_cypher)
        
        if self.mock_mode:
            rows = _mock_rows()
            error = None
        else:
            try:
                rows = self.db.query_elements(cypher)
                error = None
            except Exception as e:
                rows = []
                error = str(e)

        # 3. 결과 요약
        summary = self.build_context_summary(user_request, cypher, rows, error)
        if regulations:
            summary["markdown_summary"] += f"\n\n## 📝 관련 법규 정보\n{regulations}"
        
        return summary

    def _load_regulations(self) -> str:
        reg_dir = Path("regulations")
        reg_text = []
        if reg_dir.exists():
            for f in reg_dir.glob("*.md"):
                try:
                    content = f.read_text(encoding='utf-8')
                    reg_text.append(f"### 법규: {f.name}\n{content}")
                except:
                    pass
        return "\n\n".join(reg_text)

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
        # handles row with e.globalId etc or flat dictionary
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
