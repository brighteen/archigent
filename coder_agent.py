'''
coder_agent.py - ArchiGent Coder Agent
======================================
1. BIM_LLM_code_agent의 샘플 코드를 RAG로 활용
2. Gemini 모델을 사용하여 ifcopenshell 파이썬 스크립트 생성
'''

import os
import logging
from pathlib import Path
from typing import Optional
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

# RAG Knowledge Base 로드 (BIM_LLM_code_agent 기반)
def _load_code_kb() -> str:
    # 프로젝트 내 prompts/code_samples 디렉토리에서 지식 베이스 로드
    kb_path = Path(__file__).parent / "prompts" / "code_samples"
    kb_text = []
    if kb_path.exists():
        for f in kb_path.glob("*.txt"):
            kb_text.append(f"### Knowledge: {f.name}\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(kb_text)

CODER_SYSTEM_PROMPT = """
당신은 ifcopenshell 전문가인 Coder 에이전트입니다.
제공된 '작업 명세서'를 바탕으로 IFC 파일을 수정하거나 생성하는 파이썬 코드를 작성하세요.

[핵심 규칙]
1. 반드시 `import ifcopenshell` 및 필요한 유틸리티를 포함하세요.
2. 입력 경로는 환경변수 `os.environ["IFC_INPUT_PATH"]`, 출력 경로는 `os.environ["IFC_OUTPUT_PATH"]`를 사용하세요.
3. 수정을 수반하는 작업인 경우에만 `model.write(os.environ["IFC_OUTPUT_PATH"])`를 호출하세요.
4. 만약 조회 전용(QUERY) 작업이라면 파일을 저장하지 말고 오직 정보 추출 및 결과 출력(print)만 수행하세요.
5. 설명이나 백틱 없이 순수 Python 코드만 출력하세요.

[지식 베이스 (RAG)]
{knowledge_base}
"""

def generate_ifc_code(
    task_spec: str,
    error_feedback: str = "",
    model_name: str = "gemini-2.5-flash",
) -> str:
    """작업 명세를 기반으로 코드를 생성합니다."""
    kb = _load_code_kb()
    
    llm = ChatGoogleGenerativeAI(model=model_name, temperature=0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", CODER_SYSTEM_PROMPT),
        ("user", "작업 명세서:\n{task_spec}\n\n이전 실행 실패 피드백 (항목이 있는 경우만 참고):\n{error_feedback}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    raw_code = chain.invoke({
        "knowledge_base": kb,
        "task_spec": task_spec,
        "error_feedback": error_feedback
    })
    
    # 정제 (마크다운 백틱 제거)
    code = raw_code.replace("```python", "").replace("```", "").strip()
    return code
