import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.unit
import ifcopenshell.util.placement
import numpy as np
import logging

logger = logging.getLogger(__name__)

def get_body_context(model):
    """IFC 모델에서 3D Body 기하 정보를 담을 컨텍스트를 찾습니다."""
    for context in model.by_type("IfcGeometricRepresentationContext"):
        if context.ContextType == "Model" and (context.ContextIdentifier == "Body" or context.ContextIdentifier == "MODEL_VIEW"):
            return context
    # 못 찾을 경우 가장 적절해 보이는 컨텍스트 반환
    contexts = model.by_type("IfcGeometricRepresentationContext")
    return contexts[0] if contexts else None

def get_wall_properties(wall):
    """벽의 두께(Thickness)와 높이(Height)를 Pset에서 추출합니다."""
    psets = ifcopenshell.util.element.get_psets(wall)
    res = {"thickness": 200.0, "height": 3000.0}
    # 다양한 Pset 명칭 대응
    for pset_name in ["Pset_WallCommon", "Basic", "Parameters"]:
        if pset_name in psets:
            p = psets[pset_name]
            if "Thickness" in p: res["thickness"] = float(p["Thickness"])
            if "Width" in p: res["thickness"] = float(p["Width"])
            if "Height" in p: res["height"] = float(p["Height"])
    return res

def translate_matrix(model, matrix, dx_mm=0.0, dy_mm=0.0, dz_mm=0.0):
    """
    밀리미터(mm) 단위로 4x4 행렬을 평행 이동시킵니다.
    내부적으로 IFC 모델의 고유 단위계를 파악하여(scale 변환) 올바른 거리만큼 이동시킵니다.
    """
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    # scale: 1 model unit이 몇 미터인지 나타냄.
    # 1 model unit = scale meters = (scale * 1000) mm
    # dx_model_unit = dx_mm / (scale * 1000)
    
    dx = dx_mm / (scale * 1000.0)
    dy = dy_mm / (scale * 1000.0)
    dz = dz_mm / (scale * 1000.0)
    
    new_matrix = matrix.copy()
    new_matrix[0, 3] += dx
    new_matrix[1, 3] += dy
    new_matrix[2, 3] += dz
    return new_matrix

def get_spatial_container(model, products=None):
    """
    제공된 products 중 첫 번째 객체가 속한 공간 컨테이너를 찾거나,
    모델 내의 기본 공간 컨테이너(Storey, Building, Site 순)를 반환합니다.
    """
    if products:
        for product in products:
            try:
                if hasattr(product, "ContainedInStructure") and product.ContainedInStructure:
                    return product.ContainedInStructure[0].RelatingStructure
            except:
                continue
    
    # 모델 전체에서 검색 (Storey -> Building -> Site 순으로 우선순위)
    for cls in ["IfcBuildingStorey", "IfcBuilding", "IfcSite"]:
        containers = model.by_type(cls)
        if containers:
            return containers[0]
    return None

