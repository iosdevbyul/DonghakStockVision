# 시장 데이터 제공업체 선정

조사 기준일: 2026-09-23. 공식 문서·원저장소를 기준으로 비교했습니다. 가격·인증·호출 한도·약관은 변경될 수 있으므로 실제 키 승인 시 다시 확인합니다. **현재 사용자가 KRX 키와 API 이용 승인이 없다고 확인하여 인증된 실데이터 호출은 하지 않았습니다.**

| 제공업체 | 무료·인증 | 요청 제한·품질 | 수정주가·거래대금 | 이용권·실시간 확장 |
| --- | --- | --- | --- | --- |
| KRX Open API **선정** | 공개 API, 키 및 API별 관리자 승인 필요 | 거래소 원천, 2010년 이후(KONEX 2013년 이후). 키당 일 10,000회 이하. 오류·누락 가능 | 일별 매매 OHLC·실제 거래대금. 해당 어댑터는 비수정 원천 가격, 수정계수 없음 | 비상업적 이용·제3자 제공 제한. 일별 통계 API이므로 실시간용 아님 |
| 금융위원회 주식시세정보 | 무료, 공공데이터포털 키·활용신청 | 개발계정 10,000. 기준일 다음 영업일 13시 이후 갱신 안내, 지연된 일봉 | OHLC·거래량·거래대금. 수정주가 제공 여부를 보장할 명세는 확인하지 못함 | 공공누리 4유형·재배포 제한. 실시간 미지원 |
| 한국투자증권 KIS | 계좌·서비스 신청·appkey/secret 필요; 세부 이용 조건 확인 필요 | 공식 브로커 API. 고객/환경별 유량 정책과 신규 고객 제한 공지 확인 필요 | 기간별 주가 API 제공; 수정주가 옵션·세부 포함 범위는 도입 시 계약 검증 | REST+WebSocket 실시간 지원. 시세 재배포·상업 이용 권리는 별도 확인 |
| FinanceDataReader / 네이버 | 라이브러리 무료·NAVER 경로는 키 없이 조회 | 비공식 웹 수집, 공개 SLA/보장된 호출 한도 없음. 사이트 변경 가능 | NAVER 어댑터의 일봉은 날짜·OHLC·거래량만 반환, **거래대금 없음**. 조정 방법론 보장 부족 | 라이브러리 라이선스가 데이터 이용권을 부여하지 않음. 공식 실시간 계약 아님 |

## 선정 근거

KRX는 실제 거래대금과 원천 출처가 명확하고 인증·데이터 스키마·이용 절차가 문서화되어 있습니다. Phase 1에서 계좌·주문 권한이 필요하지 않으며 추가 데이터 분석 라이브러리 없이 HTTP 어댑터로 구현할 수 있습니다. 사용 가능한 키가 없다는 이유로 비공식 데이터의 거래대금을 추정하거나 승인 절차를 우회하지 않습니다. 현재는 비상업적 로컬 개발·검증용 선정이며 향후 상업적 용도에 그대로 사용할 수 있다는 뜻이 아닙니다.

금융위원회 데이터도 명확한 공식 출처이나 배포 지연과 제한된 이용 조건을 고려하면 이번 KRX 직접 연동에 우선할 이점이 작습니다. 네이버/FDR 기본 응답은 필수 trading_value를 충족하지 못합니다. KIS는 향후 실시간 연결 후보지만 지금 계좌 인증·토큰·WebSocket 기능을 미리 추가하지 않습니다.

## KRX 요청과 필드 매핑

공식 공개 개발 페이지의 요청·출력 스키마를 확인하여 구현했습니다. 예제 키는 사용하지 않습니다. 실서비스 응답의 실제 수신 검증은 승인 후 남은 작업입니다.

```text
GET https://data-dbg.krx.co.kr/svc/apis/sto/{endpoint}?basDd=YYYYMMDD
AUTH_KEY: 환경 변수 KRX_API_KEY의 값
KOSPI  → stk_bydd_trd
KOSDAQ → ksq_bydd_trd
KONEX  → knx_bydd_trd
응답 배열: OutBlock_1
```

| KRX 필드 | 내부 계약 |
| --- | --- |
| ISU_CD | ticker (6자리 단축코드) |
| BAS_DD | trading_date |
| MKT_NM | market |
| TDD_OPNPRC / TDD_HGPRC / TDD_LWPRC / TDD_CLSPRC | open / high / low / close |
| ACC_TRDVOL / ACC_TRDVAL | volume / trading_value |

API는 시장 전체 하루를 반환하므로 종목 코드는 로컬에서 필터링합니다. 날짜별로 조회하므로 페이지 번호 기반 pagination은 없습니다. 원본 bytes는 그대로 보관합니다. 숫자 필드는 정수 또는 ASCII 숫자 문자열만 허용하며 `-`, 누락, 소수, 서식 변경은 검증 실패입니다. 응답 스키마 변화가 있으면 조용히 잘못된 값을 만들지 않고 명시적으로 실패하도록 했습니다.

## 공식 근거

- [KRX 서비스 목록·데이터 시작일](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)
- [KRX 인증키와 API별 승인 절차](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp)
- [KRX 이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp): 비상업적 이용, 일일 한도, 출처 표시, 제3자 제공 제한
- [KRX 유가증권 일별매매정보 개발 페이지](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC)
- [금융위원회 주식시세정보](https://www.data.go.kr/data/15094808/openapi.do): 비용·인증·갱신 지연·이용허락 범위
- [KIS API 개요](https://apiportal.koreainvestment.com/apiservice-summary), [공지사항](https://apiportal.koreainvestment.com/docs)
- [FinanceDataReader 공식 저장소](https://github.com/FinanceData/FinanceDataReader), [NAVER 어댑터 원본](https://github.com/FinanceData/FinanceDataReader/blob/master/src/FinanceDataReader/naver/data.py)

## 승인 후 검증 체크리스트

1. KOSPI 일별매매정보 승인 후 README의 live pytest를 실행합니다.
2. 실제 숫자 포맷·빈 응답·수신 지연·무거래 봉·원천 수정 상태를 공식 명세/응답과 대조합니다.
3. KOSDAQ/KONEX 사용 시 각각 승인 후 해당 시장의 종목으로 collect/query/validate를 실행합니다.
4. 거래정지·신규 상장·상장폐지 사례의 원본을 로컬에서 검토합니다. 재배포 제한 때문에 실제 응답을 공개 fixture로 커밋하지 않습니다.
5. 실데이터 차이가 발견되면 합성 fixture 회귀 테스트와 어댑터만 갱신합니다. Phase 2 구현으로 범위를 넓히지 않습니다.
