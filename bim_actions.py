'''
bim_actions.py - Deterministic Actions Gateway for ArchiGent
============================================================
This module defines the structured actions that the LLM can output (as JSON) 
and the execution logic that securely maps these intents into ifcopenshell 
library calls without executing arbitrary Python scripts.
'''

import logging
import json
from typing import Any, Dict, List, Optional
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement
import numpy as np

import bim_util

logger = logging.getLogger(__name__)

def action_create_element(model: ifcopenshell.file, params: Dict[str, Any]) -> str:
    """
    Creates a new geometric element based on provided parameters.
    Supports symbolic keywords for dx_mm and dy_mm: "START", "END", "ROTATE_90", "ROTATE_-90".
    """
    ifc_class = params.get("ifc_class", "IfcWall")
    length = float(params.get("length", 4000.0))
    height = float(params.get("height", 3000.0))
    thickness = float(params.get("thickness", 200.0))
    name = params.get("name", f"ArchiGent_Gen_{ifc_class.replace('Ifc', '')}")
    
    # [Symbolic Resolution] dx_mm, dy_mm 등이 문자열 키워드인 경우 처리
    dx_raw = params.get("dx_mm", 0.0)
    dy_raw = params.get("dy_mm", 0.0)
    dz_raw = params.get("dz_mm", 0.0)
    
    ref_id = params.get("reference_element_global_id")
    ref_element = None
    matrix = np.eye(4)
    
    dx, dy, dz = 0.0, 0.0, float(dz_raw) if isinstance(dz_raw, (int, float)) else 0.0
    
    if ref_id:
        try:
            ref_element = model.by_id(ref_id)
            if ref_element and hasattr(ref_element, "ObjectPlacement") and ref_element.ObjectPlacement:
                matrix = ifcopenshell.util.placement.get_local_placement(ref_element.ObjectPlacement)
                
                # 기하 정보 추출 (키워드 환산을 위해)
                geo = bim_util.get_element_geometry_info(ref_element)
                ref_len = geo.get("length_mm", 0.0)
                
                # dx_mm 처리
                if dx_raw == "END": dx = ref_len
                elif dx_raw == "START": dx = 0.0
                elif dx_raw == "CENTER": dx = ref_len / 2.0
                else: dx = float(dx_raw)
                
                # dy_mm 처리
                dy = float(dy_raw) if isinstance(dy_raw, (int, float)) else 0.0
                
                # ROTATE 처리 (방향 행렬 조작)
                if dx_raw in ["ROTATE_90", "ROTATE_-90"] or dy_raw in ["ROTATE_90", "ROTATE_-90"]:
                    angle = 90 if "90" in str(dx_raw) or "90" in str(dy_raw) else 0
                    if "-90" in str(dx_raw) or "-90" in str(dy_raw): angle = -90
                    
                    # 90도 회전 변환 행렬 (Z축 기준)
                    rad = np.radians(angle)
                    rot_z = np.array([
                        [np.cos(rad), -np.sin(rad), 0, 0],
                        [np.sin(rad),  np.cos(rad), 0, 0],
                        [0,            0,           1, 0],
                        [0,            0,           0, 1]
                    ])
                    # 기존 매트릭스에 회전 적용
                    # 평행이동 후 회전할지, 회전 후 평행이동할지 결정 필요. 
                    # 여기서는 '끝점에서 꺾기'를 위해: 1. 끝점으로 이동 2. 회전 순서로 적용
                    if dx_raw == "ROTATE_90" or dx_raw == "ROTATE_-90":
                        matrix = bim_util.translate_matrix(model, matrix, dx_mm=ref_len)
                        matrix = matrix @ rot_z
                        dx, dy = 0.0, 0.0 # 이미 matrix에 반영됨
        except Exception as e:
            logger.warning(f"Could not resolve reference element or keywords: {e}")
            dx = float(dx_raw) if isinstance(dx_raw, (int, float)) else 0.0
            dy = float(dy_raw) if isinstance(dy_raw, (int, float)) else 0.0
    
    new_matrix = bim_util.translate_matrix(model, matrix, dx_mm=dx, dy_mm=dy, dz_mm=dz)
    
    new_element = bim_util.create_element(
        model=model,
        matrix=new_matrix,
        ifc_class=ifc_class,
        length=length,
        height=height,
        thickness=thickness,
        name=name,
        reference_element=ref_element
    )
    
    return f"[AUDIT] Created GlobalId: {new_element.GlobalId}"

