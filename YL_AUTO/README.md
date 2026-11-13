> v12 (2026-11-13): 원통이 아닌 형상도 축/원점(0,0,0)/INNER/OUTER 자동 판별 (`shape_mode: general`) - `수정내역_v12.md` 참고
> v11 (2026-11-13): 모델당 ODB 1개에 모든 스웨징 x 하중방향 (`odb_mode: single`) - `수정내역_v11.md` 참고
> v10 (2026-11-13): Abaqus Job 이 2초 만에 끝나던 문제 수정 - `수정내역_v10.md` 참고
> v9 (2026-11-12): bushing.stp 'tetmesh 0개' 오류 수정 - `수정내역_v9.md` 참고. 권장: `pip install gmsh`

# YL_AUTO : 부싱 정특성 자동 해석 파이프라인

모델(CAD)과 물성(시험데이터 또는 Ogden 계수)을 넣으면 아래 과정을 사람 손 없이 끝까지 진행한다.

> **처음 쓰는 사람은 [사용설명서.md](사용설명서.md) 부터 읽을 것.** 설치 → 점검 → 실행 → 결과 확인 → 문제 해결 순서로 정리되어 있다.

```
CAD(.stp 등) ──► HyperMesh 배치 (YL_V5_batch.tcl = YL_V4 메시 알고리즘 그대로)
                  └► Tria 품질 최적화 → C3D10H Tetra → Abaqus INP 출력
             ──► Python: 메시 검사·보정 → 축 +Y 정렬 → INNER/OUTER 자동 선택 → LOAD_RP(0,0,0)
                  └► 메시 전용 INP + Job INP (스웨징 5단계 × X/Y/Z)
             ──► Abaqus 실행 (.odb) → abaqus python 으로 ODB 에서 LOAD_RP 하중-변위 추출
             ──► 강성 계산 + 자동 QC + 학습용 데이터셋(summary.csv, curves.csv) + 그래프
```

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `run_pipeline.py` | 전체 실행기 (단계별 실행·재시작 가능) |
| `config_abaqus.json` | 연구실 PC 실사용 설정 (HyperMesh + Abaqus) |
| `config_abaqus_smoketest.json` | HyperMesh 없이 Abaqus·ODB 추출만 점검하는 4 Job 설정 |
| `check_env.py`, `run_all.bat` | 환경 점검, 실행용 배치 파일 |
| `collect_logs.py` | 실행 후 점검용 파일만 zip 으로 묶음 (ODB 제외, 이 파일만 보내면 됨) |
| `hypermesh/YL_V5_batch.tcl` | YL_V4 메시 코드 + 배치 래퍼 |
| `core/inp_mesh.py` | INP 읽기, 요소 검사·보정, 축 정렬, INNER/OUTER 선택 |
| `core/ogden.py` | Ogden N=3 피팅(시험데이터)과 Drucker 안정성 점검 |
| `core/inp_writer.py` | 메시 전용 INP와 Job INP 작성 |
| `core/runners.py` | hmbatch / Abaqus / CalculiX 실행, 완료 판정 |
| `core/results.py` | 강성 계산, QC, 데이터셋, 그래프 |
| `abaqus_scripts/extract_odb.py` | ODB → CSV (Abaqus Python, 2019 Py2.7 / 2024 Py3 공용) |
| `test/` | 가상 부싱 생성기, CalculiX 검증 설정과 결과 |

## 해석 조건 정의

| 항목 | 정의 |
|---|---|
| 축 | 원본 축(auto 판별 또는 지정)을 **무조건 +Y** 로 회전. 반경 방향 = XZ 평면 |
| LOAD_RP | (0,0,0) 에 생성, 노드번호 = 고무 최대 노드번호 + 1 |
| INNER | 바깥 법선이 축을 향하는 경계면 중 최소 반경 띠 (r ≤ r_min + inner_tol) → `*Rigid Body, ref node=LOAD_RP, tie nset=INNER` (연구실 modeling_AN_M1.inp 방식). `inner_constraint: "coupling"` 이면 `*Coupling, *Kinematic` |
| OUTER | 바깥 법선이 바깥을 향하는 경계면 중 최대 반경 띠 (r ≥ r_max − outer_tol) |
| 스웨징 | Step-1, OUTER 노드에 반경 변위 u_r = SW (XZ 평면), u_y = 0. SW = 0, +1, +2, −1, −2 mm. **+ = 바깥으로 늘어남, − = 안쪽 압축** (`swaging_outward_positive` 로 반전 가능) |
| 정특성 | Step-2, OUTER 고정 상태에서 LOAD_RP 를 X/Y/Z 로 변위 제어 (기본 2 mm), 나머지 자유도 0 |
| 재료 | `*Hyperelastic, ogden, n=3` (Abaqus 정의 μi, αi, D1~D3) |
| 요소 | C3D10H (2차 사면체 하이브리드) |
| 기타 | NLGEOM=YES, Job 이름 `모델_E요소크기_SW스웨징_방향` 예: `M01_Ep2p0_SWm1p0_XP` |

