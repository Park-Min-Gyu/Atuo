# YL_AUTO QC report (2026-11-06 18:52)

- jobs: 30, OK: 30, 문제: 0

## BUSH_PLAIN_Ep4p0

- element: C3D10, n_elem=5335, shape_q_min=0.248
- axis: Z -> +Y, LOAD_RP id=9039 at (0,0,0)
- INNER 800 nodes r=[9.999999999999938, 10.000000000000064], OUTER 2020 nodes r=[24.999999999999346, 25.000000000000675]
- bore center offset 0.0000 mm

| job | SW(mm) | dir | K_secant (N/mm) | K_initial | F_end (N) | status | QC |
|---|---|---|---|---|---|---|---|
| BUSH_PLAIN_Ep4p0_SW0p0_XP | +0 | X+ | 949.43 | 931.27 | 1898.87 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SW0p0_YP | +0 | Y+ | 196.16 | 196.30 | 392.33 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SW0p0_ZP | +0 | Z+ | 951.10 | 933.58 | 1902.19 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_XP | +1 | X+ | 713.08 | 694.15 | 1426.15 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_YP | +1 | Y+ | 201.25 | 201.43 | 402.49 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp1p0_ZP | +1 | Z+ | 715.84 | 696.62 | 1431.67 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_XP | +2 | X+ | 534.49 | 514.64 | 1068.98 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_YP | +2 | Y+ | 201.97 | 201.82 | 403.95 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWp2p0_ZP | +2 | Z+ | 537.00 | 516.59 | 1073.99 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_XP | -1 | X+ | 1237.44 | 1202.82 | 2474.88 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_YP | -1 | Y+ | 181.99 | 181.90 | 363.97 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm1p0_ZP | -1 | Z+ | 1231.88 | 1202.53 | 2463.75 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm2p0_XP | -2 | X+ | 1711.40 | 1651.33 | 3422.80 | OK | monotonic_force:WARN (force decreases) |
| BUSH_PLAIN_Ep4p0_SWm2p0_YP | -2 | Y+ | 175.39 | 175.79 | 350.77 | OK | all OK |
| BUSH_PLAIN_Ep4p0_SWm2p0_ZP | -2 | Z+ | 1705.48 | 1646.05 | 3410.96 | OK | all OK |

## BUSH_VOID_Ep4p0

- element: C3D10, n_elem=8672, shape_q_min=0.007
- axis: X -> +Y, LOAD_RP id=15532 at (0,0,0)
- INNER 800 nodes r=[9.999999999999938, 10.000000000000064], OUTER 2192 nodes r=[27.999999999999314, 28.000000000000668]
- bore center offset 0.0000 mm

| job | SW(mm) | dir | K_secant (N/mm) | K_initial | F_end (N) | status | QC |
|---|---|---|---|---|---|---|---|
| BUSH_VOID_Ep4p0_SW0p0_XP | +0 | X+ | 600.06 | 590.36 | 1200.12 | OK | all OK |
| BUSH_VOID_Ep4p0_SW0p0_YP | +0 | Y+ | 112.55 | 112.59 | 225.09 | OK | all OK |
| BUSH_VOID_Ep4p0_SW0p0_ZP | +0 | Z+ | 235.98 | 235.28 | 471.96 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_XP | +1 | X+ | 477.13 | 468.32 | 954.26 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_YP | +1 | Y+ | 115.07 | 115.14 | 230.14 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp1p0_ZP | +1 | Z+ | 202.05 | 201.07 | 404.10 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_XP | +2 | X+ | 382.56 | 374.34 | 765.11 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_YP | +2 | Y+ | 115.83 | 115.89 | 231.66 | OK | all OK |
| BUSH_VOID_Ep4p0_SWp2p0_ZP | +2 | Z+ | 175.98 | 174.90 | 351.96 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_XP | -1 | X+ | 756.20 | 744.33 | 1512.40 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_YP | -1 | Y+ | 107.54 | 107.54 | 215.08 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm1p0_ZP | -1 | Z+ | 275.78 | 275.77 | 551.55 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_XP | -2 | X+ | 960.04 | 939.78 | 1920.09 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_YP | -2 | Y+ | 100.10 | 100.04 | 200.19 | OK | all OK |
| BUSH_VOID_Ep4p0_SWm2p0_ZP | -2 | Z+ | 315.26 | 315.42 | 630.53 | OK | all OK |

## 모델 단위 점검

- BUSH_PLAIN E4.0 SW-2: Kx/Kz = 1.003 OK
- BUSH_PLAIN E4.0 SW-1: Kx/Kz = 1.005 OK
- BUSH_PLAIN E4.0 SW+0: Kx/Kz = 0.998 OK
- BUSH_PLAIN E4.0 SW+1: Kx/Kz = 0.996 OK
- BUSH_PLAIN E4.0 SW+2: Kx/Kz = 0.995 OK
- BUSH_PLAIN E4.0 X+ K_secant vs swaging -> SW-2:1711.4, SW-1:1237.4, SW+0:949.4, SW+1:713.1, SW+2:534.5
- BUSH_PLAIN E4.0 Y+ K_secant vs swaging -> SW-2:175.4, SW-1:182.0, SW+0:196.2, SW+1:201.2, SW+2:202.0
- BUSH_PLAIN E4.0 Z+ K_secant vs swaging -> SW-2:1705.5, SW-1:1231.9, SW+0:951.1, SW+1:715.8, SW+2:537.0
- BUSH_VOID E4.0 SW-2: Kx/Kz = 3.045 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW-1: Kx/Kz = 2.742 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+0: Kx/Kz = 2.543 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+1: Kx/Kz = 2.361 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 SW+2: Kx/Kz = 2.174 INFO(비대칭 형상이면 정상)
- BUSH_VOID E4.0 X+ K_secant vs swaging -> SW-2:960.0, SW-1:756.2, SW+0:600.1, SW+1:477.1, SW+2:382.6
- BUSH_VOID E4.0 Y+ K_secant vs swaging -> SW-2:100.1, SW-1:107.5, SW+0:112.5, SW+1:115.1, SW+2:115.8
- BUSH_VOID E4.0 Z+ K_secant vs swaging -> SW-2:315.3, SW-1:275.8, SW+0:236.0, SW+1:202.0, SW+2:176.0