"""
responder_agent.py — Responder Agent (최종 응답 생성기)
=====================================================
역할:
    - 파이프라인(Analyzer -> Planner -> Coder -> Verifier)이 모두 끝난 후,
    - 사용자의 초기 요청, 분석/수행 내역, 그리고 법적 정합성(Compliance) 등을 
      포함하여 인간 친화적인(MD 포맷) 대화형 응답을 생성합니다.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

SYSTEM_PROMPT = """\
당신은 ArchiGent BIM 통합 에이전트의 '대화형 안내원'입니다.
사용자에게 기술적인 수행 과정과 결과를 자연스럽고 친절하게 설명해야 합니다.

[작성 규칙]
1. 프론트엔드 UI 에러를 방지하기 위해 **절대 마크다운(Markdown) 기호(예: #, **, -, `)를 사용하지 마세요.**
2. 강조나 단락 구분이 필요하다면 반드시 순수 HTML 태그(`<b>`, `<i>`, `<br>`, `<ul><li>`)만을 사용하여 렌더링이 깨지지 않게 보장하세요.
3. [검증 결과]가 "FAIL"을 포함한다면:
   - 작업 중 어떤 기술적 문제(예: 모듈 에러, 논리 오류 등)가 발생하여 완료하지 못했는지 사과와 함께 설명하세요.
   - "우측 뷰어에 모델을 로드할 수 없음"을 명시하세요.
4. [검증 결과]가 "PASS"를 포함한다면:
   - 사용자의 초기 요청을 어떻게 해결했는지 요약하세요.
   - 어떤 변경이 가해졌는지 <b>태그로 강조하며 설명하세요.
   - 마지막에 "수정된 모델 뷰어가 우측에 로드되었습니다." 형태의 맺음말을 추가하세요.
5. 너무 길지 않게 핵심만 간결히 설명하세요.
"""

def generate_conversational_response(
    user_request: str,
    analyzer_summary: str,
    plan_text: str,
    verification_result: str,
    model: str = "qwen"
) -> str:
    """LLM을 호출하여 최종 대화형 메시지를 반환합니다."""
    
    prompt = f"""\
[사용자 요청]
{user_request}

[데이터 분석 요약]
{analyzer_summary}

[적용된 작업 계획]
{plan_text}

[검증 결과]
{verification_result}

위 정보를 바탕으로 사용자에게 제공할 최종 응답(한국어)을 작성하세요.
"""

    # VLLM / Qwen 호출
    base_url = os.getenv("OPENAI_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY", "empty")
    model_name = os.getenv("LLM_MODEL_NAME", "gpt-4-turbo")
    
    if OpenAI is None:
        return "OpenAI 라이브러리가 없어 응답 생성을 건너뜁니다."
        
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3, # 약간의 유연성
            max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"응답 생성 중 오류가 발생했습니다: {str(e)}"
