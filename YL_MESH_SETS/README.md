# YL_MESH_SETS : 자동 헥사 메시(YL_HEX_V6) + 원점 + INNER/OUTER 노드 세트

해석(Abaqus) 없이 **메시와 노드 세트만** 만드는 묶음입니다. YL_AUTO 에서 이 부분만 떼어낸 것입니다.

## 하는 일
1. `models/` 의 CAD(.stp/.step/.igs) 를 **YL_HEX_V6** (HyperMesh SolidMap 순수 헥사, C3D8H) 로 메시합니다.
   (설정에서 `engine` 을 `v10` 으로 바꾸면 YL_V10 Tetra)
   이미 메시한 `.inp` 를 넣으면 메시 단계는 건너뜁니다.
2. 관통 구멍(보어)으로 **축을 찾아 +Y 로 돌리고, 보어 중심을 원점 (0,0,0)** 으로 옮깁니다 (원통이 아니어도 됨).
3. **INNER** (보어 벽) / **OUTER** (바깥 슬리브에 닿는 면) 노드를 자동으로 고릅니다.
4. 결과를 Abaqus INP (`*Nset INNER`, `*Nset OUTER`, `LOAD_RP` 포함) 와 CSV, 확인용 그림으로 저장합니다.

## 필요한 것
- HyperMesh 2019 (hmbatch.exe) + 라이선스 : CAD 를 메시할 때만
- Python 3.8+ , `pip install numpy scipy matplotlib`

## 사용
1. CAD 또는 .inp 를 `models/` 에 넣고
2. `run_mesh_sets.bat` 더블클릭 (특정 모델만 : `run_mesh_sets.bat Part2 Part5`)

## 헥사 메시 (기본)
기본 설정(`"engine": "hex"`)은 연구실 **YL_HEX_V6_PURE_2mm** (HyperMesh SolidMap, 순수 헥사 C3D8H, 2.0 mm 고정) **만** 씁니다.
- 헥사가 안 되면(헥사 0개, 매핑 안 된 솔리드, 웨지 섞임, INP 안 나옴) 그 모델은 **실패로 끝나고 Tetra 로 바꾸지 않습니다.**
  로그에 이유가 나옵니다 (`mesh/_logs/hex_status.txt` 의 `N_HEX`, `N_UNMAP`).
- SolidMap 은 **매핑 가능한 솔리드**에서만 헥사를 만듭니다. 공극·필렛이 많은 솔리드는 HyperMesh 에서 블록으로 나눠야 합니다.
- `models/` 에 직접 넣은 `.inp` 도 헥사가 아니면 처리하지 않습니다.
- Tetra 로 돌리고 싶을 때만 `"engine": "v10"`.
- 원본 스크립트 `hypermesh/YL_HEX/YL_HEX_V6_PURE_2mm.tcl` 은 그대로이며 HyperMesh 에서 단독 실행(설정 창)도 됩니다.

## 결과 (`out/<모델>/`)
| 파일 | 내용 |
|---|---|
| `<모델>_mesh.inp` | 노드 + `LOAD_RP`(0,0,0) + 요소(`RUBBER`) + `*Nset INNER` / `*Nset OUTER`. Abaqus 에 `*Include` 해서 사용 |
| `<모델>_INNER_nodes.csv` | INNER 노드 번호, 좌표 |
| `<모델>_OUTER_nodes.csv` | OUTER 노드 번호, 좌표, 스웨징 방향(바깥쪽 단위벡터 `dir_x`, `dir_z`) |
| `<모델>_SETS.png` | INNER(파랑) / OUTER(빨강) / 원점(별) 확인 그림 |
| `<모델>_info.json` | 축, 원점 이동량, 세트 범위·면적, 메시 품질 |
| `mesh/` | YL_V10 결과 (`.hm`, HyperMesh 가 쓴 `.inp`, `_logs/batch_log_V9.txt` 등) |

좌표계 : 축 = +Y, 반경 방향 = XZ 평면, 원점 = 보어 중심 (축 방향으로는 INNER 길이의 가운데).

## 설정 (`config_mesh_sets.json`)
| 항목 | 기본 | 뜻 |
|---|---|---|
| `elem_size` | 2.0 | 요소 크기 (mm) |
| `fixed_size` | true | Tetra 성장률 1.0 (안쪽도 같은 크기). false 면 YL_V10 기본값 1.2 |
| `repair_quality` | true | 납작한 Tetra 자동 보정 (표면은 안 움직임) |
| `hypermesh.exe` | auto | hmbatch.exe 경로. 못 찾으면 전체 경로를 적거나 `set YL_HMBATCH=경로` |
| `geometry.shape_mode` | general | general = 원통이 아니어도 자동 / cylinder = 예전 원통 방식 |
| `geometry.center` | auto | auto = 보어 중심을 (0,0,0) / none = CAD 원점 그대로 |
| `geometry.outer_mode` | hull | hull = 슬리브에 닿는 바깥면만 / silhouette = 바깥 윤곽 전부 |

## 파일 구성
- `yl_mesh_sets.py` : 실행 파일 (하나)
- `hypermesh/YL_V10/YL_V10.ps1`, `.bat` : 연구실 YL_V10 원본 그대로 (단독 실행도 됨, 2D 스킨만)
- `core/yl_v10.py` : YL_V10.ps1 의 엔진 Tcl 을 꺼내 hmbatch 로 실행 (V9 -> V8, Tetra + INP 켬)
- `core/shape_general.py` : 축 / 원점 / INNER / OUTER 판별
- `core/inp_mesh.py` : INP 읽기, Tetra 방향·2차 요소·품질 보정
- `core/sets_out.py` : INP / CSV / 그림 저장
- `core/findexe.py`, `core/runners.py` : hmbatch 찾기 / 실행 도우미

## 알아둘 점
- YL_V10 단독 실행은 2D 스킨만 만듭니다. 여기서는 Tetra 가 필요해서 YL_V10 엔진 안의 3D 단계를 켭니다
  (`YL_V9_ALLOW_3D=1`). 이 3D 단계는 실제 HyperMesh 에서 아직 확인하지 못했으니, 처음에는
  `mesh/_logs/batch_log_V9.txt` 의 `Tetra mesh:` 줄을 확인하세요.
- CAD 에 결함(찌꺼기 면, 겹친 면 등)이 있어 YL_V10 이 실패하면 그 모델은 "실패"로 끝납니다.
  (YL_AUTO 전체 파이프라인에는 gmsh / TetWild 예비 메셔가 있습니다.)
- 관통 구멍이 없는 형상은 INNER 를 정할 수 없어 실패합니다.
