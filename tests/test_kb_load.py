import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (coder_agent 임포트 가능하도록)
sys.path.append(os.getcwd())

from coder_agent import _load_code_kb

def test_kb_load():
    kb_text = _load_code_kb()
    
    if not kb_text:
        print("FAIL: Knowledge Base is empty!")
        return False
    
    print("SUCCESS: Knowledge Base loaded.")
    print("-" * 20)
    print(f"KB length: {len(kb_text)} characters")
    
    # 특정 키워드가 포함되어 있는지 확인
    if "query_samples.txt" in kb_text and "modification_samples.txt" in kb_text:
        print("SUCCESS: Both sample files are present in KB.")
    else:
        print("FAIL: Some sample files are missing in KB.")
        return False
    
    if "ifcopenshell" in kb_text:
        print("SUCCESS: Content seems valid.")
    else:
        print("FAIL: Content does not look like ifcopenshell code.")
        return False
        
    return True

if __name__ == "__main__":
    if test_kb_load():
        sys.exit(0)
    else:
        sys.exit(1)
