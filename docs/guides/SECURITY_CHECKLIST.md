# 보안 체크리스트 및 가이드

## ✅ 완료된 보안 조치

### 1. 환경 변수 보호
- ✅ `.env` 파일이 `.gitignore`에 포함됨
- ✅ DB 비밀번호를 하드코딩에서 환경변수로 이동
- ✅ `.env.example` 제공 (실제 값 없음)

### 2. .gitignore 강화
- ✅ 환경 변수 파일 (`.env`, `.env.local`)
- ✅ 모델 파일 (`.joblib`, `.h5`, `.pkl`)
- ✅ 데이터 파일 (`.csv`, `.json`, `.db`)
- ✅ 로그 파일 (`.log`)
- ✅ 인증서 및 키 (`.pem`, `.key`, `.cert`)

### 3. 민감 정보 제거
- ✅ 하드코딩된 DB 비밀번호 제거
- ✅ API 키 예시만 제공

---

## 🔒 GitHub Push 전 체크리스트

### 필수 확인 사항
```bash
# 1. .env 파일이 커밋되지 않았는지 확인
git status | grep .env
# 결과: 아무것도 나오지 않아야 함

# 2. .gitignore가 제대로 작동하는지 확인
git check-ignore .env
# 결과: .env 출력되어야 함

# 3. 민감한 파일이 staging되지 않았는지 확인
git status
# 결과: .env, *.joblib, *.csv 등이 없어야 함
```

### 이미 커밋된 민감 정보 제거 (필요시)
```bash
# Git 히스토리에서 .env 파일 완전 제거
git filter-branch --force --index-filter \
  "git rm --cached --ignore-unmatch .env" \
  --prune-empty --tag-name-filter cat -- --all

# 또는 BFG Repo-Cleaner 사용 (더 빠름)
# brew install bfg
# bfg --delete-files .env
# git reflog expire --expire=now --all && git gc --prune=now --aggressive
```

---

## 📋 Push 전 실행할 명령어

```bash
# 1. 현재 디렉토리로 이동
cd /Users/nahyeonho/pythonWorkspace/Alpha

# 2. Git 상태 확인
git status

# 3. .env 파일이 추적되지 않는지 확인
git ls-files | grep .env
# 결과: .env.example만 나와야 함

# 4. 안전한 파일만 추가
git add .
git status  # 다시 확인

# 5. 커밋
git commit -m "feat: Alpha v2 with improved ensemble model (71.79% accuracy)"

# 6. Push
git push origin main
```

---

## 🚨 절대 커밋하면 안 되는 것들

### 1. 환경 변수 및 설정
- ❌ `.env`
- ❌ `.env.local`
- ❌ `secrets/`
- ❌ `credentials/`

### 2. 데이터베이스
- ❌ `*.db`
- ❌ `*.sqlite`
- ❌ `questdb_data/`

### 3. 모델 파일 (용량 큰 파일)
- ❌ `*.joblib` (수백 MB)
- ❌ `*.h5` (딥러닝 모델)
- ❌ `*.pkl`

### 4. 데이터 파일
- ❌ `*.csv` (시세 데이터)
- ❌ `*.json` (백테스팅 결과)

### 5. 인증서 및 키
- ❌ `*.pem`
- ❌ `*.key`
- ❌ `*.cert`

---

## ✅ 커밋해도 되는 것들

### 1. 소스 코드
- ✅ `*.py`
- ✅ `*.md`
- ✅ `*.txt` (requirements.txt 등)

### 2. 설정 예시
- ✅ `.env.example`
- ✅ `.gitignore`

### 3. 문서
- ✅ `README.md`
- ✅ `*.md` (가이드 문서)

### 4. 스크립트
- ✅ `*.sh`
- ✅ `*.py`

---

## 🔐 추가 보안 권장사항

### 1. GitHub Secrets 사용 (CI/CD)
```yaml
# .github/workflows/deploy.yml
env:
  DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
  FINANCIAL_API_KEY: ${{ secrets.FINANCIAL_API_KEY }}
```

### 2. .env 파일 권한 설정
```bash
chmod 600 .env  # 소유자만 읽기/쓰기
```

### 3. Git Hooks 설정 (자동 검사)
```bash
# .git/hooks/pre-commit
#!/bin/bash
if git diff --cached --name-only | grep -q "^.env$"; then
    echo "Error: .env file should not be committed!"
    exit 1
fi
```

### 4. 정기적인 보안 스캔
```bash
# git-secrets 설치 및 사용
brew install git-secrets
git secrets --install
git secrets --register-aws
git secrets --scan
```

---

## 📝 README에 추가할 보안 안내

```markdown
## 🔒 보안 설정

### 환경 변수 설정
1. `.env.example`을 복사하여 `.env` 생성
   ```bash
   cp .env.example .env
   ```

2. `.env` 파일에 실제 값 입력
   ```
   DB_PASSWORD=your_actual_password
   FINANCIAL_API_KEY=your_actual_api_key
   ```

3. ⚠️ **절대 `.env` 파일을 Git에 커밋하지 마세요!**

### 주의사항
- `.env` 파일은 `.gitignore`에 포함되어 있습니다
- 민감한 정보는 절대 코드에 하드코딩하지 마세요
- API 키나 비밀번호는 환경 변수로 관리하세요
```

---

## 🎯 최종 확인

Push 전 다음을 확인하세요:

1. ✅ `.env` 파일이 `.gitignore`에 있음
2. ✅ `git status`에 `.env`가 나타나지 않음
3. ✅ 모델 파일 (`.joblib`)이 제외됨
4. ✅ 데이터 파일 (`.csv`, `.json`)이 제외됨
5. ✅ DB 비밀번호가 환경변수로 관리됨
6. ✅ `.env.example`에 실제 값이 없음

모두 확인되면 안전하게 Push 가능합니다! ✅
