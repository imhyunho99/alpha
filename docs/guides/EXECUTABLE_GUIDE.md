# ✅ PyInstaller 빌드 완료

## 📦 빌드된 파일 위치

### 실행 파일
```
/Users/nahyeonho/pythonWorkspace/Alpha/dist/AlphaClient.app
```

### 전체 구조
```
Alpha/
├── dist/
│   ├── AlphaClient.app          ← 🎯 여기! (macOS 앱)
│   └── AlphaClient              ← 실행 파일
├── build/                       ← 빌드 임시 파일
├── AlphaClient.spec            ← GUI 빌드 설정
├── AlphaServer.spec            ← 서버 빌드 설정
├── build.sh                    ← 빌드 스크립트
└── run_client.sh               ← 실행 스크립트
```

## 🚀 실행 방법

### 1. 간단한 방법 (스크립트 사용)
```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha
./run_client.sh
```

### 2. 직접 실행
```bash
open /Users/nahyeonho/pythonWorkspace/Alpha/dist/AlphaClient.app
```

### 3. Finder에서 실행
1. Finder 열기
2. `/Users/nahyeonho/pythonWorkspace/Alpha/dist/` 이동
3. `AlphaClient.app` 더블클릭

## ⚠️ 첫 실행 시 (macOS 보안)

보안 경고가 나타나면:

```bash
# 터미널에서 실행
cd /Users/nahyeonho/pythonWorkspace/Alpha
xattr -cr dist/AlphaClient.app
open dist/AlphaClient.app
```

또는:
1. **시스템 환경설정** > **보안 및 개인정보보호**
2. **"확인 없이 열기"** 클릭

## 🔧 재빌드 방법

```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha

# 빌드 스크립트 사용
./build.sh

# 또는 수동
venv/bin/pyinstaller AlphaClient.spec --clean --noconfirm
```

## 📋 사전 요구사항

### 클라이언트만 실행 시
- 없음 (독립 실행 가능)

### 전체 기능 사용 시
1. **QuestDB 실행**
   ```bash
   brew services start questdb
   ```

2. **Alpha 서버 실행**
   ```bash
   cd /Users/nahyeonho/pythonWorkspace/Alpha
   venv/bin/uvicorn alpha_server.main:app
   ```

## 📊 빌드 정보

- **파일 크기**: 38MB
- **빌드 시간**: ~20초
- **포함 라이브러리**: PySide6, requests, pandas, numpy
- **플랫폼**: macOS (arm64)

## 🎁 배포 방법

### 단일 앱 배포
```bash
# 앱만 복사
cp -r dist/AlphaClient.app ~/Desktop/

# 또는 압축
cd dist
zip -r AlphaClient.zip AlphaClient.app
```

### 전체 패키지 배포
```bash
# 서버 포함 배포 패키지 생성
tar -czf Alpha-Package.tar.gz \
    dist/AlphaClient.app \
    alpha_server/ \
    requirements.txt \
    README.md
```

## 📚 관련 문서

- **BUILD_GUIDE.md** - 상세 빌드 가이드
- **README.md** - 프로젝트 개요
- **QUESTDB_MIGRATION.md** - QuestDB 통합 정보
- **DATA_PIPELINE_GUIDE.md** - 데이터 파이프라인

## 🐛 문제 해결

### "앱이 손상되어 열 수 없습니다"
```bash
xattr -cr dist/AlphaClient.app
```

### 서버 연결 실패
```bash
# 서버 상태 확인
curl http://localhost:8000/

# 서버 시작
cd /Users/nahyeonho/pythonWorkspace/Alpha
venv/bin/uvicorn alpha_server.main:app
```

### 빌드 실패
```bash
# 캐시 정리 후 재빌드
rm -rf build dist *.app
venv/bin/pyinstaller AlphaClient.spec --clean --noconfirm
```

---

**빌드 완료**: 2026-02-18 19:09  
**실행 파일**: `/Users/nahyeonho/pythonWorkspace/Alpha/dist/AlphaClient.app`  
**빠른 실행**: `open dist/AlphaClient.app`
