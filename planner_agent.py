"""
planner_agent.py — Planner Agent (3단계)
=========================================
Agentic Workflow 파이프라인 3단계: Planner(기획자) 에이전트

역할:
    - 2단계 Analyzer가 반환한 JSON 컨텍스트와 사용자 원본 요청을 입력받아
    - Text2BIM의 다중 에이전트 CoT(Chain-of-Thought) 방식으로
    - Coder 에이전트가 그대로 따라 구현할 수 있는 결정론적 작업 명세(Task Specification)를 반환

출력 원칙:
    - 절대 Python 코드, ifcopenshell 코드를 생성하지 않음
    - 자연어 기반 "Step 1, Step 2, ..." 형식의 작업 지시서 문자열만 반환

참고 논문 및 레퍼런스:
    - Text2BIM: Multi-Agent Architectural Reasoning (CoT 2-chain 적용)
    - BIM_graph_agent: Analyzer 출력 JSON 스키마
"""

import json
import os
import re
from typing import Optional, List, Dict, Any
from pathlib import Path
from dotenv import load_dotenv

# --- API 클라이언트 (지원 백엔드: claude, gpt, gemini, mistral) ---
try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None


# ── 경로 설정 ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

INTENT_DECOMPOSER_PROMPT_PATH = PROMPTS_DIR / "planner_intent_decomposer.txt"
TASK_SPEC_GENERATOR_PROMPT_PATH = PROMPTS_DIR / "planner_task_spec_generator.txt"

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# ── API 키 설정 (환경 변수 우선, 없으면 아래 직접 입력) ─────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")


# ── 내부 유틸리티 ────────────────────────────────────────────────────────────

def _load_prompt(path: Path) -> str:
    """프롬프트 템플릿 파일을 읽어 반환합니다."""
    if not path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {path}\n"
            f"prompts/ 디렉토리에 템플릿 파일이 있는지 확인하세요."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _call_llm(prompt: str, model: str) -> str:
    """
    지정된 LLM 백엔드를 호출하고 응답 문자열을 반환합니다.

    Args:
        prompt: 완성된 프롬프트 문자열
        model: 백엔드 선택 ("claude" | "gpt" | "gemini" | "mistral")

    Returns:
        LLM 응답 문자열
    """
    if "claude" in model:
        if anthropic is None:
            raise ImportError("anthropic 패키지가 설치되어 있지 않습니다: pip install anthropic")
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        response = client.messages.create(
            model="claude-opus-4-5",   # claude-opus-4-5, claude-sonnet-4-5
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    if "gpt" in model:
        if OpenAI is None:
            raise ImportError("openai 패키지가 설치되어 있지 않습니다: pip install openai")
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content

    if "gemini" in model:
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig
        except ImportError:
            raise ImportError("google-genai 패키지가 설치되어 있지 않습니다: pip install google-genai")
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(temperature=0.0),
        )
        return response.text

    if "mistral" in model:
        if Mistral is None:
            raise ImportError("mistralai 패키지가 설치되어 있지 않습니다: pip install mistralai")
        client = Mistral(api_key=MISTRAL_API_KEY)
        response = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content

    raise ValueError(
        f"지원하지 않는 모델입니다: '{model}'. "
        f"'claude', 'gpt', 'gemini', 또는 'mistral' 중 하나를 선택하세요."
    )


def _validate_task_spec(task_spec: str) -> None:
    """
    생성된 Task Specification 문자열의 품질을 검증합니다.

    - Step 패턴 존재 여부 확인
    - 코드 패턴(import, def, exec 등) 미포함 확인
    """
    code_patterns = [
        r"\bimport\s+\w+",
        r"\bdef\s+\w+\s*\(",
        r"ifcopenshell\.",
        r"\bexec\s*\(",
        r"\beval\s*\(",
        r"```python",
        r"```ifc",
    ]
    for pattern in code_patterns:
        if re.search(pattern, task_spec, re.IGNORECASE):
            raise ValueError(
                f"Task Specification에 코드 패턴이 감지되었습니다: '{pattern}'\n"
                f"Planner는 코드를 생성하면 안 됩니다. 프롬프트를 점검하세요."
            )

    if "Step" not in task_spec and "단계" not in task_spec:
        raise ValueError(
            "Task Specification에 Step 구조가 없습니다. "
            "LLM이 올바른 형식으로 응답하지 않았습니다."
        )


