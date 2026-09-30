# 로컬 self-hosted observability

Prometheus와 Grafana OSS를 Docker Compose로 독립 실행합니다. Python 실행 환경이나
실제 `.data` DB는 mount하지 않습니다. application metrics, exporter, dashboard는 아직
추가하지 않습니다. 외부 SaaS 계정도 필요하지 않습니다.

## 준비와 실행

Docker Engine/Desktop 및 Docker Compose v2가 필요합니다. 저장소 루트에서 실행하세요.
기존 `.env`를 덮어쓰지 말고 `.env.example`의 `GRAFANA_ADMIN_USER`와
`GRAFANA_ADMIN_PASSWORD` 항목만 기존 `.env`에 추가하세요. 비밀번호는 직접 생성한
로컬 전용 비밀번호를 지정합니다. 기본 비밀번호는 없으며 비어 있으면 Compose가 거부합니다.
`.env`는 Git에서 제외됩니다. 기존 KRX 키는 컨테이너 환경으로 전달되지 않습니다.

분리된 환경 파일을 원하면 Git에서 제외되는 `.env.observability`에 두 변수만 작성하고
아래 모든 Compose 명령에 `--env-file .env.observability`를 추가하세요.

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

`docker compose config`는 치환된 비밀번호를 출력할 수 있으므로 화면 공유·로그 수집 시
`--quiet`를 사용하세요. 비밀번호 파일과 config 출력은 커밋하지 마세요.

- Prometheus: http://localhost:9090
- Grafana OSS: http://localhost:3000 — 설정한 admin 사용자/비밀번호로 로그인

두 포트는 `127.0.0.1`에만 bind하며 외부 네트워크에는 공개하지 않습니다.
Grafana는 Prometheus가 healthy가 된 뒤 시작합니다. 초기 기동/이미지 다운로드에는 시간이
걸릴 수 있습니다. 이미 해당 포트를 사용하는 서비스가 있으면 먼저 충돌을 해결하세요.

## 구성과 검증

Prometheus는 `monitoring/prometheus/prometheus.yml`을 읽기 전용 mount하고 15초마다
자신의 `localhost:9090`을 scrape합니다. readiness endpoint는 `/-/ready`입니다.
Grafana는 provisioning을 읽기 전용 mount하고 `http://prometheus:9090`을 기본 datasource로
등록합니다. 이 주소는 Compose 내부 DNS를 사용합니다. Grafana의 healthcheck는
DB 상태를 포함하는 `/api/health`를 사용합니다. 두 healthcheck는 컨테이너의 `wget`을 사용합니다.

```bash
docker compose ps
curl --fail http://localhost:9090/-/ready
curl --fail http://localhost:3000/api/health
curl --fail 'http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22prometheus%22%7D'
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
```

최소 한 scrape 주기가 지난 뒤 `up{job="prometheus"}` 값이 `1`인지 확인합니다.
Grafana Connections → Data sources에서 기본 Prometheus datasource와 연결 상태를 확인하세요.
API로 확인할 때 다음 명령은 비밀번호를 대화형으로 요청합니다(사용자명을 변경했다면 수정).

```bash
curl --fail --user admin http://localhost:3000/api/datasources/uid/prometheus
curl --fail --user admin http://localhost:3000/api/datasources/uid/prometheus/health
```

## 유지 및 종료

`prometheus_data`는 `/prometheus`에, `grafana_data`는 `/var/lib/grafana`에 mount되는
프로젝트별 named volume입니다. 컨테이너를 재생성해도 지표와 Grafana 설정/사용자가 유지됩니다.
Prometheus는 기본 시간 retention(15일)을 사용하므로 무기한 보존 저장소는 아닙니다.
Grafana admin 환경변수는 빈 DB 최초 초기화에만 적용됩니다. 기존 volume의 비밀번호는
환경변수 변경으로 재설정되지 않으므로 Grafana의 비밀번호 변경 절차를 사용하세요.

```bash
# 컨테이너와 네트워크 종료; named volume 데이터 유지
docker compose down

# 주의: 이 Compose 프로젝트의 Prometheus/Grafana 저장 데이터까지 영구 삭제
docker compose down -v
```

`down -v`는 지표 이력·Grafana 사용자/설정까지 삭제합니다. 복구가 필요한 데이터가 있다면
먼저 백업하세요. 이 Compose는 `.data/market.sqlite3`, `.data/analysis.sqlite3`를 mount하거나
관리하지 않습니다.

## 이미지 선택

버전을 고정하여 자동 latest 갱신을 피합니다. Prometheus `v3.13.3`은 LTS 릴리스이고,
Grafana `13.2.3`은 OSS 이미지 `grafana/grafana`를 사용합니다. Grafana는 12.4부터
`grafana/grafana-oss` 저장소 업데이트를 중단했으므로 OSS 공식 안내에 따라 선택했습니다.

- [Prometheus 다운로드](https://prometheus.io/download/)
- [Prometheus Docker 설치](https://prometheus.io/docs/prometheus/latest/installation/)
- [Grafana OSS Docker 설치](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/)
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