def action_modify_wall_properties(model: ifcopenshell.file, params: Dict[str, Any]) -> str:
    """
    Modifies the properties (Pset) and representation of an existing wall.
    Currently focuses on thickness and height, modifying Psets if possible.
    (Modifying the physical 3D representation inplace is complex, so we usually update the Psets or log).
    Expected params:
    - target_global_id (str): GlobalId of the wall to modify
    - thickness (float, optional): New thickness in mm
    - height (float, optional): New height in mm
    """
    target_id = params.get("target_global_id")
    if not target_id:
        raise ValueError("target_global_id is required for modify_wall_properties")
    
    try:
        wall = model.by_id(target_id)
    except:
        raise ValueError(f"Element with GlobalId '{target_id}' not found.")
        
    thickness = params.get("thickness")
    height = params.get("height")
    
    # 1. Update Properties
    psets = ifcopenshell.util.element.get_psets(wall)
    target_pset_name = "Pset_WallCommon"
    
    pset_to_edit = None
    for rel in getattr(wall, "IsDefinedBy", []):
        if rel.is_a("IfcRelDefinesByProperties"):
            prop_set = rel.RelatingPropertyDefinition
            if prop_set.is_a("IfcPropertySet") and prop_set.Name == target_pset_name:
                pset_to_edit = prop_set
                break
                
    if not pset_to_edit:
        pset_to_edit = ifcopenshell.api.run("pset.add_pset", model, product=wall, name=target_pset_name)
        
    props_to_update = {}
    if thickness is not None:
        props_to_update["Thickness"] = float(thickness)
    if height is not None:
        props_to_update["Height"] = float(height)
        
    if props_to_update:
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset_to_edit, properties=props_to_update)
    
    return f"[AUDIT] Modified GlobalId: {wall.GlobalId} with {props_to_update}"

def action_translate_element(model: ifcopenshell.file, params: Dict[str, Any]) -> str:
    """
    Translates an existing element in 3D space.
    Expected params:
    - target_global_id (str): GlobalId of the element to translate
    - dx_mm (float)
    - dy_mm (float)
    - dz_mm (float)
    """
    target_id = params.get("target_global_id")
    if not target_id:
        raise ValueError("target_global_id is required for translate_element")
        
    try:
        element = model.by_id(target_id)
    except:
        raise ValueError(f"Element with GlobalId '{target_id}' not found.")
        
    dx = float(params.get("dx_mm", 0.0))
    dy = float(params.get("dy_mm", 0.0))
    dz = float(params.get("dz_mm", 0.0))
    
    if not hasattr(element, "ObjectPlacement") or not element.ObjectPlacement:
        raise ValueError(f"Element '{target_id}' does not have an ObjectPlacement to translate.")
        
    matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
    new_matrix = bim_util.translate_matrix(model, matrix, dx_mm=dx, dy_mm=dy, dz_mm=dz)
    
    # Needs to be back in SI units for edit_object_placement
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    matrix_si = new_matrix.copy()
    matrix_si[0:3, 3] *= scale
    
    ifcopenshell.api.run("geometry.edit_object_placement", model, product=element, matrix=matrix_si)
    
    return f"[AUDIT] Translated GlobalId: {element.GlobalId} by ({dx}, {dy}, {dz})mm"

def action_delete_element(model: ifcopenshell.file, params: Dict[str, Any]) -> str:
    """
    Deletes an element from the model.
    Expected params:
    - target_global_id (str): GlobalId of the element to delete
    """
    target_id = params.get("target_global_id")
    if not target_id:
        raise ValueError("target_global_id is required for delete_element")
        
    try:
        element = model.by_id(target_id)
    except:
        raise ValueError(f"Element with GlobalId '{target_id}' not found.")
        
    ifcopenshell.api.run("root.remove_product", model, product=element)
    
    return f"[AUDIT] Deleted GlobalId: {target_id}"

# ==========================================
# Dispatcher
# ==========================================

ACTION_DISPATCHER = {
    "create_element": action_create_element,
    "modify_wall_properties": action_modify_wall_properties,
    "translate_element": action_translate_element,
    "delete_element": action_delete_element,
}

def execute_actions_from_json(model: ifcopenshell.file, actions: List[Dict[str, Any]]) -> List[str]:
    """
    Executes a list of deterministic actions on the given IFC model.
    Supports a special variable '$LAST_ID' in params to refer to the GlobalId
    of the most recently created element in the same action list.
    """
    audit_logs = []
    last_created_id = None
    
    # actions -> [ {"action": "create_wall", "params": {...}}, ... ]
    for i, item in enumerate(actions):
        action_name = item.get("action")
        raw_params = item.get("params", {})
        
        # Deep copy and resolve $LAST_ID placeholders
        params = {}
        for k, v in raw_params.items():
            if isinstance(v, str) and "$LAST_ID" in v:
                if last_created_id:
                    params[k] = v.replace("$LAST_ID", last_created_id)
                else:
                    logger.warning(f"Action '{action_name}' used $LAST_ID but no element was created yet.")
                    params[k] = v
            else:
                params[k] = v

        if not action_name or action_name not in ACTION_DISPATCHER:
            raise ValueError(f"Action item [{i}] has an invalid or missing 'action': {action_name}. "
                             f"Available actions: {list(ACTION_DISPATCHER.keys())}")
            
        handler = ACTION_DISPATCHER[action_name]
        try:
            result = handler(model, params)
            audit_logs.append(result)
            
            # If the action created an element, capture its GlobalId for the next step's $LAST_ID
            if "Created GlobalId:" in result:
                import re
                match = re.search(r"Created GlobalId:\s*(\S+)", result)
                if match:
                    last_created_id = match.group(1)
                    
        except Exception as e:
            logger.error(f"Error executing action '{action_name}' with params {params}: {str(e)}")
            raise e # Reraise to cause iteration failure
            
    return audit_logs
