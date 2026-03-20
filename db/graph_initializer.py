"""
GraphInitializer - IFC 파일을 읽어 Neo4j DB를 초기화합니다.
IFCLoader + Neo4jClient를 조합하는 퍼사드(Facade) 클래스입니다.
"""
import logging
from pathlib import Path
from typing import Optional

from .ifc_loader import IFCLoader
from .neo4j_client import Neo4jClient


class GraphInitializer:
    """
    단일 IFC 파일을 Neo4j 그래프 DB로 완전 초기화합니다.

    사용 예:
        client = Neo4jClient(...)
        client.connect()
        init = GraphInitializer(client)
        init.initialize("path/to/file.ifc", clear_first=True)
    """

    def __init__(self, neo4j_client: Neo4jClient):
        self.logger = logging.getLogger(__name__)
        self.client = neo4j_client
        self.loader = IFCLoader()

    def initialize(
        self,
        ifc_path: str | Path,
        clear_first: bool = False,
    ) -> dict:
        """
        IFC 파일을 파싱하여 Neo4j에 전부 삽입합니다.

        Args:
            ifc_path: IFC 파일 경로
            clear_first: True이면 기존 DB를 모두 삭제한 뒤 삽입

        Returns:
            실행 결과 요약 딕셔너리
        """
        ifc_path = Path(ifc_path)
        result = {
            "ifc_path": str(ifc_path),
            "success": False,
            "elements_written": 0,
            "relationships_written": 0,
            "error": None,
        }

        try:
            # 1. DB 초기화 (선택)
            if clear_first:
                self.logger.info("기존 DB 데이터 삭제 중…")
                self.client.clear()

            # 2. IFC 파일 로드
            self.logger.info(f"IFC 로드 시작: {ifc_path}")
            if not self.loader.load(ifc_path):
                result["error"] = "IFC 파일 로드 실패"
                return result

            # 3. 파일 메타 노드 생성
            ifc_file = self.loader.ifc_file
            file_id = self.client.upsert_file_node(
                ifc_path,
                schema=ifc_file.schema if ifc_file else "",
            )
            if not file_id:
                result["error"] = "파일 메타 노드 생성 실패"
                return result
            self.logger.info(f"파일 노드 생성 완료: {file_id}")

            # 4. 요소(IfcProduct) 삽입
            elements = self.loader.get_elements()
            elem_ok = 0
            for elem in elements:
                if self.client.upsert_element(elem, file_id):
                    elem_ok += 1
            self.logger.info(f"요소 삽입 완료: {elem_ok}/{len(elements)}")
            result["elements_written"] = elem_ok

            # 5. 관계 삽입
            relationships = self.loader.get_relationships()
            rel_ok = 0
            for rel in relationships:
                if self.client.upsert_relationship(rel):
                    rel_ok += 1
            self.logger.info(f"관계 삽입 완료: {rel_ok}/{len(relationships)}")
            result["relationships_written"] = rel_ok

            # 6. 통계
            stats = self.client.get_stats()
            result["db_stats"] = stats
            result["success"] = True
            self.logger.info(f"DB 초기화 완료 — {stats}")

        except Exception as exc:
            self.logger.error(f"초기화 중 오류 발생: {exc}")
            result["error"] = str(exc)

        return result
