import json
import os
import shutil
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import RateLimitError
from pydantic import BaseModel

# 리플레이 영상 업로드 -> 분석 -> 코칭 리포트 웹 서버
#   uvicorn app.server:app --port 8000     (Backend 폴더에서 실행)
# 분석 파이프라인은 AI 저장소, 화면은 Frontend 저장소를 쓴다. 기본값은 같은 상위 폴더에 나란히 둔 클론
# (loading/AI, loading/Backend, loading/Frontend)이고, LOLDOV_AI_DIR / LOLDOV_FRONTEND_DIR 로 바꿀 수 있다.
# 업로드 영상은 uploads/ 에 잠시 저장하고, 분석이 끝나면 삭제한다 (KEEP_UPLOADS=True 로 보관).
# 결과는 AI/results/jobs/<작업ID>/ 에 남고, AI/results/ 아래 기존 분석(LOL_*)은 예시 경기로 함께 보여준다.

ROOT = Path(__file__).resolve().parent.parent
AI_DIR = Path(os.getenv('LOLDOV_AI_DIR', ROOT.parent / 'AI')).resolve()
FRONTEND_DIR = Path(os.getenv('LOLDOV_FRONTEND_DIR', ROOT.parent / 'Frontend')).resolve()
STATIC_DIR = FRONTEND_DIR / 'coach'
UPLOAD_DIR = ROOT / 'uploads'
RESULT_DIR = AI_DIR / 'results'
JOB_DIR = RESULT_DIR / 'jobs'

sys.path.insert(0, str(AI_DIR))
import main as pipeline  # noqa: E402
from report.build_site import video_payload  # noqa: E402
from vlm.coach import answer_question  # noqa: E402
from vlm.scene_analyzer import vlm_available  # noqa: E402

ALLOWED_EXT = {'.mp4', '.webm', '.mkv', '.mov'}
MAX_UPLOAD_BYTES = 4 * 1024 ** 3
CHUNK = 8 * 1024 * 1024
KEEP_UPLOADS = False
ASK_RETRY_WAITS = (10, 20, 30)  # 무료 API 키의 분당 요청 한도(429)에 걸리면 기다렸다가 다시 묻는다
STAGE_LABELS = {
    'queued': '대기 중', 'uploaded': '업로드 완료', 'scan': '영상 스캔 (HUD 인식)', 'player': '플레이어 팀 판별',
    'scenes': '주요 장면 선정', 'frames': '대표 프레임 추출', 'vlm': 'VLM 장면 분석', 'coach': '코칭 리포트 작성',
    'done': '완료', 'error': '실패',
}

app = FastAPI(title='LOLDOV Coach')
executor = ThreadPoolExecutor(max_workers=1)  # 분석은 CPU를 많이 써서 한 번에 하나씩
lock = threading.Lock()


def job_path(job_id):
    return JOB_DIR / job_id / 'job.json'


def read_job(job_id):
    path = job_path(job_id)
    if not path.is_file():
        return None
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def write_job(job_id, **fields):
    with lock:
        job = read_job(job_id) or {}
        job.update(fields)
        job['stage_label'] = STAGE_LABELS.get(job.get('stage'), job.get('stage'))
        path = job_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
        return job


def resolve_result_dir(job_id):
    """Uploaded jobs live in results/jobs/<id>; sample analyses in results/<name>."""
    if '/' in job_id or '..' in job_id:
        return None
    for candidate in (JOB_DIR / job_id, RESULT_DIR / job_id):
        if (candidate / 'scenes.json').is_file() or (candidate / 'job.json').is_file():
            return candidate
    return None


def frame_url(path):
    rel = Path(path).resolve().relative_to(RESULT_DIR.resolve())
    return '/files/' + rel.as_posix()


def process(job_id, video_path, use_vlm):
    out_dir = JOB_DIR / job_id

    def progress(stage, fraction):
        write_job(job_id, status='running', stage=stage, progress=round(fraction, 3))

    try:
        write_job(job_id, status='running', stage='scan', progress=0.0, started_at=datetime.now().astimezone().isoformat())
        pipeline.run(video_path, use_vlm=use_vlm, out_dir=out_dir, progress=progress)
        write_job(job_id, status='done', stage='done', progress=1.0, finished_at=datetime.now().astimezone().isoformat())
    except Exception as exc:
        traceback.print_exc()
        message = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: {exc}'
        write_job(job_id, status='error', stage='error', error=message)
    finally:
        if not KEEP_UPLOADS:
            Path(video_path).unlink(missing_ok=True)


@app.get('/api/config')
def config():
    return {'vlm_available': vlm_available(), 'max_upload_gb': MAX_UPLOAD_BYTES / 1024 ** 3,
            'allowed': sorted(ALLOWED_EXT)}


@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or '').suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f'지원하지 않는 형식입니다 ({", ".join(sorted(ALLOWED_EXT))})')
    free = shutil.disk_usage(UPLOAD_DIR.parent).free
    job_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / f'{job_id}{ext}'

    size = 0
    try:
        with dest.open('wb') as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES or size > free - 1024 ** 3:
                    raise HTTPException(413, '파일이 너무 크거나 서버 디스크 공간이 부족합니다.')
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    use_vlm = vlm_available()
    write_job(job_id, id=job_id, filename=file.filename, size_mb=round(size / 1024 ** 2, 1),
              created_at=datetime.now().astimezone().isoformat(), status='queued', stage='queued',
              progress=0.0, use_vlm=use_vlm)
    executor.submit(process, job_id, dest, use_vlm)
    return read_job(job_id)


