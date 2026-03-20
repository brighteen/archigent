"""
IFC 파일 파서 - BIM_graph_agent의 IFCParser를 기반으로 확장
IfcProduct 요소, 관계, PropertySet, 공간구조 정보를 추출합니다.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import ifcopenshell


class IFCLoader:
    """
    IFC 파일을 로드하고 Neo4j 삽입에 적합한 구조로 파싱합니다.

    사용 예:
        loader = IFCLoader()
        loader.load("path/to/file.ifc")
        elements = loader.get_elements()
        relationships = loader.get_relationships()
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._ifc_file: Optional[ifcopenshell.file] = None
        self._file_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def load(self, file_path: str | Path) -> bool:
        """IFC 파일을 열고 내부에 캐싱합니다."""
        self._file_path = Path(file_path)
        try:
            self.logger.info(f"IFC 파일 로드 시작: {self._file_path}")
            self._ifc_file = ifcopenshell.open(str(self._file_path))
            self.logger.info(
                f"IFC 파일 로드 완료: schema={self._ifc_file.schema}, "
                f"elements={len(list(self._ifc_file.by_type('IfcProduct')))}"
            )
            return True
        except Exception as exc:
            self.logger.error(f"IFC 파일 로드 실패: {exc}")
            return False

    @property
    def ifc_file(self) -> Optional[ifcopenshell.file]:
        return self._ifc_file

    def get_file_meta(self) -> Dict[str, Any]:
        """파일 메타데이터를 반환합니다."""
        if not self._file_path or not self._file_path.exists():
            return {}
        stat = self._file_path.stat()
        return {
            "fileName": self._file_path.name,
            "filePath": str(self._file_path.absolute()),
            "schema": self._ifc_file.schema if self._ifc_file else "unknown",
            "fileSize": stat.st_size,
        }

    def get_elements(self) -> List[Dict[str, Any]]:
        """모든 IfcProduct 요소를 딕셔너리 리스트로 반환합니다."""
        if not self._ifc_file:
            return []

        elements: List[Dict[str, Any]] = []
        for element in self._ifc_file.by_type("IfcProduct"):
            try:
                data = self._extract_element(element)
                if data:
                    elements.append(data)
            except Exception as exc:
                self.logger.warning(f"요소 추출 실패 ({element}): {exc}")
        self.logger.info(f"요소 추출 완료: {len(elements)}개")
        return elements

    def get_relationships(self) -> List[Dict[str, Any]]:
        """모든 관계를 딕셔너리 리스트로 반환합니다."""
        if not self._ifc_file:
            return []

        rel_types = [
            "IfcRelAggregates",
            "IfcRelConnectsElements",
            "IfcRelDefinesByProperties",
            "IfcRelContainedInSpatialStructure",
            "IfcRelAssignsToGroup",
        ]

        relationships: List[Dict[str, Any]] = []
        for rel_type in rel_types:
            for rel in self._ifc_file.by_type(rel_type):
                try:
                    data = self._extract_relationship(rel)
                    if data:
                        relationships.append(data)
                except Exception as exc:
                    self.logger.warning(f"관계 추출 실패 ({rel}): {exc}")
        self.logger.info(f"관계 추출 완료: {len(relationships)}개")
        return relationships

    # ------------------------------------------------------------------
    # 내부 추출 메서드
    # ------------------------------------------------------------------

    def _extract_element(self, element) -> Dict[str, Any]:
        return {
            "globalId": element.GlobalId,
            "ifcClass": element.is_a(),
            "name": getattr(element, "Name", None) or "",
            "description": getattr(element, "Description", None) or "",
            "objectType": getattr(element, "ObjectType", None) or "",
            "tag": getattr(element, "Tag", None) or "",
            "properties": self._extract_property_sets(element),
            "storey": self._get_containing_storey(element),
        }

    def _extract_property_sets(self, element) -> Dict[str, Any]:
        """PropertySet을 중첩 딕셔너리로 추출합니다."""
        psets: Dict[str, Any] = {}
        try:
            for rel in getattr(element, "IsDefinedBy", []):
                if not rel.is_a("IfcRelDefinesByProperties"):
                    continue
                prop_def = rel.RelatingPropertyDefinition
                if not prop_def.is_a("IfcPropertySet"):
                    continue
                pset_name = prop_def.Name
                psets[pset_name] = {}
                for prop in prop_def.HasProperties:
                    if hasattr(prop, "Name") and hasattr(prop, "NominalValue"):
                        val = getattr(prop.NominalValue, "wrappedValue", str(prop.NominalValue))
                        psets[pset_name][prop.Name] = val
        except Exception as exc:
            self.logger.debug(f"PropertySet 추출 경고: {exc}")
        return psets

    def _get_containing_storey(self, element) -> Optional[str]:
        """요소가 속한 IfcBuildingStorey 이름을 반환합니다."""
        try:
            for rel in getattr(element, "ContainedInStructure", []):
                structure = rel.RelatingStructure
                if structure.is_a("IfcBuildingStorey"):
                    return getattr(structure, "Name", None)
        except Exception:
            pass
        return None

    def _extract_relationship(self, rel) -> Optional[Dict[str, Any]]:
        rel_type = rel.is_a()
        if rel_type == "IfcRelAggregates":
            return {
                "type": "AGGREGATES",
                "globalId": rel.GlobalId,
                "from_element": rel.RelatingObject.GlobalId if rel.RelatingObject else None,
                "to_elements": [obj.GlobalId for obj in rel.RelatedObjects if hasattr(obj, "GlobalId")],
            }
        elif rel_type == "IfcRelConnectsElements":
            return {
                "type": "CONNECTS_TO",
                "globalId": rel.GlobalId,
                "from_element": rel.RelatingElement.GlobalId if rel.RelatingElement else None,
                "to_element": rel.RelatedElement.GlobalId if rel.RelatedElement else None,
            }
        elif rel_type == "IfcRelDefinesByProperties":
            return {
                "type": "HAS_PROPERTY",
                "globalId": rel.GlobalId,
                "from_elements": [obj.GlobalId for obj in rel.RelatedObjects if hasattr(obj, "GlobalId")],
                "to_property": (
                    rel.RelatingPropertyDefinition.GlobalId
                    if hasattr(rel.RelatingPropertyDefinition, "GlobalId")
                    else None
                ),
            }
        elif rel_type == "IfcRelContainedInSpatialStructure":
            return {
                "type": "CONTAINED_IN",
                "globalId": rel.GlobalId,
                "from_elements": [obj.GlobalId for obj in rel.RelatedElements if hasattr(obj, "GlobalId")],
                "to_structure": rel.RelatingStructure.GlobalId if rel.RelatingStructure else None,
            }
        elif rel_type == "IfcRelAssignsToGroup":
            return {
                "type": "ASSIGNED_TO",
                "globalId": rel.GlobalId,
                "from_elements": [obj.GlobalId for obj in rel.RelatedObjects if hasattr(obj, "GlobalId")],
                "to_group": rel.RelatingGroup.GlobalId if rel.RelatingGroup else None,
            }
        return None
