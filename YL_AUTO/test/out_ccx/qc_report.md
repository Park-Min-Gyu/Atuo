# YL_AUTO QC report (2026-11-06 17:19)

- jobs: 30, OK: 30, 문제: 0

## BUSH_PLAIN_Ep4p0

- element: C3D10, n_elem=5335, shape_q_min=0.248
- axis: Z -> +Y, LOAD_RP id=9039 at (0,0,0)
- INNER 800 nodes r=[9.999999999999938, 10.000000000000064], OUTER 2020 nodes r=[24.999999999999346, 25.000000000000675]
- bore center offset 0.0000 mm

| job | SW(mm) | dir | K_secant (N/mm) | K_initial | F_end (N) | status | QC |
|---|---|---|---|---|---|---|---|
| BUSH_PLAIN_Ep4p0_SW0p0_XP | +0 | X+ | 467.77 | 463.10 | 935.53 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SW0p0_YP | +0 | Y+ | 108.96 | 109.05 | 217.92 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SW0p0_ZP | +0 | Z+ | 468.12 | 463.55 | 936.24 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_XP | +1 | X+ | 369.87 | 364.30 | 739.73 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_YP | +1 | Y+ | 110.59 | 110.71 | 221.17 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_ZP | +1 | Z+ | 370.47 | 364.90 | 740.95 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_XP | +2 | X+ | 283.93 | 278.25 | 567.86 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_YP | +2 | Y+ | 108.94 | 109.06 | 217.89 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_ZP | +2 | Z+ | 284.53 | 278.75 | 569.06 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_XP | -1 | X+ | 568.86 | 564.92 | 1137.72 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_YP | -1 | Y+ | 102.25 | 102.27 | 204.50 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_ZP | -1 | Z+ | 568.11 | 564.63 | 1136.21 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm2p0_XP | -2 | X+ | 664.54 | 662.25 | 1329.07 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm2p0_YP | -2 | Y+ | 88.54 | 88.58 | 177.07 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm2p0_ZP | -2 | Z+ | 663.34 | 660.42 | 1326.68 | OK | all OK |

## BUSH_VOID_Ep4p0

- element: C3D10, n_elem=8672, shape_q_min=0.007
- axis: X -> +Y, LOAD_RP id=15532 at (0,0,0)
- INNER 800 nodes r=[9.999999999999938, 10.000000000000064], OUTER 2192 nodes r=[27.999999999999314, 28.000000000000668]
- bore center offset 0.0000 mm

| job | SW(mm) | dir | K_secant (N/mm) | K_initial | F_end (N) | status | QC |
|---|---|---|---|---|---|---|---|
| BUSH_VOID_Ep4p0_SW0p0_XP | +0 | X+ | 309.39 | 306.11 | 618.79 | OK | all OK |
| BUSH_VOID_Ep4p0_SW0p0_YP | +0 | Y+ | 62.36 | 62.38 | 124.72 | OK | all OK |
| BUSH_VOID_Ep4p0_SW0p0_ZP | +0 | Z+ | 124.38 | 124.42 | 248.76 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_XP | +1 | X+ | 254.08 | 250.80 | 508.16 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_YP | +1 | Y+ | 63.91 | 63.94 | 127.82 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_ZP | +1 | Z+ | 110.15 | 109.98 | 220.30 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_XP | +2 | X+ | 208.93 | 205.93 | 417.86 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_YP | +2 | Y+ | 64.46 | 64.50 | 128.91 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_ZP | +2 | Z+ | 98.33 | 98.10 | 196.65 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_XP | -1 | X+ | 375.02 | 372.13 | 750.05 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_YP | -1 | Y+ | 59.40 | 59.39 | 118.80 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_ZP | -1 | Z+ | 139.58 | 140.08 | 279.16 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_XP | -2 | X+ | 449.42 | 447.55 | 898.84 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_YP | -2 | Y+ | 54.60 | 54.57 | 109.20 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_ZP | -2 | Z+ | 151.44 | 152.26 | 302.88 | OK | all OK |

## 모델 단위 점검

- BUSH_PLAIN E4.0 SW-2: Kx/Kz = 1.002 OK
- BUSH_PLAIN E4.0 SW-1: Kx/Kz = 1.001 OK
- BUSH_PLAIN E4.0 SW+0: Kx/Kz = 0.999 OK
- BUSH_PLAIN E4.0 SW+1: Kx/Kz = 0.998 OK
- BUSH_PLAIN E4.0 SW+2: Kx/Kz = 0.998 OK
- BUSH_PLAIN E4.0 X+ K_secant vs swaging -> SW-2:664.5, SW-1:568.9, SW+0:467.8, SW+1:369.9, SW+2:283.9
- BUSH_PLAIN E4.0 Y+ K_secant vs swaging -> SW-2:88.5, SW-1:102.2, SW+0:109.0, SW+1:110.6, SW+2:108.9
- BUSH_PLAIN E4.0 Z+ K_secant vs swaging -> SW-2:663.3, SW-1:568.1, SW+0:468.1, SW+1:370.5, SW+2:284.5
- BUSH_VOID E4.0 SW-2: Kx/Kz = 2.968 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW-1: Kx/Kz = 2.687 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+0: Kx/Kz = 2.487 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+1: Kx/Kz = 2.307 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+2: Kx/Kz = 2.125 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 X+ K_secant vs swaging -> SW-2:449.4, SW-1:375.0, SW+0:309.4, SW+1:254.1, SW+2:208.9
- BUSH_VOID E4.0 Y+ K_secant vs swaging -> SW-2:54.6, SW-1:59.4, SW+0:62.4, SW+1:63.9, SW+2:64.5
- BUSH_VOID E4.0 Z+ K_secant vs swaging -> SW-2:151.4, SW-1:139.6, SW+0:124.4, SW+1:110.2, SW+2:98.3