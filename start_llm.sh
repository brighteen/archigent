# vLLM 서버 시작 스크립트 (사용법: ./start_llm.sh [GPU_ID])
# GPU_ID는 0, 1, 2, 3 중 하나를 입력하세요 (기본값 0)
GPU_ID=${1:-3}

echo "Using GPU: $GPU_ID"
export CUDA_VISIBLE_DEVICES=$GPU_ID

# .env 파일에서 VLLM_MODEL_PATH 등을 읽어올 수 있도록 처리하거나 환경변수를 사용합니다.
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

MODEL_PATH=${VLLM_MODEL_PATH:-"/path/to/your/model"}

python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 1 \
    --max-model-len 8192 \
    --max-num-seqs 128 \
    --gpu-memory-utilization 0.70 \
    --enforce-eager \
    --disable-custom-all-reduce \
    --trust-remote-code \
    --served-model-name archigent-llm