연구실 실행기 호환용으로 `rp`, `rp_node`, `sw` 셋 이름도 같이 만든다(`lab_aliases`).

## 실행 방법 (연구실 PC, Windows)

1. Python 3.9 이상에 `pip install numpy scipy matplotlib`
2. `config_abaqus.json` 에서 `hypermesh.exe`, `abaqus.cmd` 경로 확인
3. 환경 점검: `python check_env.py config_abaqus.json`
4. **Abaqus·ODB 점검 먼저**: `run_all.bat smoke` (= `python run_pipeline.py config_abaqus_smoketest.json`) → `smoke_out/qc_report.md` 확인
   끝나면 `smoke_out/YL_logs_날짜.zip` 이 생긴다. 문제 있으면 이 zip 을 보내면 된다.
5. CAD를 `models/` 에 넣고 물성 CSV를 `materials/` 에 넣은 뒤 전체 실행: `python run_pipeline.py config_abaqus.json`

단계만 따로 실행할 수 있다.

```
python run_pipeline.py config_abaqus.json --stage mesh,build        # INP 까지만
python run_pipeline.py config_abaqus.json --stage run,extract,report # 해석과 결과만
python run_pipeline.py config_abaqus.json --only M01 --force         # 특정 모델 다시
```

이미 끝난 Job 은 건너뛰므로 중간에 끊겨도 같은 명령으로 이어서 돌릴 수 있다.

## 물성 입력

- 시험데이터 방식: 2열 CSV `nominal_strain, nominal_stress(MPa)`. 단축(uniaxial), 등이축(biaxial), 평면(planar) 중 있는 것만 넣으면 된다. 세 가지를 모두 넣을수록 대변형 예측 오차가 줄어든다.
- 피팅은 상대오차 최소화, 300개 시작점 탐색 후 상위 8개 정밀 수렴, μi·αi > 0 강제, `stable_range`(기본 λ 0.2~5) 안에서 Drucker 안정 조건을 벌점으로 강제한다.
- 피팅 결과(R², 오차, 안정 구간)는 `auto_out/materials_ogden3.json` 에 저장된다.
- 계수를 이미 알면 `"ogden3": {"mu": [...], "alpha": [...]}` 로 직접 넣는다. 현재 기본 물성 `FJ5045` 가 이 방식이다 (modeling_AN_M1.inp 의 값 그대로: μ = 0.75744, 0.0013173, 0.00049209 / α = 1.3475, 11.347, −5.3698 / D1 = 0.0079104 → μ0 = 0.7592 MPa, K0 = 252.8 MPa, ν ≈ 0.4985).
- 압축성: `bulk_modulus`(MPa) 또는 `poisson` 또는 `D` 직접 입력. 미입력 시 D=0(완전 비압축).
- `materials/TEST_RUBBER_*.csv` 는 검증용 **가상 데이터**다. 실제 시험값으로 교체해야 한다.

## 결과물

| 파일 | 내용 |
|---|---|
| `auto_out/<tag>/run/*.odb` | Abaqus 결과 |
| `auto_out/<tag>/run/<job>_RP.csv` | LOAD_RP 증분별 U1~U3, RF1~RF3 |
| `auto_out/<tag>/model_info.json` | 축 정렬, INNER/OUTER 통계, 메시 품질, 형상 특징값 |
| `auto_out/<tag>/<tag>_FD.png` | 방향별 하중-변위 곡선 (스웨징별) |
| `auto_out/dataset/summary.csv` | Job 1개 = 1행: 형상 특징값 + 물성 + 스웨징 + 방향 → 강성(K_secant, K_initial, K_25/50/75, K_tangent_end) |
| `auto_out/dataset/curves.csv` | 학습용 전체 곡선 (job, u, F) |
| `auto_out/qc_report.md` | 자동 QC 결과 |

## 자동 QC 항목

