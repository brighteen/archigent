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
        task_id: str,
        clear_first: bool = False,
    ) -> dict:
        """
        IFC 파일을 파싱하여 Neo4j에 전부 삽입합니다.
        """
        ifc_path = Path(ifc_path)
        result = {
            "ifc_path": str(ifc_path),
            "taskId": task_id,
            "success": False,
            "elements_written": 0,
            "relationships_written": 0,
            "error": None,
        }

        try:
            # 1. DB 초기화 (해당 task_id 데이터만)
            if clear_first:
                self.logger.info(f"기존 DB 데이터 삭제 중 (taskId: {task_id})…")
                self.client.clear(task_id=task_id)

            # 2. IFC 파일 로드
            if not self.loader.load(ifc_path):
                result["error"] = "IFC 파일 로드 실패"
                return result

            # 3. 파일 메타 노드 생성
            ifc_file = self.loader.ifc_file
            file_id = self.client.upsert_file_node(ifc_path, task_id=task_id, schema=ifc_file.schema if ifc_file else "")

            # 4. 요소 삽입
            elements = self.loader.get_elements()
            for elem in elements:
                if self.client.upsert_element(elem, task_id, file_id):
                    result["elements_written"] += 1

            # 5. 관계 삽입
            relationships = self.loader.get_relationships()
            for rel in relationships:
                if self.client.upsert_relationship(rel, task_id):
                    result["relationships_written"] += 1

            result["success"] = True
        except Exception as exc:
            self.logger.error(f"초기화 중 오류 발생: {exc}")
            result["error"] = str(exc)
        return result
