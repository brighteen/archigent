'''
coder_agent.py - ArchiGent Coder Agent (Deterministic JSON Version)
===================================================================
1. RAG를 활용하여 유사 사례 검색 (Knowledge Base 유지)
2. Qwen 모델을 사용하여 파이썬 스크립트 대신, bim_actions 에 정의된 
   결정론적 액션 스키마(JSON) 형식을 생성
'''

import os
import logging
import json
import re
from pathlib import Path
from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from rag_manager import rag_manager

logger = logging.getLogger(__name__)

CODER_SYSTEM_PROMPT = """
당신은 IFC 및 BIM 데이터 제어 전문가이자 ArchiGent Coder 에이전트입니다.
제공된 '작업 명세서(task_spec)'를 분석하여 IFC 파일 수정을 지시하는 **JSON** 형식의 응답만을 생성해야 합니다.

[기하학적 배치 및 공간 추론 전략 (Spatial Reasoning Strategy)]
작업 명세서에는 객체의 `geometry_info`, `bounding_box` (AABB), `spatial_graph` (위상 정보)가 포함되어 있습니다.
1. **기호적 배치 (Symbolic Placement - 권장)**: 암산으로 좌표를 계산하지 말고 아래 키워드를 `dx_mm`에 우선 사용하세요.
   - `"END"`: 기준 객체의 끝점에 붙여서 생성. (벽 연결 시 필수)
   - `"ROTATE_90"`: 현재 방향에서 90도 회전하여 끝점에서 꺾기. (사각형 집 구조 생성 시 필수)
   - **주의**: 위 키워드 사용 시, 다른 좌표(`dy_mm`, `dz_mm`)는 반드시 `0`으로 설정하세요. 환각 수치를 넣지 마세요.
   - `"START"`, `"CENTER"`: 각각 시작점과 중앙점 배치.
2. **충돌 방지 (Collision Avoidance)**: 새 객체의 이동 거리를 잡을 때, `bounding_box` 영역을 침범하지 않는지 확인하세요.
3. **공간 단위 배치 (Room-aware)**: `spatial_graph` 자료를 보고 현재 어떤 방(IfcSpace) 내부에서 작업 중인지 인지하세요.

[사용 가능한 액션 (Actions)]
1. `create_element`
   - 설명: 새로운 요소(IfcWall, IfcWindow, IfcDoor, IfcSlab)를 생성합니다.
   - 인자(Parameters):
     - `ifc_class` (문자열, 필수): `"IfcWall"`, `"IfcWindow"`, `"IfcDoor"`, `"IfcSlab"`.
     - `name` (문자열): 객체 이름.
     - `length`, `height`, `thickness` (실수, 필수): mm 단위.
     - `dx_mm` (실수/문자열): 이동 거리 또는 키워드(`"START"`, `"END"`, `"ROTATE_90"`, `"ROTATE_-90"`).
     - `dy_mm`, `dz_mm` (실수): 추가 오프셋. (키워드 사용 시 보통 0)
     - `reference_element_global_id` (문자열, 필수): 배치 기준이 될 기존 요소의 GlobalId.

2. `modify_wall_properties`, `translate_element`, `delete_element` (기존과 동일)

[출력 형식 및 제약 규칙]
- **절대 금지**: 중괄호 플레이스홀더(`{{...}}`) 사용 금지.
- **연속 참조**: `$LAST_ID`를 사용하여 방금 만든 객체를 기준(reference)으로 다음 객체를 만드세요.
- 오직 JSON 배열만 출력하세요. (No Korean explanations!)

형식 예제 (사각형 벽 생성):
```json
[
  {{
    "action": "create_element",
    "params": {{
      "ifc_class": "IfcWall",
      "name": "Wall_2",
      "length": 3000, "height": 2500, "thickness": 200,
      "dx_mm": 5000, "dy_mm": 0, "dz_mm": 0,
      "reference_element_global_id": "EXISTING_WALL_ID"
    }}
  }},
  {{
    "action": "create_element",
    "params": {{
      "ifc_class": "IfcWall",
      "name": "Wall_3",
      "length": 3000, "height": 2500, "thickness": 200,
      "dx_mm": 3000, "dy_mm": 0, "dz_mm": 0,
      "reference_element_global_id": "$LAST_ID"
    }}
  }}
]
```

[지식 베이스]
{knowledge_base}
"""

def generate_ifc_code(
    task_spec: str,
    user_request: str = "",
    error_feedback: str = "",
    model_name: str = "qwen",
) -> str:
    """작업 명세를 기반으로 결정론적 액션 JSON 문자열을 생성합니다. (과거 호환성을 위해 함수명 유지)"""
    search_query = user_request or task_spec
    kb = rag_manager.retrieve_code_samples(search_query, top_k=1)
    
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL_NAME", "qwen-30b"),
        openai_api_key=os.getenv("OPENAI_API_KEY", "empty"),
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        temperature=0.1,  # JSON 구조 안정을 위해 낮춤
        max_tokens=2048
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", CODER_SYSTEM_PROMPT),
        ("user", "작업 명세서:\n{task_spec}\n\n이전 실행 실패 에러(JSON 포맷/파라미터 오류 등 확인):\n{error_feedback}\n\n액션 JSON을 생성하세요:")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    raw_response = chain.invoke({
        "knowledge_base": kb,
        "task_spec": task_spec,
        "error_feedback": error_feedback
    })
    
    # ── JSON 파싱 및 정제 ──
    # 1. ```json 블록 추출
    json_pattern = re.compile(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", re.DOTALL)
    match = json_pattern.search(raw_response)
    if match:
        clean_json_str = match.group(1).strip()
    else:
        # 백틱이 없다면 대괄호로 둘러싸인 배열 구조 추출 시도
        list_pattern = re.search(r"(\[\s*\{.*?\}\s*\])", raw_response, re.DOTALL)
        if list_pattern:
            clean_json_str = list_pattern.group(1).strip()
        else:
            clean_json_str = raw_response.strip()
            
    # LLM이 홑따옴표를 쓴 경우 복구 시도
    clean_json_str = clean_json_str.replace("'", '"')
    
    # JSON 검증을 한번 해보고, 실패하면 로깅은 남기지만 일단 원본을 리턴 (Executor에서 에러 처리되게)
    try:
        json.loads(clean_json_str)
        return clean_json_str
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to validate generated JSON: {e}\nRaw result: {clean_json_str}")
        return clean_json_str # Return what we have
