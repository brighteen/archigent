'''
reviewer_agent.py - ArchiGent Reviewer Agent
===========================================
코드가 실행되기 전, 계획과 생성된 코드를 검토하여 논리적 무결성과 안전성을 확인합니다.
'''

import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """
당신은 건축 BIM 소프트웨어 품질 보증(QA) 전문가입니다.
코더가 작성한 Python 코드와 원래의 계획(Task Specification)을 비교하여 리뷰를 수행하세요.

[검토 기준]
1. 계획 준수: 코드가 계획에 명시된 작업만 수행하는가?
2. 안전성: 실수로 다른 요소를 삭제하거나 전체 모델을 파괴할 위험이 있는가?
3. 문법 오류: ifcopenshell API 사용이 올바른가?

[응답 형식]
- 반드시 'APPROVED' 또는 'REJECTED: [사유]'로 시작하세요.
- 한국어로 간결하게 설명하세요.
"""

def review_code(
    plan: str,
    code: str,
    model_name: str = "gemini-2.5-flash",
) -> str:
    llm = ChatGoogleGenerativeAI(model=model_name, temperature=0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", REVIEWER_SYSTEM_PROMPT),
        ("user", "작업 계획:\n{plan}\n\n작성된 코드:\n{code}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    review_result = chain.invoke({
        "plan": plan,
        "code": code
    })
    
    return review_result.strip()
