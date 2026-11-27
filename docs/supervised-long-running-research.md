# R6 감독 기반 연구

2026-11-27 KST. **PARTIAL.** 승인된 과거 자료로 짧은 파일럿을 완료했고 R5의 데이터 차단을 진단했다. 감독 장시간 실행은 별도 승인 대상이다.

## 파일럿의 범위와 결과

기존 R3 학습·평가, R4 적응형 순위와 안정성, R1/R2 실패 근거, AnalysisStore, 네트워크 차단 작업자와 공용 잠금을 연결했다. 학습은 기존 16종목의 2022-01-03~2023-06-30, 개발은 별도 8종목의 2023-08-01~12-28이다. Source·Dataset·Plan PIN과 20bps 중립 밴드 및 기존 라벨을 유지했다. H1 두 슬롯 후 H5 두 슬롯을 등록했다. R3/R4와 동일한 구성을 후보에서 제외했으며 H20은 적격 앵커 부족으로 이번 파일럿의 비교에서 제외했다. 후보 선택에는 개발 자료만 사용했다. 원래 고정 평가를 그대로 보관했고 이번 파일럿의 새 고정 평가는 실행하지 않았다.

| 기간·개발 행수 | 후보 | 정답 / 오답 | Accuracy | BA | AP | Brier |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| H1 · 680 | Legacy | 327 / 353 | 48.09% | 48.05% | .50654 | .24973 |
| H1 · 680 | LogisticRegression C=1, Weekly | 367 / 313 | 53.97% | 54.05% | .56098 | .24868 |
| H1 · 680 | LogisticRegression C=1, Monthly | 314 / 366 | 46.18% | 45.98% | .44950 | .25193 |
| H5 · 720 | Legacy | 388 / 332 | 53.89% | 53.31% | .58419 | .24816 |
| H5 · 720 | LogisticRegression C=0.1, Weekly | 357 / 363 | 49.58% | 49.38% | .57583 | .24968 |
| H5 · 720 | RandomForest 깊이 6, Monthly | 337 / 383 | 46.81% | 46.54% | .53392 | .26450 |

H1 Weekly는 같은 표본에서 Legacy보다 40건, 모멘텀보다 20건, 학습 사전확률·다수 클래스보다 24건 더 맞혔다. 종목군의 Legacy 대비 BA 차이는 +3.21%p와 +7.02%p였다. 시간 블록의 16·17개 비중첩 앵커는 기존 최소에 미달했다. H5는 두 종목군 각 14개와 시간 블록 6·7개로 모두 부족했다. H5 Weekly의 상승 Recall은 60.26%에서 51.79%로 내려갔고 Monthly forest의 FPR은 53.64%에서 56.67%로 올라갔다. 두 H5 후보 모두 같은 모집단의 Legacy에 뒤졌다. 적격 개선 후보는 0개였고 안정성 판정 4건에서 Legacy를 유지했다.

직전 결과를 뺀 같은 후보 집합을 반사실 비교에 사용했다. 후속 선택 2건 모두 ID가 달라졌다. H1은 `5e94e1f23e04` 대신 Monthly `71e5f1aa6a7f`를, H5는 `861aa865a168` 대신 Monthly forest `82cd4ee930f8`를 실제 평가했다. 두 후속 후보 모두 같은 기간의 첫 후보보다 Accuracy가 낮았고 Brier가 악화됐다. 실제 결과가 탐색을 바꿨다는 증거와 예측 향상은 따로 판단한다.

실제 신규 fit 4회, 평가 4회, 개발 예측 2,800 모델–행, 체크포인트 정확 재사용 1회, 중복 설정 0건과 실행 실패 0건을 보존했다. 시도 5회와 감독 창 3회에서 체크포인트 중단, 감독자 중단 요청, 같은 계획으로 재시작을 각각 실행했다. 작업자 경과시간 합은 64.38초, 감독 창 합은 65.28초, 작업자 CPU 시간 합은 59.67초다. 최대 RSS는 273,912 KiB, 체크포인트 4개는 655,047바이트, 최초 보고서 내보내기 전 연구 루트는 4,276,060바이트였다. 이 실행은 CPU 1스레드, AC, RSS 1 GiB 상한과 디스크/전원 보호 가드를 사용했다. 다른 프로젝트 작업과 스케줄은 그대로 유지했다.

## 수동 감독과 복구

파일럿에 후보·fit 각각 최대 4개, 누적 165초, 작업자당 최대 60초, 실패 최대 2회를 적용했다. 원래 작업자 보호 한도, 연구 디스크 512 MiB 상한 및 디스크 여유 1 GiB를 유지한다. 피팅 완료 메타데이터와 SHA가 남아 있으면 새 프로세스가 동일 모집단·설정을 검증한 뒤 평가를 계속한다. 감독자가 감사 중에 사라지면 작업자 잠금이 풀린 뒤 같은 미완료 슬롯을 복구하며 사라진 시도에는 예약 상한을 청구한다. 메타데이터 없는 체크포인트는 보호 경로에서 멈추고 원본을 보존한다. 이 비정상 경로는 격리 테스트로 검증했다.

