import ifcopenshell
import ifcopenshell.util.element
from typing import List, Dict, Any


def verify_modifications(original_ifc_path: str, modified_ifc_path: str, modification_plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify that modifications in `modified_ifc_path` match the `modification_plan`.

    Parameters
    ----------
    original_ifc_path: str
        Path to the original IFC file.
    modified_ifc_path: str
        Path to the IFC file produced by the Coder agent.
    modification_plan: list of dict
        Each dict describes an expected change, e.g.::

            {
                "GlobalId": "0K9R8c...",
                "action": "modify",  # "create", "delete", "modify"
                "expected_attributes": {"Name": "New Wall"},
                "expected_properties": {
                    "Pset_WallCommon": {"IsExternal": True}
                }
            }

    Returns
    -------
    dict
        ``{"success": True}`` if all checks pass.
        If a check fails, returns ``{"success": False, "failed_globalid": <id>, "reason": <msg>}``.
    """
    # Load both IFC models
    original_model = ifcopenshell.open(original_ifc_path)
    modified_model = ifcopenshell.open(modified_ifc_path)

    for task in modification_plan:
        gid = task.get("GlobalId")
        action = task.get("action", "modify")
        try:
            if action == "delete":
                # The element must not exist in the modified model
                assert modified_model.by_guid(gid) is None, f"Element with GlobalId {gid} was not deleted."
                continue

            # For create or modify, the element must exist in the modified model
            modified_elem = modified_model.by_guid(gid)
            assert modified_elem is not None, f"Element with GlobalId {gid} missing in modified IFC."

            # Attribute checks
            expected_attrs = task.get("expected_attributes", {})
            for attr_name, expected_val in expected_attrs.items():
                actual_val = getattr(modified_elem, attr_name, None)
                assert actual_val == expected_val, (
                    f"Attribute '{attr_name}' mismatch for GlobalId {gid}: "
                    f"expected {expected_val!r}, got {actual_val!r}."
                )

            # Property set checks
            expected_props = task.get("expected_properties", {})
            if expected_props:
                psets = ifcopenshell.util.element.get_psets(modified_elem)
                for pset_name, prop_dict in expected_props.items():
                    actual_pset = psets.get(pset_name, {})
                    for prop_name, expected_val in prop_dict.items():
                        actual_val = actual_pset.get(prop_name)
                        assert actual_val == expected_val, (
                            f"Property '{prop_name}' in Pset '{pset_name}' mismatch for GlobalId {gid}: "
                            f"expected {expected_val!r}, got {actual_val!r}."
                        )

        except AssertionError as e:
            return {"success": False, "failed_globalid": gid, "reason": str(e)}

    return {"success": True}

# Example usage (for manual testing)
# if __name__ == "__main__":
#     plan = [
#         {
#             "GlobalId": "1A2B3C",
#             "action": "modify",
#             "expected_attributes": {"Name": "New Wall"},
#             "expected_properties": {"Pset_WallCommon": {"IsExternal": True}}
#         }
#     ]
#     result = verify_modifications("original.ifc", "modified_temp.ifc", plan)
#     print(result)