def create_element(model, matrix, ifc_class="IfcWall", length=5000.0, height=3000.0, thickness=200.0, name="New Element", reference_element=None):
    """
    기하 정보(3D Representation)를 포함하여 새 요소를 생성하고 공간 구조에 할당합니다.
    
    Args:
        model: ifcopenshell.file 객체
        matrix: 4x4 numpy array (객체의 위치와 방향)
        ifc_class: 생성할 요소의 클래스명 (예: IfcWall, IfcWindow, IfcDoor, IfcSlab)
        length: 길이 (mm). 창문/문에서는 너비(width)로 사용.
        height: 높이 (mm)
        thickness: 두께/깊이 (mm)
        name: 이름
        reference_element: (선택) 위치와 공간 구조를 참고할 기존 IFC 요소. 
    """
    # 1. 기본 엔티티 생성
    element = ifcopenshell.api.run("root.create_entity", model, ifc_class=ifc_class)
    element.Name = name
    
    # 2. 배치(Placement) 설정
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    matrix_si = matrix.copy()
    matrix_si[0:3, 3] *= scale
    
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=element, matrix=matrix_si)
    if reference_element and hasattr(reference_element, "ObjectPlacement") and reference_element.ObjectPlacement:
        element.ObjectPlacement.PlacementRelTo = reference_element.ObjectPlacement.PlacementRelTo
    
    # 3. 3D 형상(Representation) 생성 및 할당
    context = get_body_context(model)
    if context:
        try:
            length_si = length / 1000.0
            height_si = height / 1000.0
            thickness_si = thickness / 1000.0
            
            representation = None
            if ifc_class == "IfcWall":
                representation = ifcopenshell.api.run("geometry.add_wall_representation", model, context=context, length=length_si, height=height_si, thickness=thickness_si)
            elif ifc_class == "IfcWindow" or ifc_class == "IfcWindowStandardCase":
                representation = ifcopenshell.api.run("geometry.add_window_representation", model, context=context, overall_height=height_si, overall_width=length_si)
            elif ifc_class == "IfcDoor" or ifc_class == "IfcDoorStandardCase":
                representation = ifcopenshell.api.run("geometry.add_door_representation", model, context=context, overall_height=height_si, overall_width=length_si)
            elif ifc_class == "IfcSlab":
                representation = ifcopenshell.api.run("geometry.add_slab_representation", model, context=context, depth=thickness_si)
            else:
                logger.warning(f"Using fallback wall geometry for {ifc_class}")
                representation = ifcopenshell.api.run("geometry.add_wall_representation", model, context=context, length=length_si, height=height_si, thickness=thickness_si)
            
            if representation:
                ifcopenshell.api.run("geometry.assign_representation", model, product=element, representation=representation)
            logger.info(f"Created {ifc_class} '{name}' with 3D representation.")
            print(f"[AUDIT] Created GlobalId: {element.GlobalId}")
        except Exception as e:
            logger.error(f"Failed to create geometry for '{name}': {str(e)}")
    else:
        logger.warning(f"No suitable GeometricRepresentationContext found. Geometry skipped.")

    # 4. 공간 구조(Spatial Structure)에 할당
    container = get_spatial_container(model, products=[reference_element] if reference_element else None)
    if container:
        ifcopenshell.api.run("spatial.assign_container", model, products=[element], relating_structure=container)
        logger.info(f"Assigned '{name}' to container '{container.is_a()}'.")
    else:
        logger.warning(f"No spatial container found to assign '{name}'.")

    # 5. 기본 프로퍼티 세트 추가
    try:
        pset_name = "Pset_Common"
        if "Wall" in ifc_class: pset_name = "Pset_WallCommon"
        elif "Window" in ifc_class: pset_name = "Pset_WindowCommon"
        elif "Door" in ifc_class: pset_name = "Pset_DoorCommon"
        elif "Slab" in ifc_class: pset_name = "Pset_SlabCommon"
        
        pset = ifcopenshell.api.run("pset.add_pset", model, product=element, name=pset_name)
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Reference": "ArchiGent_Gen"})
    except Exception as e:
        logger.error(f"Failed to add Pset to '{name}': {str(e)}")
    
    return element

def get_distance_between(model, element1, element2):
    """두 IFC 요소 사이의 최단 거리를 mm 단위로 반환합니다. (간이 중심점 기반)"""
    import numpy as np
    m1 = ifcopenshell.util.placement.get_local_placement(element1.ObjectPlacement)
    m2 = ifcopenshell.util.placement.get_local_placement(element2.ObjectPlacement)
    
    p1 = m1[0:3, 3]
    p2 = m2[0:3, 3]
    
    dist_model_units = np.linalg.norm(p1 - p2)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    return dist_model_units * scale * 1000.0

def get_typical_wall_spacing(model):
    """모델 내 벽들 사이의 가장 흔한(typical) 간격을 mm 단위로 추정합니다."""
    walls = model.by_type("IfcWall")
    if len(walls) < 2:
        return 3000.0 # 기본값
        
    distances = []
    # 단순화: 모든 벽 쌍의 거리를 구한 뒤 0이 아닌 최소값 주변을 탐색
    for i in range(min(len(walls), 10)):
        for j in range(i + 1, min(len(walls), 10)):
            d = get_distance_between(model, walls[i], walls[j])
            if d > 100: # 너무 가까운(겹친) 객체 제외
                distances.append(d)
                
    if not distances:
        return 3000.0
        
    # 가장 작은 거리 반환 (보통 이게 방의 너비나 벽 사이 간격임)
    return min(distances)

