# Oracle Cloud 배포

맥북 없이 항상 접속되는 고정 주소로 웹사이트를 띄운다.

- 주소: https://168-110-28-55.sslip.io
- 서버: Oracle Cloud 도쿄, VM.Standard.E5.Flex (2 OCPU / 16GB), Ubuntu 24.04 x86
- 구성: `uvicorn`(127.0.0.1:8000, systemd `loldov` 서비스) ← `Caddy`(80/443, HTTPS 자동 발급)
- 코드: `/home/ubuntu/loldov/{AI,Backend,Frontend}` (GitHub 클론), 가상환경 `/home/ubuntu/loldov/.venv`

`sslip.io`는 IP를 이름으로 바꿔 주는 무료 DNS라 도메인 없이 HTTPS를 쓸 수 있다. IP가 바뀌면 주소도 바뀐다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `setup.sh` | 새 서버에서 처음 한 번: 패키지·클론·가상환경·서비스·Caddy·방화벽 |
| `update.sh` | GitHub 최신 코드 받고 서버 재시작 |
| `loldov.service` | systemd 서비스 (꺼지면 자동 재시작, 부팅 시 자동 시작) |
| `Caddyfile` | HTTPS 리버스 프록시 |
| `requirements.txt` | 서버에 설치한 패키지 버전 |

## 처음 설치

1. Oracle 콘솔에서 인스턴스 생성 (Ubuntu 24.04, SSH 개인 키 저장).
2. 서버에서 실행:
   ```
   git clone -q https://github.com/LOLDOV/Backend /tmp/Backend
   bash /tmp/Backend/deploy/oracle/setup.sh <공인IP>
   ```
3. 맥에서 API 키 파일 복사 (`.env`는 GitHub에 올리지 않는다, 설정은 Backend README의 "API 키 설정" 참고):
   ```
   scp -i <키파일> AI/.env ubuntu@<공인IP>:loldov/AI/.env
   ssh -i <키파일> ubuntu@<공인IP> 'chmod 600 ~/loldov/AI/.env && sudo systemctl restart loldov'
   ```
4. Oracle 콘솔 → 인스턴스의 Subnet → Security List → Add Ingress Rules:
   Source CIDR `0.0.0.0/0`, TCP, Destination Port `80,443`.
5. 예시 경기는 GitHub에 없으므로(소환사 이름 노출) `AI/results/LOL_*` 를 rsync로 따로 복사한다.
   결과 JSON 안의 프레임 경로가 맥 경로면 `/home/ubuntu/loldov` 로 바꿔야 이미지가 보인다.

## 코드 수정 후 반영

```
ssh -i <키파일> ubuntu@168.110.28.55 'bash ~/loldov/Backend/deploy/oracle/update.sh'
```

## 상태 확인

```
sudo systemctl status loldov          # 앱
journalctl -u loldov -n 50            # 앱 로그 (분석 오류 등)
sudo systemctl status caddy           # HTTPS
```

## 비용

무료 체험 크레딧(SGD 400, 2026-11-24 시작, 약 30일)으로 운영한다. Pay As You Go로 업그레이드하지 않으면 요금이 청구되지 않고,
체험이 끝나면 이 인스턴스(Always Free 아님)는 중지·삭제된다. 필요 없어지면 콘솔에서 Terminate (boot volume 함께 삭제).
