Part2.inp : Part2.stp 를 gmsh 로 미리 메시한 것 (요소크기 2 mm, Tetra 62,604개, 원본 형상 그대로).
v19 부터는 run_all.bat 이 Part2.stp 를 자동으로 이렇게 메시하므로 보통은 필요 없다.
자동 메시가 그래도 실패할 때만:
  1) models 폴더의 Part2.stp 를 다른 곳으로 옮기고 (같은 이름이 두 개 있으면 오류)
  2) 이 Part2.inp 를 models 폴더에 넣고
  3) run_all.bat --only Part2
