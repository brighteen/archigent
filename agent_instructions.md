# ArchiGent 에이전트 작업 지침 (Agent Guidelines)

이 파일은 ArchiGent 프로젝트를 수행하는 모든 AI 에이전트를 위한 작업 지침서입니다. 작업 및 답변 생성 시 다음 규칙을 반드시 준수하십시오.

## 1. 언어 및 사고 체계 (Language & Thinking)
- **언어 우선순위**: 모든 계획 수립(Planning), 작업 명세 설계(Task Design), 그리고 내부 사고 과정은 **한국어**를 사용합니다.
- **답변 생성**: 사용자에게 제공하는 모든 답변과 설명은 특별한 요청이 없는 한 한국어로 작성합니다.

## 2. 작업 환경 및 실행 (Environment & Execution)
- **가상환경(Virtual Env)**: 모든 라이브러리 설치(`pip`), 파이썬 파일 실행(`python`), 그리고 개발 도구 호출은 반드시 프로젝트 전용 가상환경(`archigent`) 내부에서 진행해야 합니다.
- **프로세스 관리**: 서버 실행(`start_web.sh`, `start_llm.sh`) 전에 이전 프로세스가 종료되었는지 확인하고, 필요시 `stop_all.sh`를 실행하여 포트(8000, 8001) 및 GPU 리소스를 완전히 정리한 후 시작합니다.
- **리소스 최적화**: GPU 메모리 상태(nvidia-smi)를 주기적으로 확인하며, vLLM 서버의 OOM(Out of Memory) 발생 시 메모리 점유 프로세스를 정리합니다.

## 3. 코드 및 데이터 무결성 (Code & Data Integrity)
- **결정론적 액션**: IFC 파일 수정 시 직접적인 파이썬 스크립트 생성보다는 `bim_actions.py`에 정의된 결정론적 JSON 액션 구조를 우선적으로 사용합니다.
- **데이터 격리**: Neo4j 쿼리 시 반드시 `taskId` 또는 `task_label`을 사용하여 사용자 데이터 간의 격리를 유지합니다.
