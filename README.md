# LOLDOV Backend

리플레이 영상 업로드 → 분석 작업 대기열 → 코칭 리포트·질의응답 API 서버 (FastAPI).

분석 파이프라인은 [AI](https://github.com/LOLDOV/AI), 화면은 [Frontend](https://github.com/LOLDOV/Frontend) 저장소를 쓴다.
세 저장소를 같은 상위 폴더에 나란히 클론한다.

```
loading/
├── AI/        # 분석 파이프라인 (vod, vlm, main.py), 결과는 AI/results/
├── Backend/   # 이 저장소
└── Frontend/  # coach/index.html (업로드·리포트 화면)
```

다른 위치라면 환경 변수 `LOLDOV_AI_DIR`, `LOLDOV_FRONTEND_DIR`로 지정한다.

## 실행

ffmpeg 필요 (`brew install ffmpeg`). Python 3.12 권장.

```
cd Backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.server:app --port 8000    # http://localhost:8000
```

## API 키 설정 (.env)

VLM 장면 분석과 질의응답에 쓰는 키는 AI 저장소의 `.env`에 둔다 (`AI/.env.example` 참고).
키가 없으면 규칙 기반 코칭으로 동작하고 질의응답은 꺼진다. 설정을 바꾸면 서버를 다시 켠다.

Gemini 키를 쓸 때 (OpenAI 호환 방식이라 변수 이름은 그대로 둔다):

```
DASHSCOPE_API_KEY=<Gemini API 키>
DASHSCOPE_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
QWEN_MODEL=gemini-3.6-flash
VLM_THINKING_TOKENS=8000    # Gemini는 '생각' 토큰도 한도에 포함돼 답변이 잘리므로 여유분을 준다
```

- 키는 각자 [Google AI Studio](https://aistudio.google.com) → Get API key 에서 발급받아도 된다.
- `.env`는 절대 GitHub에 올리지 않는다 (`.gitignore`에 포함됨). 키를 채팅·문서에 붙여 넣지 않는다.

### 사용 한도 주의

무료 키는 분당 요청 한도가 낮다. 한도(429)에 걸리면 질의응답은 10·20·30초 기다리며 최대 3번 다시 시도하고,
그래도 안 되면 "1분쯤 뒤에 다시 질문해 주세요"를 띄운다.
팀원 여럿이 동시에 쓰거나 시연 중 질문을 연달아 할 예정이면, AI Studio에서 결제를 연결해 유료 등급으로 올리거나
한도가 더 높은 팀원 키를 쓰는 게 안전하다.

## API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/api/config` | VLM 사용 가능 여부, 업로드 최대 크기, 허용 확장자 |
| POST | `/api/upload` | 영상 한 번에 업로드 (multipart) |
| POST | `/api/upload/init` | 조각 업로드 시작 `{filename, size}` → `{upload_id, chunk_bytes}` |
| PUT | `/api/upload/{id}/chunk?offset=N` | 조각 전송 (본문 = 바이트) |
| POST | `/api/upload/{id}/complete` | 업로드 완료 → 분석 대기열 등록 |
| GET | `/api/jobs` | 업로드 분석 목록 + 예시 경기 |
| GET | `/api/jobs/{id}` | 진행 상태, 완료 시 리포트(`report`) 포함 |
| POST | `/api/jobs/{id}/ask` | 코치에게 질문 (Qwen 키 필요) |
| GET | `/files/...` | 분석 결과 파일 (대표 프레임 이미지) |

- 조각 업로드는 터널(Cloudflare 등)의 요청 크기 제한(약 100MB)을 피하기 위한 것. 화면은 48MB씩 보낸다.
- 업로드 영상은 `uploads/`에 잠시 저장했다가 분석 후 삭제한다. 최대 4GB, 디스크 여유 1GB는 남긴다.
- 분석은 한 번에 하나씩 진행된다 (15분 영상 기준 약 3분). 서버가 분석 도중 꺼지면 해당 작업은 실패로 표시된다.