def get_element_geometry_info(element, model=None):
    """
    IFC 요소의 위치(Matrix), 중심점, 시작점, 끝점 및 방향 정보를 추출합니다.
    벽(IfcWall)의 경우 선형 배치를 기준으로 시작점과 끝점을 계산합니다.
    """
    import ifcopenshell.util.placement
    import ifcopenshell.util.element
    import ifcopenshell.util.unit
    
    matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
    pos = matrix[0:3, 3] # [x, y, z]
    direction = matrix[0:3, 0] # x-axis vector (방향)
    
    # 기본 정보
    info = {
        "globalId": element.GlobalId,
        "class": element.is_a(),
        "name": element.Name or "",
        "position": pos.tolist(),
        "direction": direction.tolist()
    }
    
    # 벽의 경우 추가 기하 정보 (Length 기반)
    if element.is_a("IfcWall"):
        props = get_wall_properties(element)
        length = 5000.0 # 기본값
        
        # Pset에서 Length가 없을 경우 수동 추출 시도
        psets = ifcopenshell.util.element.get_psets(element)
        for pset_name in ["Pset_WallCommon", "Basic", "Parameters", "ArchiGent_Gen"]:
            if pset_name in psets:
                p = psets[pset_name]
                if "Length" in p: length = float(p["Length"])
        
        # 시작점(Start)과 끝점(End) 계산
        start_pt = pos
        end_pt = pos + direction * (length / 1000.0)
        
        # 단위 보정
        scale = 1.0
        if model:
            scale = ifcopenshell.util.unit.calculate_unit_scale(model)
        
        info["start_pt"] = (start_pt * 1000.0 / (scale * 1000.0)).tolist() 
        info["end_pt"] = (end_pt * 1000.0 / (scale * 1000.0)).tolist()
        info["length_mm"] = length
        
    return info

def extract_bounding_box(element, model=None):
    """
    IFC 요소의 모든 정점(Vertex)을 분석하여 AABB(Axis-Aligned Bounding Box)를 추출합니다.
    단위: mm
    """
    import ifcopenshell.geom
    import ifcopenshell.util.unit
    
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    
    scale = 1.0
    if model:
        scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = shape.geometry.verts
        
        x_coords = [verts[i] for i in range(0, len(verts), 3)]
        y_coords = [verts[i+1] for i in range(0, len(verts), 3)]
        z_coords = [verts[i+2] for i in range(0, len(verts), 3)]
        
        return {
            "x_min": min(x_coords) * 1000.0, "x_max": max(x_coords) * 1000.0,
            "y_min": min(y_coords) * 1000.0, "y_max": max(y_coords) * 1000.0,
            "z_min": min(z_coords) * 1000.0, "z_max": max(z_coords) * 1000.0
        }
    except Exception as e:
        # Representation이 없거나 계산 실패 시, Placement 좌표만이라도 반환 (Fallback)
        logger.warning(f"BBox extraction failed for {element.GlobalId} ({e}). Falling back to placement.")
        matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
        pos = matrix[0:3, 3] * 1000.0 / (scale * 1000.0)
        return {
            "x_min": pos[0], "x_max": pos[0],
            "y_min": pos[1], "y_max": pos[1],
            "z_min": pos[2], "z_max": pos[2]
        }

def extract_spatial_graph(model):
    """
    에이전트가 이해할 수 있는 위상학적 공간 관계(Scene Graph)를 추출합니다.
    방(IfcSpace) 단위로 포함된 요소들을 정리합니다.
    """
    spatial_graph = {}
    spaces = model.by_type("IfcSpace")
    
    for space in spaces:
        space_id = space.GlobalId
        space_name = space.Name or space_id
        
        elements_in_space = []
        # IfcRelContainedInSpatialStructure 관계 탐색
        for rel in getattr(space, "ContainsElements", []):
            for element in rel.RelatedElements:
                elements_in_space.append({
                    "id": element.GlobalId,
                    "type": element.is_a(),
                    "name": element.Name
                })
        
        bounded_by = []
        # IfcRelSpaceBoundary 관계 탐색
        for boundary in getattr(space, "BoundedBy", []):
            if boundary.RelatedBuildingElement:
                be = boundary.RelatedBuildingElement
                bounded_by.append({
                    "id": be.GlobalId,
                    "type": be.is_a(),
                    "name": be.Name
                })
        
        spatial_graph[space_id] = {
            "name": space_name,
            "type": "Room/Space",
            "contains": elements_in_space,
            "bounded_by": bounded_by
        }
    
    return spatial_graph

def get_connected_elements(element, model=None):
    """지정된 요소와 물리적/논리적으로 연결된 요소 목록을 반환합니다."""
    import ifcopenshell.util.element
    # 1. Direct References (유틸리티 사용)
    connected = ifcopenshell.util.element.get_referenced_elements(element)
    
    # 2. Relationship-based (IfcRelConnects...)
    for rel in getattr(element, "ConnectedTo", []):
        if hasattr(rel, "RelatedElement"):
            connected.append(rel.RelatedElement)
    for rel in getattr(element, "ConnectedFrom", []):
        if hasattr(rel, "RelatingElement"):
            connected.append(rel.RelatingElement)
            
    # 중복 제거 및 자기 자신 제외
    unique_connected = list(set(connected))
    if element in unique_connected:
        unique_connected.remove(element)
        
    return unique_connected
    if element in unique_connected:
        unique_connected.remove(element)
        
    return unique_connected
