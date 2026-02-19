# 🚀 Alpha 실행 파일 빌드 가이드

## 📦 빌드된 파일 위치

```
Alpha/
├── dist/
│   ├── AlphaClient.app          ← GUI 클라이언트 (macOS 앱)
│   └── AlphaClient              ← 실행 파일 (단독)
├── build/                       ← 빌드 임시 파일
├── AlphaClient.spec            ← GUI 빌드 설정
├── AlphaServer.spec            ← 서버 빌드 설정
└── build.sh                    ← 빌드 스크립트
```

## 🎯 빌드 방법

### 방법 1: 빌드 스크립트 사용 (추천)
```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha
./build.sh
```

### 방법 2: 수동 빌드
```bash
cd /Users/nahyeonho/pythonWorkspace/Alpha

# GUI 클라이언트만
venv/bin/pyinstaller AlphaClient.spec --clean --noconfirm

# 서버도 빌드
venv/bin/pyinstaller AlphaServer.spec --clean --noconfirm
```

## 🚀 실행 방법

### GUI 클라이언트 실행

#### macOS
```bash
# 방법 1: Finder에서 더블클릭
open dist/AlphaClient.app

# 방법 2: 터미널에서
open dist/AlphaClient.app
```

#### 첫 실행 시 보안 경고
macOS에서 처음 실행 시 보안 경고가 나타날 수 있습니다:

1. "AlphaClient.app은(는) 확인되지 않은 개발자가 배포했기 때문에 열 수 없습니다" 메시지
2. **시스템 환경설정** > **보안 및 개인정보보호** 이동
3. **"확인 없이 열기"** 클릭

또는 터미널에서:
```bash
xattr -cr dist/AlphaClient.app
open dist/AlphaClient.app
```

### 서버 실행 (별도 필요)

GUI 클라이언트는 서버에 연결하므로 서버를 먼저 실행해야 합니다:

```bash
# QuestDB 시작
brew services start questdb

# Alpha 서버 시작 (개발 모드)
cd /Users/nahyeonho/pythonWorkspace/Alpha
venv/bin/uvicorn alpha_server.main:app --host 127.0.0.1 --port 8000
```

## 📁 배포 파일

### 단일 앱 배포
```bash
# AlphaClient.app만 복사하여 배포
cp -r dist/AlphaClient.app ~/Desktop/
```

### 전체 패키지 배포
```bash
# 압축하여 배포
cd dist
zip -r AlphaClient.zip AlphaClient.app
```

## 🔧 빌드 설정 커스터마이징

### 아이콘 추가
1. 아이콘 파일 준비 (`.icns` 형식, macOS)
2. `AlphaClient.spec` 수정:
```python
exe = EXE(
    ...
    icon='path/to/icon.icns',  # 아이콘 경로 지정
)
```

### 앱 이름 변경
`AlphaClient.spec` 수정:
```python
exe = EXE(
    ...
    name='MyCustomName',
)

app = BUNDLE(
    ...
    name='MyCustomName.app',
)
```

### 추가 파일 포함
`AlphaClient.spec`의 `datas` 섹션:
```python
datas=[
    ('alpha/gui.py', 'alpha'),
    ('config.json', '.'),  # 설정 파일 추가
    ('assets/', 'assets'),  # 폴더 전체 추가
],
```

## 🐛 문제 해결

### 빌드 실패 시
```bash
# 캐시 정리 후 재빌드
rm -rf build dist *.app
venv/bin/pyinstaller AlphaClient.spec --clean --noconfirm
```

### 실행 시 모듈 없음 오류
`AlphaClient.spec`의 `hiddenimports`에 추가:
```python
hiddenimports=[
    'PySide6.QtCore',
    'missing_module_name',  # 누락된 모듈 추가
],
```

### 서버 연결 실패
1. 서버가 실행 중인지 확인: `curl http://localhost:8000/`
2. QuestDB가 실행 중인지 확인: `brew services list | grep questdb`

## 📊 빌드 결과

### 파일 크기
- **AlphaClient.app**: ~38MB (모든 의존성 포함)
- **AlphaServer**: ~50MB (빌드 시)

### 포함된 라이브러리
- PySide6 (Qt GUI)
- requests (HTTP 통신)
- pandas, numpy (데이터 처리)

## 🌐 크로스 플랫폼 빌드

### Windows
```bash
# Windows에서 실행
pyinstaller AlphaClient.spec --clean --noconfirm
# 결과: dist/AlphaClient.exe
```

### Linux
```bash
# Linux에서 실행
pyinstaller AlphaClient.spec --clean --noconfirm
# 결과: dist/AlphaClient
```

## 📝 주의사항

1. **서버 의존성**: GUI는 서버 없이 실행되지만 기능을 사용하려면 서버 필요
2. **QuestDB**: 서버는 QuestDB에 의존
3. **네트워크**: 서버 주소가 `http://127.0.0.1:8000`으로 하드코딩됨
4. **첫 실행**: macOS 보안 설정으로 인해 추가 단계 필요

## 🎁 배포 체크리스트

- [ ] QuestDB 설치 가이드 포함
- [ ] 서버 실행 스크립트 제공
- [ ] 환경 설정 파일 (.env) 예시
- [ ] README.md 작성
- [ ] 라이선스 파일 포함
- [ ] 버전 정보 명시

---

**빌드 완료**: 2026-02-18  
**빌드 위치**: `/Users/nahyeonho/pythonWorkspace/Alpha/dist/AlphaClient.app`  
**실행 방법**: `open dist/AlphaClient.app`