# 조각 업로드: 터널(Cloudflare 등)의 요청 크기 제한(약 100MB)을 피하려고 영상을 나눠 받는다
pending_uploads = {}


class UploadInit(BaseModel):
    filename: str
    size: int


@app.post('/api/upload/init')
def upload_init(body: UploadInit):
    ext = Path(body.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f'지원하지 않는 형식입니다 ({", ".join(sorted(ALLOWED_EXT))})')
    free = shutil.disk_usage(UPLOAD_DIR.parent).free
    if body.size <= 0 or body.size > MAX_UPLOAD_BYTES or body.size > free - 1024 ** 3:
        raise HTTPException(413, '파일이 너무 크거나 서버 디스크 공간이 부족합니다.')
    job_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f'{job_id}{ext}.part'
    path.write_bytes(b'')
    pending_uploads[job_id] = {'path': path, 'filename': body.filename, 'size': body.size, 'received': 0}
    return {'upload_id': job_id, 'chunk_bytes': CHUNK * 6}


@app.put('/api/upload/{upload_id}/chunk')
async def upload_chunk(upload_id: str, offset: int, request: Request):
    up = pending_uploads.get(upload_id)
    if up is None:
        raise HTTPException(404, '업로드 세션이 없습니다. 처음부터 다시 올려 주세요.')
    if offset != up['received']:
        return {'received': up['received']}  # 재시도로 중복된 조각은 무시
    data = await request.body()
    if up['received'] + len(data) > up['size']:
        raise HTTPException(400, '파일 크기가 맞지 않습니다.')
    with up['path'].open('ab') as out:
        out.write(data)
    up['received'] += len(data)
    return {'received': up['received']}


@app.post('/api/upload/{upload_id}/complete')
def upload_complete(upload_id: str):
    up = pending_uploads.get(upload_id)
    if up is None:
        raise HTTPException(404, '업로드 세션이 없습니다.')
    if up['received'] != up['size']:
        raise HTTPException(400, f'아직 전송되지 않은 부분이 있습니다 ({up["received"]}/{up["size"]}).')
    del pending_uploads[upload_id]
    dest = up['path'].with_suffix('')
    up['path'].replace(dest)
    use_vlm = vlm_available()
    write_job(upload_id, id=upload_id, filename=up['filename'], size_mb=round(up['size'] / 1024 ** 2, 1),
              created_at=datetime.now().astimezone().isoformat(), status='queued', stage='queued',
              progress=0.0, use_vlm=use_vlm)
    executor.submit(process, upload_id, dest, use_vlm)
    return read_job(upload_id)


@app.get('/api/jobs')
def list_jobs():
    jobs = []
    if JOB_DIR.is_dir():
        for path in JOB_DIR.glob('*/job.json'):
            job = read_job(path.parent.name)
            if job:
                jobs.append(job)
    jobs.sort(key=lambda j: j.get('created_at', ''), reverse=True)
    samples = [{'id': d.name, 'filename': f'{d.name} (예시 경기)', 'status': 'done', 'sample': True}
               for d in sorted(RESULT_DIR.glob('LOL_*')) if (d / 'scenes.json').is_file()]
    return {'jobs': jobs, 'samples': samples}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    result_dir = resolve_result_dir(job_id)
    if result_dir is None:
        raise HTTPException(404, '분석 결과를 찾을 수 없습니다.')
    job = read_job(job_id) if result_dir.parent == JOB_DIR else {'id': job_id, 'status': 'done', 'sample': True}
    if job.get('status') == 'done':
        payload = video_payload(result_dir, url_for=frame_url)
        if payload is None:
            raise HTTPException(500, '결과 파일이 불완전합니다.')
        if not payload.get('coaching'):
            from vlm.coach import build_coaching
            payload['coaching'] = build_coaching(result_dir)
        job['report'] = payload
    return job


class Question(BaseModel):
    question: str
    history: list = []


@app.post('/api/jobs/{job_id}/ask')
def ask(job_id: str, body: Question):
    result_dir = resolve_result_dir(job_id)
    if result_dir is None:
        raise HTTPException(404, '분석 결과를 찾을 수 없습니다.')
    if not vlm_available():
        raise HTTPException(409, '질의응답은 API 키(.env)가 설정되어야 사용할 수 있습니다.')
    if not body.question.strip():
        raise HTTPException(400, '질문을 입력하세요.')
    for wait in (*ASK_RETRY_WAITS, None):
        try:
            return {'answer': answer_question(result_dir, body.question, body.history)}
        except RateLimitError:
            if wait is None:
                raise HTTPException(429, '질문이 몰려 AI 사용 한도에 걸렸습니다. 1분쯤 뒤에 다시 질문해 주세요.')
            time.sleep(wait)
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(502, f'LLM 호출 실패: {type(exc).__name__}: {exc}')


@app.get('/')
def index():
    # 화면을 수정하면 바로 보이도록 브라우저가 예전 페이지를 캐시해 두지 않게 한다
    return FileResponse(STATIC_DIR / 'index.html', headers={'Cache-Control': 'no-cache'})


def recover_interrupted_jobs():
    # 서버가 분석 도중 꺼졌던 작업은 실패로 표시한다
    if not JOB_DIR.is_dir():
        return
    for path in JOB_DIR.glob('*/job.json'):
        job = read_job(path.parent.name)
        if job and job.get('status') in ('queued', 'running'):
            write_job(job['id'], status='error', stage='error', error='서버가 재시작되어 분석이 중단되었습니다. 다시 업로드하세요.')


recover_interrupted_jobs()
RESULT_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/files', StaticFiles(directory=RESULT_DIR), name='files')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
