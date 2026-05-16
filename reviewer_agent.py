'''
reviewer_agent.py - ArchiGent Reviewer Agent
===========================================
코드가 실행되기 전, 계획과 생성된 코드를 검토하여 논리적 무결성과 안전성을 확인합니다.
'''

import os
import logging
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """
당신은 건축 BIM 소프트웨어 품질 보증(QA) 전문가입니다.
코더 에이전트가 작성한 **결정론적 액션 JSON 데이터**와 원래의 계획(Task Specification)을 비교하여 리뷰를 수행하세요.

[검토 기준]
1. JSON 형식 검증: 제공된 데이터가 파싱 가능한 유효한 JSON 배열(Array) 형식인가?
2. 액션 유효성: 허용된 액션(`create_element`, `modify_wall_properties`, `translate_element`, `delete_element`)과 해당 파라미터가 사용되었는가? 필수 파라미터가 비어 있지는 않은가?
3. 의도 일치성: 생성된 액션들이 '계획(Plan)'에 명시된 작업 의도(예: 벽 생성, 속성 수정)를 충실히 따르고 있는가?
4. 실행 결과: 이미 발생한 '실행 에러(Execution Error)'가 있는가? 에러가 있다면 무조건 REJECTED를 내리세요.

[응답 형식]
- 반드시 'APPROVED' 또는 'REJECTED: [사유]'로 시작하세요.
- 불필요하게 엄격한 기준으로 거절하지 마세요. (예: 두께를 동적으로 가져오는 파이썬 로직이 부족하다면서 거절하지 마세요. JSON 자체로 타당하면 APPROVED 합니다.)
- 한국어로 간결하게 한 문장으로 설명하세요.
"""

def review_code(
    plan: str,
    code: str,
    execution_error: str = "",
) -> str:
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL_NAME"),
        openai_api_key=os.getenv("OPENAI_API_KEY", "empty"),
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        temperature=0,
        max_tokens=1024
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", REVIEWER_SYSTEM_PROMPT),
        ("user", "작업 계획:\n{plan}\n\n작성된 코드:\n{code}\n\n실행 시 발생한 에러(기존 실행 결과):\n{execution_error}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    review_result = chain.invoke({
        "plan": plan,
        "code": code,
        "execution_error": execution_error or "없음(정상 실행됨)"
    })
    
    return review_result.strip()
