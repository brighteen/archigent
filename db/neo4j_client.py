"""
Neo4j 클라이언트 - BIM_graph_agent의 Neo4jDatabase를 기반으로 확장
IFC 요소/관계를 MERGE 방식으로 삽입하고 조회합니다.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase


class Neo4jClient:
    """
    Neo4j 드라이버 래퍼. IFC 요소를 노드/관계로 삽입합니다.

    사용 예:
        client = Neo4jClient(uri, user, password, database)
        client.connect()
        client.upsert_file_node(Path("file.ifc"), schema="IFC4")
        client.upsert_element(element_data, file_id)
        client.upsert_relationship(rel_data)
        client.close()
    """

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self.logger = logging.getLogger(__name__)
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.driver = None

    # ------------------------------------------------------------------
    # 연결 관리
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            with self.driver.session(database=self.database) as s:
                s.run("RETURN 1")
            self.logger.info(f"Neo4j 연결 성공: {self.uri} / DB={self.database}")
            return True
        except Exception as exc:
            self.logger.error(f"Neo4j 연결 실패: {exc}")
            return False

    def close(self):
        if self.driver:
            self.driver.close()
            self.logger.info("Neo4j 연결 종료")

    # ------------------------------------------------------------------
    # DB 관리
    # ------------------------------------------------------------------

    def clear(self, task_id: Optional[str] = None) -> bool:
        """데이터베이스 초기화. task_id가 있으면 해당 작업 데이터만 삭제."""
        try:
            with self.driver.session(database=self.database) as s:
                if task_id:
                    s.run("MATCH (n) WHERE n.taskId = $tid DETACH DELETE n", tid=task_id)
                    self.logger.info(f"Neo4j DB 초기화 완료 (taskId: {task_id})")
                else:
                    s.run("MATCH (n) DETACH DELETE n")
                    self.logger.info("Neo4j DB 전체 초기화 완료")
            return True
        except Exception as exc:
            self.logger.error(f"DB 초기화 실패: {exc}")
            return False

    def get_stats(self) -> Dict[str, int]:
        try:
            with self.driver.session(database=self.database) as s:
                nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
                rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            return {"total_nodes": nodes, "total_relationships": rels}
        except Exception as exc:
            self.logger.error(f"통계 조회 실패: {exc}")
            return {"total_nodes": 0, "total_relationships": 0}

    # ------------------------------------------------------------------
    # 노드 삽입
    # ------------------------------------------------------------------

    def upsert_file_node(self, file_path: Path, task_id: str, schema: str = "") -> Optional[str]:
        """IFC 파일 메타데이터 노드를 생성(또는 갱신)합니다."""
        try:
            stat = file_path.stat()
            file_id = f"FILE_{file_path.stem}_{int(stat.st_mtime)}"
            props = {
                "fileId": file_id,
                "taskId": task_id,
                "fileName": file_path.name,
                "filePath": str(file_path.absolute()),
                "fileSize": stat.st_size,
                "schema": schema,
                "importedAt": datetime.now().isoformat(),
            }
            with self.driver.session(database=self.database) as s:
                s.execute_write(self._tx_upsert_file, file_id, task_id, props)
            return file_id
        except Exception as exc:
            self.logger.error(f"파일 노드 생성 실패: {exc}")
            return None

    @staticmethod
    def _tx_upsert_file(tx, file_id: str, task_id: str, props: dict):
        tx.run(
            "MERGE (f:IFCFile {fileId: $fid, taskId: $tid}) SET f += $props",
            fid=file_id,
            tid=task_id,
            props=props,
        )

    def upsert_element(self, element: Dict[str, Any], task_id: str, file_id: Optional[str] = None) -> bool:
        """IFC 요소(IfcProduct)를 Neo4j 노드로 삽입합니다."""
        try:
            with self.driver.session(database=self.database) as s:
                s.execute_write(self._tx_upsert_element, element, task_id, file_id)
            return True
        except Exception as exc:
            self.logger.error(f"요소 삽입 실패 ({element.get('globalId')}): {exc}")
            return False

    @staticmethod
    def _tx_upsert_element(tx, element: Dict[str, Any], task_id: str, file_id: Optional[str]):
        gid = element["globalId"]
        ifc_class = element["ifcClass"]
        props = {
            "globalId": gid,
            "taskId": task_id,
            "ifcClass": ifc_class,
            "name": element.get("name", ""),
            "description": element.get("description", ""),
            "objectType": element.get("objectType", ""),
            "tag": element.get("tag", ""),
            "storey": element.get("storey") or "",
        }
        if file_id:
            props["sourceFileId"] = file_id
        if element.get("properties"):
            props["propertiesJson"] = json.dumps(element["properties"], ensure_ascii=False)

        # 동적 레이블 (Element + IFC 클래스명 + TaskID 레이블)
        # task_id가 하이픈(-)을 포함할 수 있으므로 안전하게 처리
        task_label = f"Task_{task_id.replace('-', '_')}"
        labels = f":Element:{ifc_class}:{task_label}"
        query = f"MERGE (e{labels} {{globalId: $gid, taskId: $tid}}) SET e += $props"
        if file_id:
            query += """
            WITH e
            MATCH (f:IFCFile {fileId: $fid, taskId: $tid})
            MERGE (e)-[:BELONGS_TO_FILE]->(f)
            """
        tx.run(query, gid=gid, tid=task_id, props=props, fid=file_id)

    # ------------------------------------------------------------------
    # 관계 삽입
    # ------------------------------------------------------------------

    def upsert_relationship(self, rel: Dict[str, Any], task_id: str) -> bool:
        """IFC 관계를 Neo4j 관계로 삽입합니다."""
        try:
            rel_type = rel["type"]
            with self.driver.session(database=self.database) as s:
                if rel_type == "AGGREGATES":
                    s.execute_write(self._tx_aggregates, rel, task_id)
                elif rel_type == "CONNECTS_TO":
                    s.execute_write(self._tx_connects, rel, task_id)
                elif rel_type == "CONTAINED_IN":
                    s.execute_write(self._tx_contained_in, rel, task_id)
                elif rel_type == "ASSIGNED_TO":
                    s.execute_write(self._tx_assigned_to, rel, task_id)
            return True
        except Exception as exc:
            self.logger.error(f"관계 삽입 실패 ({rel.get('type')} / taskId: {task_id}): {exc}")
            return False

    @staticmethod
    def _tx_aggregates(tx, rel: dict, task_id: str):
        from_id = rel.get("from_element")
        for to_id in rel.get("to_elements", []):
            tx.run(
                "MATCH (a:Element {globalId:$f, taskId: $tid}) MATCH (b:Element {globalId:$t, taskId: $tid})"
                " MERGE (a)-[r:AGGREGATES]->(b) SET r.relId=$rid, r.taskId=$tid",
                f=from_id, t=to_id, rid=rel["globalId"], tid=task_id
            )

    @staticmethod
    def _tx_connects(tx, rel: dict, task_id: str):
        tx.run(
            "MATCH (a:Element {globalId:$f, taskId: $tid}) MATCH (b:Element {globalId:$t, taskId: $tid})"
            " MERGE (a)-[r:CONNECTS_TO]->(b) SET r.relId=$rid, r.taskId=$tid",
            f=rel.get("from_element"), t=rel.get("to_element"), rid=rel["globalId"], tid=task_id
        )

    @staticmethod
    def _tx_contained_in(tx, rel: dict, task_id: str):
        to_id = rel.get("to_structure")
        for from_id in rel.get("from_elements", []):
            tx.run(
                "MATCH (a:Element {globalId:$f, taskId: $tid}) MATCH (b:Element {globalId:$t, taskId: $tid})"
                " MERGE (a)-[r:CONTAINED_IN]->(b) SET r.relId=$rid, r.taskId=$tid",
                f=from_id, t=to_id, rid=rel["globalId"], tid=task_id
            )

    @staticmethod
    def _tx_assigned_to(tx, rel: dict, task_id: str):
        to_id = rel.get("to_group")
        for from_id in rel.get("from_elements", []):
            tx.run(
                "MATCH (a:Element {globalId:$f, taskId: $tid}) MATCH (b:Element {globalId:$t, taskId: $tid})"
                " MERGE (a)-[r:ASSIGNED_TO]->(b) SET r.relId=$rid, r.taskId=$tid",
                f=from_id, t=to_id, rid=rel["globalId"], tid=task_id
            )

    # ------------------------------------------------------------------
    # 조회 API (에이전트 노드에서 사용)
    # ------------------------------------------------------------------

    def query_elements(self, cypher: str, params: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """임의 Cypher 쿼리를 실행하고 결과를 딕셔너리 리스트로 반환합니다."""
        try:
            with self.driver.session(database=self.database) as s:
                result = s.run(cypher, params or {})
                return [dict(record) for record in result]
        except Exception as exc:
            msg = str(exc)
            if "expected" in msg and len(msg) > 300:
                # Neo4j SyntaxError의 'expected' 리스트가 너무 길어 로그를 어지럽히는 것을 방지
                msg = msg.split("expected")[0] + "expected ... (truncated)"
            self.logger.error(f"쿼리 실행 실패: {msg}")
            return []

    def get_element_by_global_id(self, global_id: str) -> Optional[Dict[str, Any]]:
        """globalId로 단일 요소를 조회합니다."""
        rows = self.query_elements(
            "MATCH (e:Element {globalId: $gid}) RETURN e", {"gid": global_id}
        )
        return rows[0] if rows else None

    def get_elements_by_class(self, ifc_class: str, limit: int = 100) -> List[Dict[str, Any]]:
        """IFC 클래스명으로 요소 목록을 조회합니다."""
        return self.query_elements(
            "MATCH (e:Element {ifcClass: $cls}) RETURN e LIMIT $lim",
            {"cls": ifc_class, "lim": limit},
        )