- Abaqus 정상 종료 (`.sta` 의 COMPLETED SUCCESSFULLY, `***ERROR` 검색)
- 하중 스텝 끝까지 진행(step time = 1)과 목표 변위 도달
- 곡선 점 수, 하중 단조 증가, 강성 > 0
- 스웨징 후 LOAD_RP 잔류 반력
- 모델 단위: Kx/Kz 대칭성(축대칭 형상이면 1에 가까워야 함), 스웨징에 따른 강성 경향

## 학습 데이터 늘리기

- `models_glob` 에 모델을 계속 추가하면 자동으로 전부 돈다.
- `mesh.elem_sizes` 에 `[2.0, 1.5, 1.0]` 처럼 여러 값을 넣으면 메시 수렴 확인용 데이터가 같이 나온다.
- `load.signs` 를 `[1, -1]` 로 하면 ± 방향 모두, `amplitude_mm` 으로 하중 범위를 바꿀 수 있다.
- 여러 물성은 `materials` 에 추가하고 모델별 `"material"` 로 지정한다.

## 아직 실제 장비에서 확인하지 못한 부분

이 코드는 HyperMesh·Abaqus 가 없는 환경에서 작성했다. 아래는 연구실 PC 첫 실행 때 확인이 필요하다.

1. `hmbatch.exe -tcl` 로 YL_V5 가 끝까지 도는지 (`mesh/hm_log.txt`, `hm_status.txt`). HM 버전에 따라 `*geomimport` 옵션 문자열이 다를 수 있다.
2. `*feoutputwithdata` 출력 INP 위치와 요소 종류. Python 쪽에서 tet4/tet10, 절점 순서, 체적 부호를 다시 검사·보정하므로 HM 쪽 요소 타입 설정은 결과에 영향이 없다.
3. Abaqus 에서 `*Coupling` + C3D10H + Ogden N=3 Job 이 경고 없이 도는지 → `config_abaqus_smoketest.json` 으로 먼저 확인.
4. ODB 의 history region 이름 (`Node PART-1-1.<RP번호>`). 못 찾으면 field output 으로 자동 대체한다.

## 샌드박스 검증 결과 (CalculiX 2.21 대체 실행)

HyperMesh·Abaqus 가 없는 환경이라 메시는 gmsh 로 만든 tet10 INP(`test/models/`)를 넣고,
솔버만 CalculiX 로 바꿔(`test/config_test_ccx.json`, `solver: ccx`) 같은 파이프라인을 끝까지 돌렸다.
물성은 가상 Ogden 시험데이터(1% 노이즈)이므로 **수치 자체는 의미 없고 흐름·경향 확인용**이다.

- 2 모델 × 스웨징 5 (0, +1, +2, −1, −2 mm) × X/Y/Z = 30 Job, 30/30 수렴, QC 전부 OK
- 축 자동 판별: BUSH_PLAIN(원래 Z축) → +Y, BUSH_VOID(원래 X축) → +Y 로 회전 확인
- INNER/OUTER 면적: 2501 / 6278 mm² (이론 2513 / 6283), VOID 7033 (이론 7037)
- Ogden N=3 피팅: 초기전단 μ0 = 0.4251 MPa (가상데이터 참값 0.422), 상대오차 RMS ≈ 1 % (= 노이즈 수준)
- SW0 축방향 K = 109.0 N/mm, 장부싱 이론식 2πGL/ln(ro/ri) = 116.6 N/mm (−6.5 %, 끝단 자유면 효과)
- 축대칭 PLAIN: Kx/Kz = 0.998–1.002 (대칭 확인), VOID: Kx/Kz = 2.1–3.0 (공극 방향 차이)
- 스웨징 경향: −(압축)일수록 반경방향 K 증가 (PLAIN X: 664 → 568 → 468 → 370 → 284 N/mm, SW −2 → +2)

| 모델 | SW(mm) | Kx | Ky(축) | Kz |
|---|---|---|---|---|
| PLAIN | −2 | 664.5 | 88.5 | 663.3 |
| PLAIN | −1 | 568.9 | 102.2 | 568.1 |
| PLAIN | 0 | 467.8 | 109.0 | 468.1 |
| PLAIN | +1 | 369.9 | 110.6 | 370.5 |
| PLAIN | +2 | 283.9 | 108.9 | 284.5 |
| VOID | −2 | 449.4 | 54.6 | 151.4 |
| VOID | −1 | 375.0 | 59.4 | 139.6 |
| VOID | 0 | 309.4 | 62.4 | 124.4 |
| VOID | +1 | 254.1 | 63.9 | 110.2 |
| VOID | +2 | 208.9 | 64.5 | 98.3 |