def generate_task_specification(
    analyzer_context: dict,
    user_request: str,
    model: str = "claude",
    available_api_list: Optional[list] = None,
) -> dict:
    """
    단일 작업 명세(Task Specification)를 생성합니다.
    (기존 generate_task_specification_multi의 단일 버전)
    """
    analyzer_context_str = json.dumps(analyzer_context, ensure_ascii=False, indent=2)
    
    # Chain 1: Intent Decomposer
    intent_prompt_template = _load_prompt(INTENT_DECOMPOSER_PROMPT_PATH)
    intent_prompt = (
        intent_prompt_template
        .replace("<<analyzer_context>>", analyzer_context_str)
        .replace("<<user_request>>", user_request)
    )
    intent_document_raw = _call_llm(intent_prompt, model)

    # Chain 2: Task Spec Generator
    spec_prompt_template = _load_prompt(TASK_SPEC_GENERATOR_PROMPT_PATH)
    spec_prompt = (
        spec_prompt_template
        .replace("<<intent_document>>", intent_document_raw)
        .replace("<<analyzer_context>>", analyzer_context_str)
        .replace("<<available_api_list>>", str(available_api_list or "ifcopenshell standard API"))
    )

    task_spec = _call_llm(spec_prompt, model)
    _validate_task_spec(task_spec)

    return {
        "task_spec": task_spec,
        "intent_json": intent_document_raw
    }


# ── 메인 함수 ────────────────────────────────────────────────────────────────

def generate_task_specification_multi(
    analyzer_context: dict,
    user_request: str,
    style_profile_summary: str = "",
    num_options: int = 3,
    model: str = "claude",
) -> List[Dict[str, Any]]:
    """
    여러 개의 설계 시안(Options)을 생성합니다.
    """
    analyzer_context_str = json.dumps(analyzer_context, ensure_ascii=False, indent=2)
    
    # Chain 1: Intent Decomposer (공통 의도 파악)
    intent_prompt_template = _load_prompt(INTENT_DECOMPOSER_PROMPT_PATH)
    intent_prompt = (
        intent_prompt_template
        .replace("<<analyzer_context>>", analyzer_context_str)
        .replace("<<user_request>>", user_request)
    )
    intent_document_raw = _call_llm(intent_prompt, model)

    # Chain 2: Multi-Option Spec Generator
    # 사용자의 스타일 선호도 및 건축 법규 정보를 프롬프트에 추가
    generator_system_prompt = f"""
    당신은 건축 설계 및 법규 준수 전문가입니다. 
    다음 정보를 바탕으로 {num_options}가지 최적화 시안을 제안하세요.
    
    [준수 사항]
    1. 사용자의 스타일 선호도: {style_profile_summary}
    2. 제공된 '법규 정보'에 명시된 부등식 및 제약 조건을 반드시 만족해야 합니다.
    3. 최적화 문제 해결: 만약 특정 수치가 부족하다면(예: 채광 면적 < 1/10), 이를 만족하기 위해 필요한 최소한의 수치를 계산하여 작업 명세에 포함하세요.
    
    각 시안은 다음 JSON 형식으로 응답하세요:
    [{{ "id": 1, "title": "시안 제목", "task_spec": "단계별 명세 (계산 근거 포함)...", "features": {{ ... }} }}]
    """
    
    spec_prompt_template = _load_prompt(TASK_SPEC_GENERATOR_PROMPT_PATH)
    spec_prompt = (
        generator_system_prompt + "\n\n" +
        spec_prompt_template
        .replace("<<intent_document>>", intent_document_raw)
        .replace("<<analyzer_context>>", analyzer_context_str)
        .replace("<<available_api_list>>", "ifcopenshell standard API")
    )

    print(f"[Planner] {num_options}개의 시안 생성 중...")
    
    # QUERY인 경우 3개 시안 생성이 무의미하므로 단일 시안(보고서)으로 처리
    if "QUERY" in intent_document_raw:
        print("[Planner] 단순 조회 요청(QUERY)으로 감지되었습니다. 단일 보고서 시안을 생성합니다.")
        query_system_prompt = f"""
        당신은 BIM 데이터 분석 전문가입니다.
        사용자의 조회 요청(QUERY)에 대한 분석 결과와 이를 보기 좋게 정리하기 위한 작업 명세를 생성하세요.
        
        [중요 지시사항]
        1. 이 작업은 '조회 전용(Read-only)'입니다. 절대 IFC 파일을 수정하거나 `model.write()`를 호출하는 코드를 생성하라고 지시하지 마세요.
        2. `ifcopenshell`을 사용하여 필요한 정보를 추출하고 이를 표준 출력(print)이나 보고서 형식으로 구성하는 단계만 포함하세요.
        3. 결과 파일 저장이 필요 없는 순수 분석 작업임을 명시하세요.
        
        응답 형식:
        [{{ "id": 1, "title": "분석 보고서 생성", "task_spec": "보고서 생성 단계별 명세 (조회만 수행)...", "features": {{ "query_mode": 1.0 }} }}]
        """
        spec_prompt = query_system_prompt + "\n\n" + spec_prompt_template.replace("<<intent_document>>", intent_document_raw).replace("<<analyzer_context>>", analyzer_context_str).replace("<<available_api_list>>", "ifcopenshell standard API")
    else:
        spec_prompt = (
            generator_system_prompt + "\n\n" +
            spec_prompt_template
            .replace("<<intent_document>>", intent_document_raw)
            .replace("<<analyzer_context>>", analyzer_context_str)
            .replace("<<available_api_list>>", "ifcopenshell standard API")
        )

    response_raw = _call_llm(spec_prompt, model)
    
    try:
        # JSON 파싱 공백 및 백틱 제거
        clean_json = response_raw.replace("```json", "").replace("```", "").strip()
        options = json.loads(clean_json)
        # Verifier를 위해 intent_json을 각 옵션에 포함하거나 별도 관리
        for opt in options:
            opt["intent_json"] = intent_document_raw
        return options
    except Exception as e:
        logger.error(f"Failed to parse multi-options: {e}")
        return [{
            "id": 1,
            "title": "Default Plan",
            "task_spec": response_raw,
            "intent_json": intent_document_raw,
            "features": {"modern_aesthetic": 0.5, "functional_efficiency": 0.5}
        }]


