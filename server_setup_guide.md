# Qwen 모델 서버 설정 가이드 (A100)

이 워크플로우를 A100 서버에 다운로드된 Qwen 모델과 함께 사용하려면, OpenAI 호환 API로 모델을 서빙해야 합니다. 성능면에서 **vLLM**을 추천드리며, 설정이 간편한 **Ollama**도 사용 가능합니다.

## 옵션 1: vLLM (A100 추천)
vLLM은 NVIDIA GPU에 맞게 최적화되어 있으며, 별도의 설정 없이 OpenAI와 호환되는 API 서버를 제공합니다.

### 1. vLLM 설치
```bash
pip install vllm
```

### 2. 서버 실행
`/path/to/your/qwen_model` 부분을 실제 모델이 위치한 경로로 변경하여 실행하세요.
```bash
python -m vllm.entrypoints.openai.api_server \
    --model /path/to/your/qwen_model \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9
```

## 옵션 2: Ollama (가장 간편한 방법)
### 1. Ollama 설치
[ollama.com](https://ollama.com)에서 설치 지침을 확인하세요.

### 2. Qwen 실행
```bash
ollama run qwen2.5:72b
```
Ollama는 기본적으로 `http://localhost:11434/v1` 주소에서 서빙됩니다.

---

## 워크플로우 설정 (.env)
서버가 준비되면, 로컬 프로젝트의 `.env` 파일을 다음과 같이 업데이트하십시오.

```env
# 예: 서버 IP가 192.168.1.100이고 vLLM을 사용하는 경우
OPENAI_BASE_URL=http://192.168.1.100:8000/v1
OPENAI_API_KEY=empty  # vLLM은 기본적으로 키가 필요하지 않음

# 모델 명칭 (서버에서 설정한 모델 태그와 일치해야 함)
LLM_MODEL_NAME=qwen2.5-72b-instruct
```
