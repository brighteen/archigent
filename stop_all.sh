#!/bin/bash

echo "🛑 ArchiGent 서버 및 관련 프로세스 종료를 시작합니다..."

# 1. 아키전트 웹 서버 종료
echo "🌐 웹 서버(Uvicorn) 종료 중..."
pkill -if "uvicorn server:app"
sleep 1

# 2. vLLM 모델 서버 및 워커 종료
echo "🤖 모델 서버(vLLM) 및 GPU 워커 종료 중..."
# 메인 API 서버 및 모든 관련 프로세스 종료 (대소문자 무시)
pkill -9 -if "vllm"
pkill -9 -if "multiprocessing.spawn"
# 명시적으로 VLLM::Worker 및 EngineCore 패턴 종료
pkill -9 -f "VLLM::"

# 3. 남아있는 좀비 프로세스 확인 (포트 기준)
echo "🔍 잔여 프로세스 정리 (8000, 8001 포트)..."
fuser -k 8000/tcp 2>/dev/null
fuser -k 8001/tcp 2>/dev/null

# 4. GPU 사용 중인 잔여 프로세스 강제 종료 (현재 사용자 프로세스만)
echo "🧹 GPU 잔여 메모리 해제 중..."
fuser -k /dev/nvidia0 2>/dev/null
fuser -k /dev/nvidia1 2>/dev/null
fuser -k /dev/nvidia2 2>/dev/null
fuser -k /dev/nvidia3 2>/dev/null

echo "✅ 모든 서버 및 GPU 워커가 정리되었습니다. nvidia-smi로 메모리 해제를 확인하세요."