(단위 N/mm, 0→2 mm 할선강성. 결과: `test/out_ccx/qc_report.md`, `dataset/summary.csv`, `*_FD.png`)

압축 스웨징에서 축방향 K 가 오히려 줄어드는 것은 가상 물성(Ogden 2항 μ<0)과 ν=0.49 설정 영향일 수 있어
실물성으로 다시 봐야 한다. CalculiX 는 C3D10H 가 없어서 C3D10 + ν=0.49 로 돌렸고, Abaqus 설정은 C3D10H + K=2000 MPa 이다.

### FJ5045 (연구실 물성) 로 다시 돌린 결과

`test/config_test_ccx_FJ5045.json`, 같은 2 모델 × 스웨징 5 × X/Y/Z = 30 Job → **30/30 수렴**.
FJ5045 계수는 Drucker 안정성 점검(단축·등이축·평면, λ 0.1~10)에서 전 구간 안정.

| 모델 | SW(mm) | Kx | Ky(축) | Kz |
|---|---|---|---|---|
| PLAIN | −2 | 1711.4 | 175.4 | 1705.5 |
| PLAIN | −1 | 1237.4 | 182.0 | 1231.9 |
| PLAIN | 0 | 949.4 | 196.2 | 951.1 |
| PLAIN | +1 | 713.1 | 201.2 | 715.8 |
| PLAIN | +2 | 534.5 | 202.0 | 537.0 |
| VOID | −2 | 960.0 | 100.1 | 315.3 |
| VOID | −1 | 756.2 | 107.5 | 275.8 |
| VOID | 0 | 600.1 | 112.5 | 236.0 |
| VOID | +1 | 477.1 | 115.1 | 202.0 |
| VOID | +2 | 382.6 | 115.8 | 176.0 |

- SW0 축방향 196.2 N/mm vs 장부싱 이론식 208.2 N/mm (μ0 = 0.7592 MPa, −5.8 %)
- PLAIN SW−2 X 에서 u ≈ 1.83 mm 부근 하중이 2 N(0.06 %) 순간 감소 + 증분 급감 → QC 가 `monotonic_force:WARN` 으로 잡음.
  스웨징 2 mm + 횡변위 1.83 mm 로 압축측 두께가 약 25 % 줄어든 상태라 국부 요소 찌그러짐으로 보임(4 mm 조대 메시). 2 mm 메시에서 다시 확인 필요.
- 압축 스웨징에서 축방향 K 가 줄어드는 경향은 FJ5045 에서도 같음. 반경방향으로 눌린 상태(λr < 1)에서 r-y 전단 접선강성이 대략 μλr² 로 줄어드는 효과로 설명되지만, 실험값과 비교 확인은 아직 안 함.

### Test_2026.stp 점검 (2026-11-09, 샌드박스: gmsh + CalculiX)

- 형상: CATIA V5R20 STEP, 고무 바디 1개, 면 5,207개 (그중 약 4,600개가 높이 0.2 mm 양각 글자, R0.25 점 표시 224개)
- 글자 제거(gmsh Boolean, 부피 −0.011 %) 후 3 mm tet4 → tet10 변환, C3D10 155,808개, 방정식 89만 개
- 축 자동 판별 X → +Y 회전 OK. INNER = 타원 구멍 전체 (r 12.5~19.7, 5414 mm², 길이 52 mm), OUTER = R29.25 원통 (5623 mm², 길이 38 mm)
- SW0 Y(축방향): 2 mm 까지 수렴, K = 73.7 N/mm (거의 선형)
- SW0 X(반경): 1.13 mm 까지 K = 115.1 N/mm, 이후 발산. 발산 절점은 플랜지 끝면 점 표시 부근과 x=19 끝면 글자 자국 부근 → CAD 에서 각인 제거 필요
- CalculiX 는 절점 30만 개라 전체 평균 힘이 매우 작아(~5e-4 N) 기본 수렴판정이 안 맞음 → `steps.ccx_controls` (CalculiX 전용, Abaqus INP 에는 안 들어감) 로 완화
- 이 수치는 gmsh 메시·CalculiX 결과라 **연구실 Abaqus 결과와 같다고 보장할 수 없음**. 흐름 확인용

- 코드 구조와 내부 동작 설명: [코드설명서.md](코드설명서.md)