# ── 편의 함수 ────────────────────────────────────────────────────────────────

def run_planner(
    analyzer_context: dict,
    user_request: str,
    model: str = "claude",
    available_api_list: Optional[list] = None,
    verbose: bool = True,
) -> str:
    """
    generate_task_specification()의 래퍼 함수.
    verbose=True이면 최종 Task Specification을 콘솔에 출력합니다.
    """
    task_spec = generate_task_specification(
        analyzer_context=analyzer_context,
        user_request=user_request,
        model=model,
        available_api_list=available_api_list,
    )
    if verbose:
        print("=" * 60)
        print(task_spec)
        print("=" * 60)
    return task_spec


# ── 직접 실행 (간단 테스트) ──────────────────────────────────────────────────
if __name__ == "__main__":
    # 샘플 Analyzer 컨텍스트 (BIM_graph_agent 출력 예시)
    sample_analyzer_context = {
        "targets": [
            {
                "globalId": "2O2Fr$t4X7Zf8NOew3FLOH",
                "ifcClass": "IfcWallStandardCase",
                "name": "Basic Wall:Interior - Partition:187578",
                "attributes": {
                    "Width": 200,
                    "Height": 2800,
                    "Length": 4500,
                },
                "relationships": {
                    "CONTAINED_IN": "Level 2",
                    "AGGREGATES": None,
                },
                "properties_json": '{"Pset_WallCommon": {"LoadBearing": false, "IsExternal": false}, "BaseQuantities": {"Width": 200, "Height": 2800, "Length": 4500}}',
            }
        ],
        "query_summary": "2층 복도에 위치한 내부 파티션 벽체 1개를 GlobalId로 식별함",
        "cypher_used": "MATCH (w:IfcWallStandardCase) WHERE w.name CONTAINS 'Partition' RETURN w.globalId, w.name, w.properties LIMIT 5",
    }

    sample_user_request = "2층 복도에 있는 내부 파티션 벽의 두께를 200mm에서 300mm로 변경해줘"

    result = run_planner(
        analyzer_context=sample_analyzer_context,
        user_request=sample_user_request,
        model="claude",  # 실제 API 키가 있어야 실행됩니다
    )
    print("\n[완료] Task Specification 생성 성공")