```sh
dsv autonomous-research supervised-register --root "$R6_ROOT" --pins-root "$R6_PINS" \
  --request "$REQUEST" --request-pin-file "$REQUEST_PIN"
dsv autonomous-research supervised-run --root "$R6_ROOT" --plan-pin-file "$R6_PINS/plan.pin" \
  --steps 1 --checkpoint-only
dsv autonomous-research supervised-status --root "$R6_ROOT" --plan-pin-file "$R6_PINS/plan.pin"
dsv autonomous-research supervised-stop --root "$R6_ROOT" --plan-pin-file "$R6_PINS/plan.pin" \
  --window "$ACTIVE_WINDOW_ID"
dsv autonomous-research supervised-run --root "$R6_ROOT" --plan-pin-file "$R6_PINS/plan.pin" \
  --steps 3
dsv autonomous-research supervised-status --root "$R6_ROOT" --plan-pin-file "$R6_PINS/plan.pin" \
  --report "$R6_ROOT/new-status.json"
```

`supervised-stop`은 현재 작업자의 안전한 경계에서 멈춘다. `supervised-status`는 조회만 수행한다. Historical 실행은 R5의 상태와 독립적이다. Prospective 상태는 #250 `preflight`와 현재 운영 저장소에서 읽는다. 상태 보고서의 원본 계보, 선택과 반사실 비교, 정오답 행, 자원 감사 및 중단 기록은 R7 조회에 사용할 수 있다. 비공개 보고서는 운영 API에 자동 공개하지 않는다.

### 별도 승인용 다음 연구 창

기존 4개 trial을 다음 Plan에 보존했고 동일 후보는 다음 계획에서 모두 제외됐다. 제안은 누적 **240초·최대 12 fit과 12 평가**, H1/H5 교차 6개씩, 작업자당 60초, 실패 한도 2회다. 파일럿의 작업자 최대 13.70초로 12개 시도의 기준 비용을 약 164초로 산정했다. Gemma, KRX 요청, 스케줄 변경, 승격과 거래는 각각 0으로 제한한다. 제안 Plan의 fit과 시도는 모두 0이다. 1시간/120 fit 확대는 이번 측정과 적격 개선 근거가 제한돼 보류한다.

별도 명시 승인 후 제안 Plan이 기록한 실행 리비전과 동일 구현으로 실행한다. 고정 리비전은 설치 패키지의 provenance에 전달하며 원래 구현 해시와 의존성 검증을 유지한다. `previous_run`은 파일럿의 별도 Root·PIN에 바인딩한다. 더 새로운 실행 코드로 진행하는 경우 같은 제안 요청을 새 비공개 Root·PIN에 등록하고 원본 제안을 보존한다.

```sh
DSV_CODE_REVISION="$APPROVED_EXECUTION_REVISION" \
  dsv autonomous-research supervised-run --root "$APPROVED_PROPOSAL" \
  --plan-pin-file "$APPROVED_PROPOSAL_PIN" --steps 12
```

## R5 차단의 실제 원인

기존 LaunchAgent는 평일 19:00 KST에 실행되도록 로드돼 있다. 확인 시 대기 중이며 마지막 종료 코드 0, 설정 enabled였다. 운영 checkout에서 scheduler 진입점과 권한/runner 코드는 기준 main과 일치했다. 11월 26일 배치 원본과 현재 점검 모두 `provider_permission_expired_or_not_yet_valid`를 반환했다. **로컬 recurring policy의 만료는 2026-11-22 00:00 KST**다. 허용 거래일은 11월 13일과 16~20일이었다. 공급자 문서에 기록된 승인 기간은 2027-09-28까지다. 로컬 정책의 지연 상한은 7일이고 일 1회 한도에서 현재 사용 0·잔량 1이다.

운영 저장소는 32거래일, 30,046행, 고유 종목 942개다. 최신 거래일은 11월 19일이고 원본 수집 시각은 11월 20일 19:00:22 KST다. 보존 달력에서 11월 20일과 23~26일의 가격 행이 없다. 11월 27일 낮 점검 기준 당일과 30일의 관측은 각각 완료 시점 이후 확인이 필요하다. 기존 운영 특징 최소 35관측, H1/H5 기록 시한, H20 목표까지의 검증 달력은 미충족이다. R5 보존 모델은 3개이고 신규 예측·평가·독립 적격 앵커는 각각 0이다.

복구 승인 범위는 기존 KOSPI 전체 시장 API의 읽기 전용 요청이다. 11월 27일에 승인과 검증이 끝나는 경우 누락된 11월 20일·23~26일 중 실행 시점 기준 1~7일 지연 조건을 각각 확인한다. 기간을 지난 날짜는 별도 backfill 승인 대상이다. 신선한 입력은 달력으로 확인된 11월 27일·30일 두 거래일, 11월 30일~12월 2일 KST 범위의 별도 유한 정책을 검토할 수 있다. 각각 원래 일 1회, 거래일당 1회, 재시도 0회, 요청당 30초·2 MiB 상한을 적용한다. 정책은 실제 승인 시각 이후부터 유효해야 한다. 실제 첫 예측에는 H20 목표까지의 추가 검증 달력과 독립 시간 증거가 필요하다.

승인된 정책과 별도 PIN을 갖춘 후 `dsv krx-scheduler preflight --config "$APPROVED_CONFIG" --config-pin "$APPROVED_CONFIG_PIN"`으로 확인한다. 승인된 최신 거래일의 단일 수집 명령은 `dsv krx-scheduler run-due --execute --max-runs 1 --config "$APPROVED_CONFIG" --config-pin "$APPROVED_CONFIG_PIN"`이다. 이전 누락일은 승인된 일별 계획으로 [기존 단일 수집·ingest 절차](krx-production-readiness.md)를 사용한다. R6의 정책 갱신, 신규 KRX 요청, 운영 설정 변경은 각각 0건이었다.
