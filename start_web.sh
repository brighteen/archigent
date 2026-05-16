#!/bin/bash

# 아키전트 웹 서버 시작 스크립트
cd "$(dirname "$0")"

# Python 가상환경이 활성화되어 있다고 가정하거나, 환경변수의 python을 사용합니다.
python -m uvicorn server:app --host 0.0.0.0 --port 8001
