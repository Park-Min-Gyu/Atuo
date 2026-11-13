# =====================================================================
# YL_V5_batch (v9) : YL_V4(Rev.4) 메시 알고리즘 + 배치 자동화 래퍼
#  - 메시 알고리즘(Case A/B 분리, Rail 보존 F3, 품질 게이트 직선화, Tetra)은
#    YL_V4 원본 코드를 한 줄도 바꾸지 않고 그대로 사용한다.
#  - 바뀐 부분은 아래 4가지뿐이다.
#      [B1] 입력창(hm_getfloat)·선택 패널(*createmarkpanel) 대신 ::YL() 파라미터 사용
#      [B2] 형상 자동 불러오기 (STEP/IGES/Parasolid/CATIA 등 = *geomimport, .hm = *readfile)
#      [B3] Tetra 생성 후 Abaqus INP 자동 출력 (*feoutputwithdata, standard.3d)
#      [B4] tk_messageBox 대신 상태 파일(hm_status.txt) 기록 후 *quit
#  - ::YL 배열이 없으면(HyperMesh GUI에서 직접 source) 예전 V4처럼 입력창/패널로 동작한다.
#
#  사용(배치): run_pipeline.py 가 아래와 같은 래퍼를 만들어 hmbatch -tcl 로 실행한다.
#      set ::YL(BATCH)      1
#      set ::YL(INPUT)      "C:/work/models/model_01.stp"
#      set ::YL(OUT_DIR)    "C:/work/auto/model_01_2p0/mesh"
#      set ::YL(BASE_NAME)  "model_01"
#      set ::YL(ELEM_SIZE)  2.0
#      set ::YL(ELEM_TYPE)  "C3D10H"
#      source "C:/.../YL_V5_batch.tcl"
# =====================================================================

proc yl_param {key default} {
    if {[info exists ::YL($key)] && $::YL($key) ne ""} { return $::YL($key) }
    return $default
}
set YL_BATCH [yl_param BATCH 0]

# [B6] 배치 모드 확인창 자동 응답
#   HyperMesh 는 *replacenodes 등에서 "Replacing this node will destroy an element.
#   Do you wish to continue?" 같은 확인창을 띄운다. GUI 에서는 사람이 '예'를 누르지만
#   hmbatch 에서는 "Invalid interactive routine called while in batch mode" FatalError 로
#   프로그램이 즉사한다(catch 로도 못 잡음). 그래서 확인창이 뜰 수 있는 명령 앞에는
#   항상 hm_answernext yes 를 먼저 걸어 둔다 (GUI 에서 '예' 누른 것과 같은 동작).
proc yl_hm {args} {
    catch { hm_answernext yes }
    return [uplevel 1 $args]
}

proc yl_log {msg} {
    set f [yl_param LOG ""]
    if {$f ne ""} {
        if {![catch {open $f a} fh]} {
            puts $fh "[clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}] $msg"
            close $fh
        }
    }
    catch { puts $msg }
}

proc yl_write_status {pairs} {
    set f [yl_param STATUS_FILE ""]
    if {$f eq ""} { return }
    if {![catch {open $f w} fh]} {
        foreach {k v} $pairs { puts $fh "$k=$v" }
        close $fh
    }
}

proc yl_finish {} {
    global YL_BATCH
    if {$YL_BATCH} { catch { *quit 1 } }
}

proc yl_fail {msg} {
    global YL_BATCH
    yl_log "FAIL: $msg"
    yl_write_status [list STATUS FAIL MESSAGE $msg]
    if {$YL_BATCH} {
        yl_finish
    } else {
        catch { tk_messageBox -message $msg }
    }
    return -code return
}

# =====================================================================
# >>> [ 1. 2D 전용 확인 vs 3D 테트라 생성 스위치 ] <<<
#   - 배치 자동화에서는 INP를 뽑아야 하므로 기본값 1 (3D Tetra)
# =====================================================================
set DO_TETRA          [yl_param DO_TETRA [expr {$YL_BATCH ? 1 : 0}]]

# =====================================================================
# >>> [ 2. ABAQUS 3D ELEMENT TYPE 설정 구간 ] <<<
# =====================================================================
set ABAQUS_ELEM_TYPE  [yl_param ELEM_TYPE "C3D10H"]   ;# "C3D4", "C3D4H", "C3D10H"

# =====================================================================
# >>> [ 3. 메시 크기 : 배치=파라미터, GUI=입력창 ] <<<
# =====================================================================
set DEFAULT_SIZE      2.0
set ELEM_SIZE         [expr {double([yl_param ELEM_SIZE $DEFAULT_SIZE])}]

if {!$YL_BATCH && ![info exists ::YL(ELEM_SIZE)]} {
    if {![catch {hm_getfloat "Enter Target Mesh Size (mm):" "Please enter target Tria element size" $DEFAULT_SIZE} user_val]} {
        if {$user_val ne "" && $user_val > 0.05} {
            set ELEM_SIZE [expr {double($user_val)}]
        }
    }
}

# Case B(곡률/필렛부)를 위한 가변 범위 (Rev.3 성공 비율 유지)
set MIN_CURVE_SIZE    [expr {$ELEM_SIZE * 0.52}]
set MAX_ELEM_SIZE     [expr {$ELEM_SIZE * 1.15}]
set TARGET_ASPECT     5.0

set CHORDAL_DEV       [expr {$ELEM_SIZE * 0.02}]
set MAX_CURVE_ANG     15.0
set SHARP_ANGLE       26.0
set F3_MAX_PASSES     10
set COLLAPSE_ABS_LEN  [expr {$ELEM_SIZE * 0.28}]

set WIDE_SURF_WIDTH   [expr {$ELEM_SIZE * 2.2}]
set FLAT_SPAN_ANG     18.0
set RAIL_MIN_LEN      [expr {$ELEM_SIZE * 1.5}]

# --- [3D Tetra Parameters] ---
set DELETE_2D_SKIN    1
set TET_GROWTH        1.2
set MIN_TET_COLLAPSE  0.15
# [v8] Tetra 개수 합격 기준 : n_tet >= max(TET_MIN_ABS, TET_MIN_RATIO * 표면 Tria 수)
#      (예전에는 n_tet > 0 이면 OK 였기 때문에 3개짜리 메시가 OK 로 통과했었음)
set TET_MIN_RATIO     [expr {double([yl_param TET_MIN_RATIO 0.25])}]
set TET_MIN_ABS       [yl_param TET_MIN_ABS 50]
set ::YL_TET_MIN      $TET_MIN_ABS

# [v8] 작은 형상(양각/음각 글자, 0.2mm 단차 등) 자동 무시
#   폭 < SMALL_W_RATIO*요소크기  이고  가장 긴 모서리 < SMALL_MAXLEN_RATIO*요소크기 인 면의
#   모서리를 suppress 해서 메셔가 그 면을 무시하고 주변 면과 한 덩어리로 메시하게 한다.
#   (글자 0.2mm 를 2mm 요소로 메시하면 찌그러진/자기교차 Tria 가 생겨 tetmesh 가 실패함)
set DEFEATURE          [yl_param DEFEATURE 1]
set SMALL_W_RATIO      [expr {double([yl_param SMALL_W_RATIO 0.25])}]
set SMALL_MAXLEN_RATIO [expr {double([yl_param SMALL_MAXLEN_RATIO 5.0])}]

set OUT_DIR           [yl_param OUT_DIR "C:/work/mesh"]
set BASE_NAME         [yl_param BASE_NAME "YL_V5"]

file mkdir $OUT_DIR
yl_log "YL_V5 start : batch=$YL_BATCH size=$ELEM_SIZE type=$ABAQUS_ELEM_TYPE out=$OUT_DIR"

# ---------------- Vector & Geometry Helper Functions ----------------
proc vsub {a b} {
    lassign $a ax ay az
    lassign $b bx by bz
    return [list [expr {$ax-$bx}] [expr {$ay-$by}] [expr {$az-$bz}]]
}

proc vadd {a b} {
    lassign $a ax ay az
    lassign $b bx by bz
    return [list [expr {$ax+$bx}] [expr {$ay+$by}] [expr {$az+$bz}]]
}

proc vscale_clean {a s} {
    lassign $a ax ay az
    return [list [expr {$ax*$s}] [expr {$ay*$s}] [expr {$az*$s}]]
}

proc vdot {a b} {
    lassign $a ax ay az
    lassign $b bx by bz
    return [expr {$ax*$bx + $ay*$by + $az*$bz}]
}

proc vcross {a b} {
    lassign $a ax ay az
    lassign $b bx by bz
    return [list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]]
}

proc vlen {a} {
    return [expr {sqrt([vdot $a $a])}]
}

proc vnorm {a} {
    set l [vlen $a]
    if {$l <= 1e-12} { return [list 0.0 0.0 1.0] }
    return [vscale_clean $a [expr {1.0 / $l}]]
}

proc node_xyz {n} {
    if {![hm_entityinfo exist nodes $n]} { return [list 0.0 0.0 0.0] }
    return [lindex [hm_nodevalue $n] 0]
}

proc sc_get_surf_comp {sid} {
    if {![hm_entityinfo exist surfs $sid]} { return -1 }
    if {![catch {hm_getvalue surfs id=$sid dataname=collector.id} cid] && $cid ne ""} {
        return $cid
    }
    return -1
}

proc sc_get_line_len {lid} {
    if {![hm_entityinfo exist lines $lid]} { return 0.0 }
    if {![catch {hm_linelength $lid} len] && $len ne ""} { return $len }
    if {![catch {hm_getvalue lines id=$lid dataname=length} len] && $len ne ""} { return $len }
    return 0.0
}

# [v9] 면적 함수 수정
#   예전 코드는 hm_getareaofsurface $sid (인자 1개)로 불렀는데 HM2019 정식 문법은
#   hm_getareaofsurface surfs $sid (인자 2개)이다. 인자 1개 호출은 오류 -> catch 에 묻혀
#   면적이 항상 0 으로 나왔고, 그 결과
#     (1) 모든 면이 Case B 로 분류 (CASE_A=0)
#     (2) 작은 면 자동 무시(defeature)가 한 개도 못 찾음 (DEFEATURED=0)
#   이 되어 0.2mm 글자가 그대로 메시되었다. 되는 방법을 한 번 찾아서 계속 쓴다.
set ::YL_AREA_API ""
set ::YL_AREA_FAIL 0
proc sc_area_try {api sid} {
    switch -- $api {
        typed   { return [hm_getareaofsurface surfs $sid] }
        plain   { return [hm_getareaofsurface $sid] }
        value   { return [hm_getvalue surfs id=$sid dataname=area] }
    }
    error "unknown area api $api"
}
proc sc_get_surf_area {sid} {
    if {![hm_entityinfo exist surfs $sid]} { return 0.0 }
    if {$::YL_AREA_API ne ""} {
        if {![catch {sc_area_try $::YL_AREA_API $sid} a] && [string is double -strict $a] && $a > 0.0} {
            return $a
        }
    }
    foreach api {typed plain value} {
        if {$api eq $::YL_AREA_API} { continue }
        if {![catch {sc_area_try $api $sid} a] && [string is double -strict $a] && $a > 0.0} {
            if {$::YL_AREA_API eq ""} {
                set ::YL_AREA_API $api
                catch { yl_log "면적 함수 확인 : '$api' 방식 사용 (surf $sid area=[format %.4f $a])" }
            }
            return $a
        }
    }
    incr ::YL_AREA_FAIL
    return 0.0
}

# 면의 bounding box 대각선 길이 (면적을 못 구할 때 작은 면 판정용). 실패하면 -1
proc sc_surf_bbox_diag {sid} {
    set d -1.0
    catch {
        *clearmark surfs 1
        *createmark surfs 1 $sid
        set bb [hm_getboundingbox surfs 1 0 0 0]
        *clearmark surfs 1
        if {[llength $bb] == 6} {
            lassign $bb x0 y0 z0 x1 y1 z1
            set d [expr {sqrt(($x1-$x0)*($x1-$x0) + ($y1-$y0)*($y1-$y0) + ($z1-$z0)*($z1-$z0))}]
        }
    }
    return $d
}

proc sc_get_surf_lines {sid} {
    if {![hm_entityinfo exist surfs $sid]} { return [list] }
    *clearmark surfs 1
    *clearmark lines 2
    *createmark surfs 1 $sid
    if {[catch {*findmark surfs 1 1 1 lines 0 2}]} {
        *clearmark surfs 1
        *clearmark lines 2
        return [list]
    }
    set lids [hm_getmark lines 2]
    *clearmark surfs 1
    *clearmark lines 2
    return $lids
}

proc sc_surf_width {sid} {
    set area [sc_get_surf_area $sid]
    if {$area <= 1e-9} { return 0.0 }
    set lids [sc_get_surf_lines $sid]
    set max_l 0.0
    set perim 0.0
    foreach l $lids {
        set ll [sc_get_line_len $l]
        set perim [expr {$perim + $ll}]
        if {$ll > $max_l} { set max_l $ll }
    }
    if {$max_l <= 1e-6} { return 0.0 }
    set w_maxl [expr {$area / $max_l}]
    set w_hyd  [expr {$perim > 1e-6 ? (2.0 * $area / $perim) : $w_maxl}]
    return [expr {min($w_maxl, $w_hyd * 1.05)}]
}

proc sc_surf_bend_span {sid} {
    set lids [sc_get_surf_lines $sid]
    if {[llength $lids] < 2} { return 90.0 }
    set l_pairs [list]
    foreach l $lids {
        lappend l_pairs [list $l [sc_get_line_len $l]]
    }
    set sorted_l [lsort -real -decreasing -index 1 $l_pairs]
    set l1 [lindex [lindex $sorted_l 0] 0]
    set l2 [lindex [lindex $sorted_l 1] 0]

    if {[catch {hm_getcoordinatesofpointsonline $l1 [list 0.25 0.5 0.75]} p1_list]} { return 0.0 }
    if {[catch {hm_getcoordinatesofpointsonline $l2 [list 0.25 0.5 0.75]} p2_list]} { return 0.0 }

    set max_ang 0.0
    foreach p1 $p1_list p2 $p2_list {
        if {[llength $p1] != 3 || [llength $p2] != 3} { continue }
        lassign $p1 x1 y1 z1
        lassign $p2 x2 y2 z2
        if {[catch {hm_getsurfaceuv $sid $x1 $y1 $z1} uv1]} { continue }
        if {[catch {hm_getsurfaceuv $sid $x2 $y2 $z2} uv2]} { continue }
        lassign $uv1 u1 v1
        lassign $uv2 u2 v2
        if {[catch {hm_getsurfacenormalatcoordinate $sid $u1 $v1} n1]} { continue }
        if {[catch {hm_getsurfacenormalatcoordinate $sid $u2 $v2} n2]} { continue }
        if {[llength $n1] < 3 || [llength $n2] < 3} { continue }
        set vn1 [vnorm [lrange $n1 0 2]]
        set vn2 [vnorm [lrange $n2 0 2]]
        set dot [vdot $vn1 $vn2]
        if {$dot > 1.0} { set dot 1.0 }
        if {$dot < -1.0} { set dot -1.0 }
        set ang [expr {acos(abs($dot)) * 180.0 / acos(-1.0)}]
        if {$ang > $max_ang} { set max_ang $ang }
    }
    return $max_ang
}

proc tria_metrics {n1 n2 n3} {
    set p1 [node_xyz $n1]
    set p2 [node_xyz $n2]
    set p3 [node_xyz $n3]
    set e12 [vsub $p2 $p1]
    set e23 [vsub $p3 $p2]
    set e31 [vsub $p1 $p3]
    set L12 [vlen $e12]
    set L23 [vlen $e23]
    set L31 [vlen $e31]

    if {$L12 <= $L23 && $L12 <= $L31} {
        set lmin $L12; set s_na $n1; set s_nb $n2; set opp_n $n3
    } elseif {$L23 <= $L12 && $L23 <= $L31} {
        set lmin $L23; set s_na $n2; set s_nb $n3; set opp_n $n1
    } else {
        set lmin $L31; set s_na $n3; set s_nb $n1; set opp_n $n2
    }

    if {$L12 >= $L23 && $L12 >= $L31} {
        set lmax $L12; set l_na $n1; set l_nb $n2; set apex_n $n3
    } elseif {$L23 >= $L12 && $L23 >= $L31} {
        set lmax $L23; set l_na $n2; set l_nb $n3; set apex_n $n1
    } else {
        set lmax $L31; set l_na $n3; set l_nb $n1; set apex_n $n2
    }

    set A [expr {0.5 * [vlen [vcross $e12 [vsub $p3 $p1]]]}]
    if {$A <= 1e-12} {
        return [list $lmin $lmax 999.0 $s_na $s_nb $l_na $l_nb $apex_n]
    }
    set hmin [expr {2.0 * $A / $lmax}]
    set asp_hm [expr {($lmax / $hmin) * 0.8660254}]
    return [list $lmin $lmax $asp_hm $s_na $s_nb $l_na $l_nb $apex_n]
}

proc tria_normal {n1 n2 n3} {
    set p1 [node_xyz $n1]
    set p2 [node_xyz $n2]
    set p3 [node_xyz $n3]
    return [vnorm [vcross [vsub $p2 $p1] [vsub $p3 $p1]]]
}

# =====================================================================
# [핵심 업그레이드] Guide-Rail & Sharp Node 3-Tier Detector
#  - Tier 3 : 외곽 Free Edge (절대 고정)
#  - Tier 2 : 26도 이상 꺾이는 Sharp Feature Edge 노드
#  - Tier 1 : 각도는 완만하지만 길게 뻗은 CAD 가이드 라인(필렛 경계선 등) 위의 노드!
#             (일반 내부 노드(Tier 0)와 합쳐질 때 무조건 Tier 1 위치로 흡수시켜
#              필렛/단차 세로선이 지그재그로 울퉁불퉁해지는 현상을 원천 차단합니다!)
# =====================================================================
proc sc_detect_mesh_sharp_and_rail_nodes {eids sharp_deg rail_node_list} {
    array set edge_elems {}
    array set elem_norm {}

    foreach e $eids {
        if {![hm_entityinfo exist elems $e]} { continue }
        if {[catch {hm_getvalue elems id=$e dataname=nodes} nds] || [llength $nds] != 3} { continue }
        lassign $nds n1 n2 n3
        set elem_norm($e) [tria_normal $n1 $n2 $n3]

        foreach pair [list [list $n1 $n2] [list $n2 $n3] [list $n3 $n1]] {
            lassign $pair a b
            set key [expr {$a < $b ? "${a}_${b}" : "${b}_${a}"}]
            lappend edge_elems($key) $e
        }
    }

    set cos_thresh [expr {cos($sharp_deg * acos(-1.0) / 180.0)}]
    array set sharp_nodes {}

    # 먼저 긴 CAD 가이드 라인 위의 노드들에 Tier 1 부여
    foreach rn $rail_node_list {
        set sharp_nodes($rn) 1
    }

    foreach key [array names edge_elems] {
        set elist $edge_elems($key)
        lassign [split $key "_"] na nb
        if {[llength $elist] != 2} {
            set sharp_nodes($na) 3
            set sharp_nodes($nb) 3
            continue
        }
        lassign $elist e1 e2
        set dot [vdot $elem_norm($e1) $elem_norm($e2)]
        if {$dot < $cos_thresh} {
            if {![info exists sharp_nodes($na)] || $sharp_nodes($na) < 2} { set sharp_nodes($na) 2 }
            if {![info exists sharp_nodes($nb)] || $sharp_nodes($nb) < 2} { set sharp_nodes($nb) 2 }
        }
    }
    return [array get sharp_nodes]
}

proc sc_get_bad_aspect_elems {eids target_asp} {
    if {[llength $eids] == 0} { return [list] }
    set bad_list [list]
    *clearmark elems 1
    *clearmark elems 2
    eval *createmark elems 1 $eids
    if {![catch {*elementtestaspect elems 1 $target_asp 2 2 0 "2D Aspect Ratio"}]} {
        set bad_list [hm_getmark elems 2]
    }
    *clearmark elems 1
    *clearmark elems 2

    if {[llength $bad_list] > 0} { return $bad_list }

    foreach e $eids {
        if {![hm_entityinfo exist elems $e]} { continue }
        if {[catch {hm_getvalue elems id=$e dataname=nodes} nodes] || [llength $nodes] != 3} { continue }
        lassign $nodes n1 n2 n3
        lassign [tria_metrics $n1 $n2 $n3] lmin lmax asp s_na s_nb l_na l_nb apex_n
        if {$asp > $target_asp} { lappend bad_list $e }
    }
    return $bad_list
}

proc sc_project_nodes_to_surfs {sids} {
    foreach s $sids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        *clearmark surfs 1
        *clearmark elems 2
        *createmark surfs 1 $s
        if {![catch {*findmark surfs 1 257 1 elems 0 2}]} {
            set eids [hm_getmark elems 2]
            if {[llength $eids] > 0} {
                *clearmark elems 1
                *clearmark nodes 2
                eval *createmark elems 1 $eids
                if {![catch {*findmark elems 1 1 1 nodes 0 2}]} {
                    set nids [hm_getmark nodes 2]
                    if {[llength $nids] > 0} {
                        *clearmark nodes 1
                        eval *createmark nodes 1 $nids
                        catch { yl_hm *projectmark nodes 1 1 $s }
                        *clearmark nodes 1
                    }
                }
                *clearmark elems 1
                *clearmark nodes 2
            }
        }
        *clearmark surfs 1
        *clearmark elems 2
    }
}

# =====================================================================
# [신규 핵심 함수] Quality-Gated Line Straightener (롤백 안전장치 포함)
#  - 긴 CAD 라인(가이드 레일)에 붙어 있던 노드가 스무딩/리메시 과정에서 살짝 옆으로 밀려
#    선이 울퉁불퉁해졌을 때, 해당 노드를 다시 반듯한 CAD 라인 위로 스냅(투영)시켜 봅니다.
#  - 투영 직후 주변 요소들의 최대 Aspect Ratio를 검사하여, 만약 Aspect Ratio가 4.8을
#    넘어가면 즉시 원래 좌표로 롤백(원상복구)합니다! (품질 저하 부작용 0% 보장)
# =====================================================================
proc sc_safe_straighten_rail_nodes {node_line_pairs safe_aspect_limit} {
    set straightened_cnt 0
    array set n_to_l $node_line_pairs

    foreach nd [array names n_to_l] {
        if {![hm_entityinfo exist nodes $nd]} { continue }
        set lid $n_to_l($nd)
        if {![hm_entityinfo exist lines $lid]} { continue }

        set old_xyz [node_xyz $nd]
        lassign $old_xyz ox oy oz

        # 이 노드를 공유하는 주변 삼각형 요소 찾기
        *clearmark nodes 1
        *clearmark elems 2
        *createmark nodes 1 $nd
        if {[catch {*findmark nodes 1 1 1 elems 0 2}]} {
            *clearmark nodes 1; *clearmark elems 2; continue
        }
        set adj_eids [hm_getmark elems 2]
        *clearmark nodes 1
        *clearmark elems 2
        if {[llength $adj_eids] == 0} { continue }

        # 일단 반듯한 CAD 라인 위로 노드 투영 시도
        *clearmark nodes 1
        *createmark nodes 1 $nd
        if {[catch {yl_hm *projectmark nodes 1 0 $lid}]} {
            *clearmark nodes 1; continue
        }
        *clearmark nodes 1

        set new_xyz [node_xyz $nd]
        set move_dist [vlen [vsub $new_xyz $old_xyz]]
        if {$move_dist < 1e-4} { continue }

        # 너무 멀리(요소 크기의 35% 이상) 점프하려거나, 주변 요소 중 하나라도 Aspect > safe_aspect_limit 이면 롤백!
        set is_safe 1
        foreach ae $adj_eids {
            if {![hm_entityinfo exist elems $ae]} { continue }
            if {[catch {hm_getvalue elems id=$ae dataname=nodes} nds] || [llength $nds] != 3} { continue }
            lassign $nds n1 n2 n3
            lassign [tria_metrics $n1 $n2 $n3] lmin lmax asp s_na s_nb l_na l_nb apex_n
            if {$asp > $safe_aspect_limit} {
                set is_safe 0
                break
            }
        }

        if {!$is_safe} {
            # 품질이 나빠지므로 즉시 원래 위치로 롤백!
            catch { yl_hm *setnodecoords $nd $ox $oy $oz }
        } else {
            incr straightened_cnt
        }
    }
    return $straightened_cnt
}

proc sc_force_all_trias {sc_comp_list} {
    *clearmark elems 1
    *createmark elems 1 "by config" quad4 quad8
    set q_eids [hm_getmark elems 1]
    if {[llength $q_eids] > 0} {
        catch { yl_hm *splitelements 2 1 }
    }
    *clearmark elems 1
}

proc sc_count_free_edges {shell_eids} {
    if {[llength $shell_eids] == 0} { return 0 }
    catch {
        *clearmark comps 1
        *createmark comps 1 "^edges"
        if {[llength [hm_getmark comps 1]] > 0} { yl_hm *deletemark comps 1 }
        *clearmark comps 1
    }
    set fe_cnt 0
    *clearmark elems 1
    eval *createmark elems 1 $shell_eids
    if {![catch {*findedges1 elems 1 0}]} {
        *clearmark elems 2
        *createmark elems 2 "by comp name" "^edges"
        set fe_cnt [llength [hm_getmark elems 2]]
        *clearmark elems 2
        catch {
            *clearmark comps 1
            *createmark comps 1 "^edges"
            if {[llength [hm_getmark comps 1]] > 0} { yl_hm *deletemark comps 1 }
            *clearmark comps 1
        }
    }
    *clearmark elems 1
    return $fe_cnt
}

# =====================================================================
# [v8] Tetra 헬퍼
#   HyperMesh 2019 *tetmesh 정식 문법:
#     *createstringarray N "tet: ..." "pars: ..." ...
#     *tetmesh entity_type1 mark_id1 mode1 entity_type2 mark_id2 mode2 1 N
#   예전 코드는 마지막 인자에 "1 1.2 2 0 0.8 0 0" 문자열을 그대로 넣어서
#   command.tcl 에 '*tetmesh elements 1 1 elements 2 -1 1 0' (문자열 0개 = tet 파라미터 없음)
#   으로 기록되었고, 그 결과 Tetra 가 3개만 생겼다.
# =====================================================================
proc yl_count_tets {} {
    *clearmark elems 1
    *createmark elems 1 "by config" tetra4 tetra10
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}

proc yl_delete_tets {} {
    *clearmark elems 1
    *createmark elems 1 "by config" tetra4 tetra10
    if {[llength [hm_getmark elems 1]] > 0} { catch { yl_hm *deletemark elems 1 } }
    *clearmark elems 1
}

proc yl_count_trias {} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3 tria6
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}

# [v9] 표면 Tria 메시 검사/정리 (tetmesh 직전에 호출)
#   tetmesh 는 표면 메시가 "완전히 닫힌 한 겹"일 때만 된다. 아래를 직접 세어 로그에 남긴다.
#     free  : Tria 1개만 붙은 모서리 (구멍)
#     T     : Tria 3개 이상이 붙은 모서리 (면이 겹치거나 접힘)
#     dup   : 같은 절점 3개를 쓰는 중복 Tria      -> 삭제
#     degen : 절점이 겹친(면적 0) Tria            -> 삭제
#   반환 : [list n_tria n_free n_T n_deleted]
proc yl_shell_check {{do_fix 1} {label "shell"}} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3 tria6
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    array set ecnt {}
    array set tri {}
    array set ekeys {}
    set degen [list]
    foreach e $eids {
        if {[catch {hm_getvalue elems id=$e dataname=nodes} nds] || [llength $nds] < 3} { continue }
        lassign $nds a b c
        if {$a == $b || $b == $c || $a == $c} { lappend degen $e; continue }
        set ks [list]
        foreach {p q} [list $a $b $b $c $c $a] {
            set k [expr {$p < $q ? "${p}_${q}" : "${q}_${p}"}]
            lappend ks $k
            if {[info exists ecnt($k)]} { incr ecnt($k) } else { set ecnt($k) 1 }
        }
        set ekeys($e) $ks
        lappend tri([join [lsort -integer [list $a $b $c]] _]) $e
    }
    # 중복 Tria : 주변과 붙어 있으면 1장만 남기고, 납작하게 접힌 주머니(모든 모서리가 4장 이상
    # 또는 자기들끼리만 붙음)면 전부 지운다
    set del $degen
    set n_dup 0
    foreach k [array names tri] {
        set grp $tri($k)
        if {[llength $grp] < 2} { continue }
        incr n_dup [expr {[llength $grp] - 1}]
        set pocket 1
        foreach ek $ekeys([lindex $grp 0]) {
            set c $ecnt($ek)
            if {!($c == [llength $grp] || $c >= [expr {[llength $grp] + 2}])} { set pocket 0; break }
        }
        if {$pocket && [llength $grp] == 2} {
            foreach e $grp { lappend del $e }
        } else {
            foreach e [lrange $grp 1 end] { lappend del $e }
        }
    }
    set n_del 0
    if {$do_fix && [llength $del] > 0} {
        *clearmark elems 1
        eval *createmark elems 1 $del
        if {![catch { yl_hm *deletemark elems 1 }]} {
            set n_del [llength $del]
            foreach e $del {
                if {[info exists ekeys($e)]} {
                    foreach ek $ekeys($e) { incr ecnt($ek) -1 }
                }
            }
        }
        *clearmark elems 1
    }
    set n_free 0
    set n_t 0
    foreach k [array names ecnt] {
        set c $ecnt($k)
        if {$c == 1} { incr n_free } elseif {$c > 2} { incr n_t }
    }
    set n_tria [expr {[llength $eids] - $n_del}]
    yl_log "$label check: tria=$n_tria free_edges=$n_free T_edges=$n_t dup=$n_dup degen=[llength $degen] deleted=$n_del"
    return [list $n_tria $n_free $n_t $n_del]
}

# Tetra 생성 : 여러 방법을 차례로 시도하고, 합격 개수(n_min) 이상 나온 첫 결과를 사용
# 반환 : [list n_tet 방법이름]
proc yl_tetmesh_robust {n_min} {
    global TET_GROWTH ELEM_SIZE MIN_CURVE_SIZE
    if {[catch { yl_shell_check 1 "tetmesh 입력 표면" } err]} { yl_log "shell check 오류(무시): $err" }
    set tetstr "tet: 35 $TET_GROWTH 2 0 0.8 0 0"
    set attempts [list \
        [list shell_fixed       elems  1 [list $tetstr "pars: post_cln"]] \
        [list shell_float       elems  0 [list $tetstr "pars: upd_shell post_cln"]] \
        [list shell_fixed_plain elems  1 [list $tetstr]] \
        [list solid_geometry    solids 0 [list $tetstr "2d: 1 0 1 $ELEM_SIZE $MIN_CURVE_SIZE 30 1" "pars: post_cln"]] ]
    set best_n 0
    set best_name "none"
    foreach att $attempts {
        lassign $att name etype mode strs
        yl_delete_tets
        *clearmark elems 1
        *clearmark elems 2
        catch { *clearmark solids 1 }
        if {$etype eq "elems"} {
            *createmark elems 1 "by config" tria3 tria6
            set n_in [llength [hm_getmark elems 1]]
        } else {
            set n_in 0
            catch { *createmark solids 1 "all"; set n_in [llength [hm_getmark solids 1]] }
        }
        if {$n_in == 0} {
            yl_log "tetmesh <$name> skip : 입력 $etype 0개"
            continue
        }
        set ns [llength $strs]
        set t0 [clock seconds]
        set rc [catch {
            eval [list *createstringarray $ns] $strs
            yl_hm *tetmesh $etype 1 $mode elems 2 -1 1 $ns
        } err]
        *clearmark elems 1
        catch { *clearmark solids 1 }
        set n [yl_count_tets]
        set dt [expr {[clock seconds] - $t0}]
        if {$rc} {
            yl_log "tetmesh <$name> input=$n_in $etype : n_tet=$n (need >= $n_min) ${dt}s  ERROR: $err"
        } else {
            yl_log "tetmesh <$name> input=$n_in $etype : n_tet=$n (need >= $n_min) ${dt}s"
        }
        if {$n >= $n_min} { return [list $n $name] }
        if {$n > $best_n} { set best_n $n; set best_name $name }
    }
    # 합격한 방법이 없음 -> 마지막 시도 결과가 남아 있음 (개수 부족 -> 상태 FAIL 로 처리됨)
    return [list [yl_count_tets] "none(best=$best_name:$best_n)"]
}

# Tetra 생성 뒤 공통 처리 : 2D 스킨 삭제 + 2차 요소 변환(*orderchangetosecond)
#   (예전 코드의 *orderchange 는 HyperMesh 2019 에 없는 명령이라 catch 에 묻혀 항상 C3D4 로 나왔음.
#    변환이 실패해도 Python(inp_mesh.upgrade_tet4_to_tet10)이 C3D10 으로 바꿔 주므로 안전)
proc yl_post_tet {elem_type} {
    *clearmark elems 1
    *createmark elems 1 "by config" tria3 tria6
    if {[llength [hm_getmark elems 1]] > 0} { catch { yl_hm *deletemark elems 1 } }
    *clearmark elems 1
    if {[string match "C3D10*" $elem_type]} {
        *createmark elems 1 "by config" tetra4
        if {[llength [hm_getmark elems 1]] > 0} {
            if {[catch { yl_hm *orderchangetosecond 1 0 1 } err]} {
                yl_log "orderchangetosecond 실패(무시, Python 에서 tet10 변환): $err"
            }
        }
        *clearmark elems 1
    } else {
        *createmark elems 1 "by config" tetra10
        if {[llength [hm_getmark elems 1]] > 0} { catch { yl_hm *orderchangetofirst 1 } }
        *clearmark elems 1
    }
}

# 작은 형상(글자 등) 모서리 suppress
#   scale : 기준을 몇 배로 넓힐지 (fallback 재시도 때 2.0 으로 더 강하게 지움)
proc yl_suppress_small_features {elem_size {scale 1.0}} {
    global SMALL_W_RATIO SMALL_MAXLEN_RATIO
    set w_lim [expr {$elem_size * $SMALL_W_RATIO * $scale}]
    set l_lim [expr {$elem_size * $SMALL_MAXLEN_RATIO * $scale}]
    set n_noarea 0
    set n_bbox 0
    *clearmark surfs 1
    *createmark surfs 1 "all"
    set sids [hm_getmark surfs 1]
    *clearmark surfs 1
    set n_small 0
    array set sup {}
    foreach s $sids {
        set lids [sc_get_surf_lines $s]
        if {[llength $lids] == 0} { continue }
        set maxl 0.0
        set perim 0.0
        foreach l $lids {
            set ll [sc_get_line_len $l]
            set perim [expr {$perim + $ll}]
            if {$ll > $maxl} { set maxl $ll }
        }
        if {$maxl <= 1e-9 || $maxl > $l_lim} { continue }
        set area [sc_get_surf_area $s]
        if {$area <= 0.0} {
            # 면적을 못 구함 -> bounding box 가 요소 1.5개보다 작으면 작은 면으로 본다
            incr n_noarea
            set dg [sc_surf_bbox_diag $s]
            if {$dg > 0.0 && $dg < [expr {$elem_size * 1.5 * $scale}]} {
                incr n_small
                incr n_bbox
                foreach l $lids { set sup($l) 1 }
            }
            continue
        }
        set w [expr {min($area / $maxl, 2.0 * $area / $perim * 1.05)}]
        if {$w < $w_lim} {
            incr n_small
            foreach l $lids { set sup($l) 1 }
        }
    }
    if {$n_noarea > 0} {
        yl_log "defeature 주의: 면 $n_noarea 개는 면적을 못 구함 (그 중 $n_bbox 개는 bounding box 로 작은 면 판정). area api='$::YL_AREA_API'"
    }
    set lines [array names sup]
    set n_sup 0
    if {[llength $lines] > 0} {
        *clearmark lines 1
        eval *createmark lines 1 $lines
        if {[catch { yl_hm *edgesmarksuppress 1 180 } err]} {
            yl_log "edgesmarksuppress 일괄 실패 -> 한 개씩 시도 ($err)"
            *clearmark lines 1
            foreach l $lines {
                if {![catch { yl_hm *edgesuppress $l }]} { incr n_sup }
            }
        } else {
            set n_sup [llength $lines]
        }
        *clearmark lines 1
    }
    *clearmark surfs 1
    *createmark surfs 1 "all"
    set n_after [llength [hm_getmark surfs 1]]
    *clearmark surfs 1
    yl_log "defeature: 면 [llength $sids] 개 중 작은 면 $n_small 개 (폭<[format %.3f $w_lim]mm, 최대모서리<[format %.2f $l_lim]mm) -> 모서리 $n_sup 개 suppress, 남은 면 $n_after 개"
    return $n_small
}

# =====================================================================
# [B2] STEP 0: 형상 자동 불러오기 (배치 모드 전용)
# =====================================================================
set YL_INPUT [yl_param INPUT ""]
if {$YL_BATCH} {
    if {$YL_INPUT eq "" || ![file exists $YL_INPUT]} {
        yl_fail "INPUT file not found: $YL_INPUT"
    }
    catch { *templatefileset "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" }
    set ext [string tolower [file extension $YL_INPUT]]
    if {$ext eq ".hm"} {
        if {[catch {*readfile "$YL_INPUT" 0} err]} { yl_fail "*readfile failed: $err" }
    } else {
        catch { *start_batch_import 3 }
        set imp_ok 0
        if {![catch {*geomimport "auto_detect" "$YL_INPUT" "CleanupTol=-0.01" "DoNotMergeEdges=off" "ImportBlanked=off" "ScaleFactor=1" "SplitComponents=Part" "TargetUnits=CAD units"} err]} {
            set imp_ok 1
        } elseif {![catch {*geomimport "auto_detect" "$YL_INPUT" "ImportBlanked=off" "ScaleFactor=1"} err]} {
            set imp_ok 1
        }
        catch { *end_batch_import }
        if {!$imp_ok} { yl_fail "*geomimport failed: $err" }
    }
    *clearmark surfs 1
    *createmark surfs 1 "all"
    set n_imp [llength [hm_getmark surfs 1]]
    *clearmark surfs 1
    yl_log "geometry imported : $YL_INPUT  surfaces=$n_imp"
    if {$n_imp == 0} { yl_fail "no surfaces after import: $YL_INPUT" }
    *clearmark solids 1
    catch { *createmark solids 1 "all" }
    set n_sol 0
    catch { set n_sol [llength [hm_getmark solids 1]] }
    *clearmark solids 1
    yl_log "solids after import : $n_sol"
}

set n_defeat 0
if {$YL_BATCH && $DEFEATURE} {
    yl_write_status [list STATUS RUNNING STAGE defeature MESSAGE "HyperMesh stopped during stage defeature"]
    if {[catch { set n_defeat [yl_suppress_small_features $ELEM_SIZE] } err]} {
        yl_log "defeature 오류(무시하고 계속): $err"
    }
}

# =====================================================================
# [B5] 오류 보호 : STEP 1~5 어디서 Tcl 오류가 나도
#      (1) 어느 단계에서 무슨 오류인지 hm_log.txt / hm_status.txt 에 남기고
#      (2) 단순 메시(전체 면 Tria -> Tetra)로 한 번 더 시도한다 (::YL(FALLBACK)=0 이면 안 함)
# =====================================================================
proc yl_stage {name} {
    set ::YL_STAGE $name
    yl_log "STAGE $name"
    yl_write_status [list STATUS RUNNING STAGE $name MESSAGE "HyperMesh stopped during stage $name"]
}
set YL_STAGE "start"
set YL_ALGO "YL_V4"
set YL_ERR ""
set caseA_wide_surfs [list]
set caseB_curved_surfs [list]
set total_f3_merged 0
set straightened_nodes 0
set n_2d_skin 0
set bad_2d_cnt 0
set free_edge_cnt 0
set n_tet 0
set bad_tet_collapse_cnt 0
set mode_label "2D_Only"
set shell_eids [list]

set YL_SKIP_V4 [yl_param SKIP_V4 0]
if {$YL_SKIP_V4} {
    yl_log "SKIP_V4=1 : YL_V4 알고리즘을 건너뛰고 바로 단순 메시(fallback)로 갑니다"
    set YL_ERR "YL_V4 skipped (retry after crash)"
    set yl_rc 0
    set yl_res ""
} else {
set yl_rc [catch {
# =====================================================================
yl_stage "step1_select"
# STEP 1: Select Surfaces & Clear Old Mesh
# =====================================================================
set ABAQUS_ELEM_TYPE [string toupper [string trim $ABAQUS_ELEM_TYPE]]

# [B1] 배치 모드: 불러온 모든 면을 자동 선택 / GUI 모드: 기존처럼 패널에서 선택
*clearmark surfs 1
if {$YL_BATCH} {
    *createmark surfs 1 "all"
} else {
    *createmarkpanel surfs 1 "Select surfaces for YL_V5 Straight-Line Tria Mesh (Size = ${ELEM_SIZE}mm)"
}
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1

if {[llength $surf_ids] == 0} {
    *clearmark surfs 1
    *createmark surfs 1 "displayed"
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
}

if {[llength $surf_ids] == 0} {
    yl_fail "No surfaces found."
}

array set sc_comps {}
foreach s $surf_ids {
    set cid [sc_get_surf_comp $s]
    if {$cid != -1} { set sc_comps($cid) 1 }
}
set sc_comp_list [array names sc_comps]

if {[llength $sc_comp_list] > 0} {
    *clearmark elems 1
    eval *createmark elems 1 "by comp id" $sc_comp_list
    if {[llength [hm_getmark elems 1]] > 0} {
        catch { yl_hm *deletemark elems 1 }
    }
    *clearmark elems 1
}
*clearmark elems 1
*createmark elems 1 "by config" tetra4 tetra10
if {[llength [hm_getmark elems 1]] > 0} {
    catch { yl_hm *deletemark elems 1 }
}
*clearmark elems 1
catch { *ameshclearsurface }

# =====================================================================
yl_stage "step2_case_split"
# STEP 2: Surface Case Classification (Case A: 넓은 벽면 vs Case B: 곡률/단차/필렛)
# =====================================================================
set long_rail_lines [list]
*clearmark surfs 1
*clearmark lines 2
eval *createmark surfs 1 $surf_ids
if {![catch {*findmark surfs 1 1 1 lines 0 2}]} {
    foreach l [hm_getmark lines 2] {
        set llen [sc_get_line_len $l]
        if {$llen >= $RAIL_MIN_LEN} {
            lappend long_rail_lines $l
        }
        set n_seed [expr {int(round($llen / $ELEM_SIZE))}]
        if {$n_seed < 1} { set n_seed 1 }
        *clearmark lines 1
        *createmark lines 1 $l
        catch { *setedgedensitylink 1 $n_seed }
        *clearmark lines 1
    }
}
*clearmark surfs 1
*clearmark lines 2

set caseA_wide_surfs   [list]
set caseB_curved_surfs [list]

foreach s $surf_ids {
    if {![hm_entityinfo exist surfs $s]} { continue }
    set w [sc_surf_width $s]
    set span_ang [sc_surf_bend_span $s]

    if {$w >= $WIDE_SURF_WIDTH && $span_ang <= $FLAT_SPAN_ANG} {
        lappend caseA_wide_surfs $s
    } else {
        lappend caseB_curved_surfs $s
    }
}

yl_stage "step2_caseA_mesh"
# --- [Case A 메시 생성]: 넓고 완만한 벽면 -> 100% 순수 Tria(0 0 2) + 고정 사이즈 ---
if {[llength $caseA_wide_surfs] > 0} {
    *clearmark surfs 1
    eval *createmark surfs 1 $caseA_wide_surfs
    if {[catch {yl_hm *defaultremeshsurf 1 $ELEM_SIZE 0 0 2 1 1 1 1 $ELEM_SIZE $ELEM_SIZE 0.0 0.0}]} {
        catch {yl_hm *defaultremeshsurf 1 $ELEM_SIZE 0 0 2 1 1 1 1 $ELEM_SIZE $ELEM_SIZE $CHORDAL_DEV $MAX_CURVE_ANG}
    }
    *clearmark surfs 1

    *clearmark surfs 1
    *clearmark elems 2
    eval *createmark surfs 1 $caseA_wide_surfs
    if {![catch {*findmark surfs 1 257 1 elems 0 2}]} {
        set ca_elems [hm_getmark elems 2]
        if {[llength $ca_elems] > 0} {
            *clearmark elems 1
            eval *createmark elems 1 $ca_elems
            catch { yl_hm *smoothmark elems 1 1 3 }
            *clearmark elems 1
        }
    }
    *clearmark surfs 1
    *clearmark elems 2
}

yl_stage "step2_caseB_mesh"
# --- [Case B 메시 생성]: 곡률/필렛/좁은 단차면 -> 100% 순수 Tria(0 0 2) + 곡률 적응형 ---
if {[llength $caseB_curved_surfs] > 0} {
    *clearmark surfs 1
    eval *createmark surfs 1 $caseB_curved_surfs
    if {[catch {yl_hm *defaultremeshsurf 1 $ELEM_SIZE 0 0 2 1 0 1 4 $MIN_CURVE_SIZE $MAX_ELEM_SIZE $CHORDAL_DEV $MAX_CURVE_ANG}]} {
        catch {yl_hm *defaultremeshsurf 1 $ELEM_SIZE 0 0 2 1 0 1 1 $MIN_CURVE_SIZE $MAX_ELEM_SIZE $CHORDAL_DEV $MAX_CURVE_ANG}
    }
    *clearmark surfs 1
}

sc_force_all_trias $sc_comp_list

*clearmark elems 1
if {[llength $sc_comp_list] > 0} {
    eval *createmark elems 1 "by comp id" $sc_comp_list
} else {
    *createmark elems 1 "displayed"
}
if {[llength [hm_getmark elems 1]] > 0} {
    catch { yl_hm *equivalence elems 1 0.01 1 0 0 }
}
*clearmark elems 1

# =====================================================================
yl_stage "step2_rail_nodes"
# [핵심] 긴 CAD 가이드 라인(필렛/단차 세로선) 위의 노드 기록
#  - 이 노드들은 일반 내부 노드와 F3 병합될 때 절대 중간(midpoint=1)으로 끌려가지 않고,
#    무조건 자기 자신의 위치(midpoint=0)로 내부 노드를 끌어당겨 직선성을 유지합니다!
# =====================================================================
array set rail_node_to_line {}
set rail_node_list [list]
foreach lid $long_rail_lines {
    if {![hm_entityinfo exist lines $lid]} { continue }
    *clearmark lines 1
    *clearmark nodes 2
    *createmark lines 1 $lid
    if {![catch {*findmark lines 1 1 1 nodes 0 2}]} {
        foreach rn [hm_getmark nodes 2] {
            set rail_node_to_line($rn) $lid
            lappend rail_node_list $rn
        }
    }
    *clearmark lines 1
    *clearmark nodes 2
}
set rail_node_list [lsort -unique $rail_node_list]

# =====================================================================
yl_stage "step3_F3"
# STEP 3: Straight-Line Preserving F3 Engine
# =====================================================================
set total_f3_merged 0
set total_relocated 0

for {set pass 1} {$pass <= $F3_MAX_PASSES} {incr pass} {
    *clearmark elems 1
    if {[llength $sc_comp_list] > 0} {
        eval *createmark elems 1 "by comp id" $sc_comp_list
    } else {
        *createmark elems 1 "displayed"
    }
    set cur_eids [hm_getmark elems 1]
    *clearmark elems 1

    if {[llength $cur_eids] == 0} { break }

    array unset sharp_map
    array set sharp_map [sc_detect_mesh_sharp_and_rail_nodes $cur_eids $SHARP_ANGLE $rail_node_list]

    set merged_this_pass 0
    set touched_nodes [list]

    foreach e $cur_eids {
        if {![hm_entityinfo exist elems $e]} { continue }
        if {[catch {hm_getvalue elems id=$e dataname=nodes} nodes] || [llength $nodes] != 3} { continue }
        lassign $nodes n1 n2 n3
        lassign [tria_metrics $n1 $n2 $n3] lmin lmax asp s_na s_nb l_na l_nb apex_n

        set need_f3 0
        if {$asp > [expr {$TARGET_ASPECT * 0.90}] && $lmin < [expr {$ELEM_SIZE * 0.65}]} {
            set need_f3 1
        } elseif {$lmin < $COLLAPSE_ABS_LEN} {
            set need_f3 1
        } elseif {$asp > $TARGET_ASPECT} {
            set need_f3 1
        }

        if {!$need_f3} { continue }

        set sa [expr {[info exists sharp_map($s_na)] ? $sharp_map($s_na) : 0}]
        set sb [expr {[info exists sharp_map($s_nb)] ? $sharp_map($s_nb) : 0}]

        # [핵심 F3 직선 보존 병합 규칙]
        # Tier가 높은 노드(외곽 3 > 각진 모서리 2 > 긴 가이드 라인 1 > 일반 내부 0) 쪽으로
        # 무조건 midpoint=0 (위치 고정 흡수)을 사용하여 세로선이 옆으로 휘는 것을 방지!
        if {$sa > $sb} {
            if {![catch {yl_hm *replacenodes $s_nb $s_na 0 0}]} {
                incr merged_this_pass; incr total_f3_merged
                lappend touched_nodes $s_na
                continue
            }
        } elseif {$sb > $sa} {
            if {![catch {yl_hm *replacenodes $s_na $s_nb 0 0}]} {
                incr merged_this_pass; incr total_f3_merged
                lappend touched_nodes $s_nb
                continue
            }
        } else {
            # 둘 다 같은 Tier일 때:
            # - 둘 다 일반 내부 노드(Tier 0)이면 중점(1)으로 부드럽게 병합
            # - 둘 다 라인 위(Tier >= 1)에 있으면 한쪽 라인 위치(0)로 정확히 스냅 병합!
            set use_mid [expr {($sa == 0 && $sb == 0) ? 1 : 0}]
            if {![catch {yl_hm *replacenodes $s_na $s_nb $use_mid 0}]} {
                incr merged_this_pass; incr total_f3_merged
                lappend touched_nodes $s_nb
                continue
            }
        }

        # 둔각 납작 삼각형(Cap Sliver) 꼭짓점 직접 이동
        if {$asp > $TARGET_ASPECT} {
            set s_apex [expr {[info exists sharp_map($apex_n)] ? $sharp_map($apex_n) : 0}]
            if {$s_apex < 3} {
                set pa [node_xyz $l_na]
                set pb [node_xyz $l_nb]
                set pc [node_xyz $apex_n]
                set mid_ab [vscale_clean [vadd $pa $pb] 0.5]
                set dir_vec [vsub $pc $mid_ab]
                set cur_h [vlen $dir_vec]
                if {$cur_h > 1e-5} {
                    set req_h [expr {($lmax * 0.8660254) / 4.0}]
                    if {$req_h > $cur_h} {
                        set new_pt [vadd $mid_ab [vscale_clean [vnorm $dir_vec] $req_h]]
                        lassign $new_pt nx ny nz
                        if {![catch {yl_hm *setnodecoords $apex_n $nx $ny $nz}]} {
                            incr total_relocated
                            incr merged_this_pass
                            lappend touched_nodes $apex_n
                        }
                    }
                }
            }
        }
    }

    # [핵심 수정] 스무딩 시 두 번째 인자를 0(라인 파괴)에서 1(라인 따라 슬라이딩)로 변경!
    # 이렇게 하면 노드가 세로 필렛 선 밖으로 삐져나가지 않고 선을 따라서만 매끄럽게 정돈됩니다.
    set valid_touched [list]
    foreach tn [lsort -unique $touched_nodes] {
        if {[hm_entityinfo exist nodes $tn]} { lappend valid_touched $tn }
    }
    if {[llength $valid_touched] > 0} {
        *clearmark nodes 1
        *clearmark elems 2
        eval *createmark nodes 1 $valid_touched
        if {![catch {*findmark nodes 1 1 1 elems 0 2}]} {
            set loc_elems [hm_getmark elems 2]
            if {[llength $loc_elems] > 0} {
                *clearmark elems 1
                eval *createmark elems 1 $loc_elems
                catch { *appendmark elems 1 "by adjacent" }
                catch { yl_hm *smoothmark elems 1 1 2 }
                *clearmark elems 1
            }
        }
        *clearmark nodes 1
        *clearmark elems 2
    }

    if {$merged_this_pass == 0} { break }
}

# =====================================================================
yl_stage "step4_remesh"
# STEP 4: Mesh-Only Local Remesh (*remeshelems) & Safe Line Straightener
# =====================================================================
*clearmark elems 1
if {[llength $sc_comp_list] > 0} {
    eval *createmark elems 1 "by comp id" $sc_comp_list
} else {
    *createmark elems 1 "displayed"
}
set shell_eids [hm_getmark elems 1]
*clearmark elems 1

set bad_eids [sc_get_bad_aspect_elems $shell_eids $TARGET_ASPECT]
if {[llength $bad_eids] > 0} {
    *clearmark elems 1
    eval *createmark elems 1 $bad_eids
    catch { *appendmark elems 1 "by adjacent" }
    catch { yl_hm *remeshelems 1 $ELEM_SIZE 2 2 2 1 1 $SHARP_ANGLE }
    *clearmark elems 1
    sc_force_all_trias $sc_comp_list
}

# 곡면 위로 재투영
sc_project_nodes_to_surfs $surf_ids

# [신규 단계] 품질 보장형 직선 복원 (Quality-Gated Line Straightening)
# 살짝 흔들린 가이드 레일 노드들을 원래의 반듯한 CAD 선 위로 되돌리되,
# 주변 요소의 Aspect Ratio가 4.75 이하로 유지될 때만 확정하고 나빠지면 즉시 롤백합니다!
set straightened_nodes [sc_safe_straighten_rail_nodes [array get rail_node_to_line] 4.75]

yl_stage "step4_killer"
# [최종 Zero-Defect 킬러 루프]: 혹시라도 남은 Aspect > 5.0 요소를 0개로 완전 제거!
for {set killer 1} {$killer <= 5} {incr killer} {
    *clearmark elems 1
    if {[llength $sc_comp_list] > 0} {
        eval *createmark elems 1 "by comp id" $sc_comp_list
    } else {
        *createmark elems 1 "displayed"
    }
    set shell_eids [hm_getmark elems 1]
    *clearmark elems 1

    set rem_bad [sc_get_bad_aspect_elems $shell_eids $TARGET_ASPECT]
    if {[llength $rem_bad] == 0} { break }

    array unset sharp_map
    array set sharp_map [sc_detect_mesh_sharp_and_rail_nodes $shell_eids $SHARP_ANGLE $rail_node_list]

    set fixed_any 0
    foreach be $rem_bad {
        if {![hm_entityinfo exist elems $be]} { continue }
        if {[catch {hm_getvalue elems id=$be dataname=nodes} nodes] || [llength $nodes] != 3} { continue }
        lassign $nodes n1 n2 n3
        lassign [tria_metrics $n1 $n2 $n3] lmin lmax asp s_na s_nb l_na l_nb apex_n
        if {$asp > $TARGET_ASPECT} {
            set sa [expr {[info exists sharp_map($s_na)] ? $sharp_map($s_na) : 0}]
            set sb [expr {[info exists sharp_map($s_nb)] ? $sharp_map($s_nb) : 0}]
            if {$sa > $sb} {
                if {![catch {yl_hm *replacenodes $s_nb $s_na 0 0}]} {
                    incr total_f3_merged; incr fixed_any
                }
            } elseif {$sb > $sa} {
                if {![catch {yl_hm *replacenodes $s_na $s_nb 0 0}]} {
                    incr total_f3_merged; incr fixed_any
                }
            } else {
                if {![catch {yl_hm *replacenodes $s_na $s_nb 0 0}]} {
                    incr total_f3_merged; incr fixed_any
                } elseif {![catch {yl_hm *replacenodes $s_na $apex_n 0 0}]} {
                    incr total_f3_merged; incr fixed_any
                }
            }
        }
    }
    if {$fixed_any == 0} { break }
}

# 최종 한 번 더 안전한 범위(Aspect <= 4.8) 내에서 라인 직선 정렬 마무리
incr straightened_nodes [sc_safe_straighten_rail_nodes [array get rail_node_to_line] 4.80]

# 최종 요소 리스트 및 Equivalence 정리
*clearmark elems 1
if {[llength $sc_comp_list] > 0} {
    eval *createmark elems 1 "by comp id" $sc_comp_list
} else {
    *createmark elems 1 "displayed"
}
set shell_eids [hm_getmark elems 1]
if {[llength $shell_eids] > 0} {
    catch { yl_hm *equivalence elems 1 0.01 1 0 0 }
}
*clearmark elems 1

*clearmark elems 1
if {[llength $sc_comp_list] > 0} {
    eval *createmark elems 1 "by comp id" $sc_comp_list
} else {
    *createmark elems 1 "displayed"
}
set shell_eids [hm_getmark elems 1]
*clearmark elems 1

set n_2d_skin [llength $shell_eids]
set final_bad_2d [sc_get_bad_aspect_elems $shell_eids $TARGET_ASPECT]
set bad_2d_cnt [llength $final_bad_2d]

# =====================================================================
yl_stage "step5_tetmesh"
# STEP 5 (Optional): 3D Tetra + Abaqus Element Type Assignment
# =====================================================================
set n_tet 0
set free_edge_cnt [sc_count_free_edges $shell_eids]
set bad_tet_collapse_cnt 0
set mode_label "2D_Only"

if {$DO_TETRA && $n_2d_skin == 0} { yl_log "V4 표면 메시 Tria 0개 -> V4 tetmesh 생략" }
if {$DO_TETRA && $n_2d_skin > 0} {
    set mode_label "3D_${ABAQUS_ELEM_TYPE}"
    if {$free_edge_cnt > 0} {
        *clearmark elems 1
        eval *createmark elems 1 $shell_eids
        catch { yl_hm *equivalence elems 1 0.04 1 0 0 }
        *clearmark elems 1
        set free_edge_cnt [sc_count_free_edges $shell_eids]
    }
    if {$free_edge_cnt > 0} {
        yl_log "V4 표면 메시에 free edge $free_edge_cnt 개 (구멍) -> V4 tetmesh 생략"
    }

    if {$free_edge_cnt == 0} {
        catch { *templatefileset "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d" }

        set ::YL_TET_MIN [expr {max($TET_MIN_ABS, int($TET_MIN_RATIO * [yl_count_trias]))}]
        lassign [yl_tetmesh_robust $::YL_TET_MIN] n_tet tet_how
        yl_log "V4 tetmesh: n_tet=$n_tet (min $::YL_TET_MIN) method=$tet_how"

        if {$n_tet >= $::YL_TET_MIN && $DELETE_2D_SKIN} {
            yl_post_tet $ABAQUS_ELEM_TYPE
        }

        if {$n_tet >= $::YL_TET_MIN} {
            if {$ABAQUS_ELEM_TYPE eq "C3D10H" || $ABAQUS_ELEM_TYPE eq "C3D10" || $ABAQUS_ELEM_TYPE eq "C3D10M"} {
                *clearmark elems 1
                *createmark elems 1 "by config" tetra10
                if {$ABAQUS_ELEM_TYPE eq "C3D10H"} {
                    catch { *eltype 1 2 }
                    catch { *elementtype 204 2 }
                } else {
                    catch { *eltype 1 1 }
                    catch { *elementtype 204 1 }
                }
                *clearmark elems 1
            } else {
                *clearmark elems 1
                *createmark elems 1 "by config" tetra10
                if {[llength [hm_getmark elems 1]] > 0} {
                    catch { yl_hm *orderchangetofirst 1 }
                }
                *clearmark elems 1

                *clearmark elems 1
                *createmark elems 1 "by config" tetra4
                if {$ABAQUS_ELEM_TYPE eq "C3D4H"} {
                    catch { *eltype 1 2 }
                    catch { *elementtype 204 2 }
                } else {
                    catch { *eltype 1 1 }
                    catch { *elementtype 204 1 }
                }
                *clearmark elems 1
            }

            *clearmark elems 1
            *clearmark elems 2
            *createmark elems 1 "by config" tetra4 tetra10
            if {![catch {yl_hm *elementtesttetcollapse elems 1 $MIN_TET_COLLAPSE 2 2 "3D Tet Collapse"}]} {
                set bad_tet_collapse_cnt [llength [hm_getmark elems 2]]
            }
            *clearmark elems 1
            *clearmark elems 2
        }
    }
}

} yl_res]
}
if {$yl_rc == 2} { return }
if {$yl_rc == 1} {
    set YL_ERR "stage $YL_STAGE : $yl_res"
    yl_log "ERROR (YL_V4 algorithm) in stage $YL_STAGE : $yl_res"
    yl_log "errorInfo: $::errorInfo"
}
if {$YL_BATCH && $DO_TETRA && $n_tet < $::YL_TET_MIN && [yl_param FALLBACK 1]} {
    if {$n_tet > 0} { yl_log "V4 tetra $n_tet 개 < 기준 $::YL_TET_MIN -> 불량으로 보고 fallback 진행" }
    set n_tet 0
    set YL_ALGO "FALLBACK_SIMPLE"
    yl_stage "fallback_simple_mesh"
    # [v9] 조건을 바꿔 가며 최대 3회 시도 (첫 성공에서 멈춤)
    #   1) 요소크기 그대로            2) 작은 면을 2배 더 넓게 무시(defeature x2) 후 다시
    #   3) defeature x2 + 표면 요소크기 0.7배
    set fb_recipes [list [list "size x1.0" 1.0 0] [list "defeature x2" 1.0 1] [list "defeature x2 + size x0.7" 0.7 0]]
    if {!$DEFEATURE} { set fb_recipes [list [list "size x1.0" 1.0 0] [list "size x0.7" 0.7 0]] }
    set fb_rc [catch {
      foreach fb_rcp $fb_recipes {
        lassign $fb_rcp fb_name fb_scale fb_defeat
        set fb_size [expr {$ELEM_SIZE * $fb_scale}]
        yl_log "fallback 시도 <$fb_name> : 표면 요소크기 [format %.2f $fb_size] mm"
        # 기존 요소 전부 삭제
        *clearmark elems 1
        *createmark elems 1 "all"
        if {[llength [hm_getmark elems 1]] > 0} { catch { yl_hm *deletemark elems 1 } }
        *clearmark elems 1
        catch { *ameshclearsurface }
        if {$fb_defeat} {
            if {[catch { incr n_defeat [yl_suppress_small_features $ELEM_SIZE 2.0] } err]} {
                yl_log "defeature x2 오류(무시하고 계속): $err"
            }
        }
        # 전체 면 Tria (YL_V4 Case A 와 같은 명령)
        *clearmark surfs 1
        *createmark surfs 1 "all"
        if {[catch {yl_hm *defaultremeshsurf 1 $fb_size 0 0 2 1 1 1 1 $fb_size $fb_size 0.0 0.0} err]} {
            yl_log "fallback 표면 메시 1차 명령 오류 -> 2차 명령 시도 ($err)"
            catch {yl_hm *defaultremeshsurf 1 $fb_size 0 0 2 1 1 1 1 [expr {$fb_size * 0.52}] [expr {$fb_size * 1.15}] $CHORDAL_DEV $MAX_CURVE_ANG}
        }
        *clearmark surfs 1
        *clearmark comps 1
        *createmark comps 1 "all"
        set fb_comps [hm_getmark comps 1]
        *clearmark comps 1
        catch { sc_force_all_trias $fb_comps }
        *clearmark elems 1
        *createmark elems 1 "by config" tria3 tria6
        set shell_eids [hm_getmark elems 1]
        if {[llength $shell_eids] > 0} { catch { yl_hm *equivalence elems 1 0.01 1 0 0 } }
        *clearmark elems 1
        *clearmark elems 1
        *createmark elems 1 "by config" tria3 tria6
        set shell_eids [hm_getmark elems 1]
        *clearmark elems 1
        set n_2d_skin [llength $shell_eids]
        set free_edge_cnt [sc_count_free_edges $shell_eids]
        set bad_2d_cnt 0
        catch { set bad_2d_cnt [llength [sc_get_bad_aspect_elems $shell_eids $TARGET_ASPECT]] }
        yl_log "fallback <$fb_name>: tria=$n_2d_skin free_edges=$free_edge_cnt aspect>$TARGET_ASPECT=$bad_2d_cnt"
        if {$n_2d_skin > 0} {
            set mode_label "3D_${ABAQUS_ELEM_TYPE}_fallback"
            set ::YL_TET_MIN [expr {max($TET_MIN_ABS, int($TET_MIN_RATIO * $n_2d_skin))}]
            lassign [yl_tetmesh_robust $::YL_TET_MIN] n_tet tet_how
            yl_log "fallback <$fb_name> tetmesh: n_tet=$n_tet (min $::YL_TET_MIN) method=$tet_how"
            if {$n_tet >= $::YL_TET_MIN} {
                yl_post_tet $ABAQUS_ELEM_TYPE
                set n_tet [yl_count_tets]
                break
            }
        }
      }
      yl_log "fallback: tetra=$n_tet"
    } fb_res]
    if {$fb_rc == 1} {
        yl_log "ERROR (fallback) : $fb_res"
        yl_log "errorInfo: $::errorInfo"
        append YL_ERR " | fallback: $fb_res"
    }
}
yl_stage "export"

set out_hm "$OUT_DIR/${BASE_NAME}_${mode_label}_${ELEM_SIZE}mm.hm"
hm_answernext yes
catch { *writefile "$out_hm" 1 }

# =====================================================================
# [B3] Abaqus INP 출력 (3D Tetra 가 만들어진 경우만)
# =====================================================================
set out_inp ""
set export_ok 0
if {$n_tet >= $::YL_TET_MIN} {
    set out_inp "$OUT_DIR/${BASE_NAME}_hm.inp"
    set tpl "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d"
    catch { *feoutputmergeincludefiles 0 }
    hm_answernext yes
    if {![catch {*feoutputwithdata "$tpl" "$out_inp" 0 0 1 1 0} err]} {
        set export_ok [file exists $out_inp]
    } else {
        yl_log "*feoutputwithdata error: $err"
    }
}

set status "OK"
if {$DO_TETRA && ($n_tet < $::YL_TET_MIN || !$export_ok)} { set status "FAIL" }

set msg [format "YL_V5 (YL_V4 Rev.4 mesh + batch) Complete! algo=$YL_ALGO\n\n- Target Mesh Size: %.2f mm\n- Case A (Wide Flat Surfaces): %d\n- Case B (Curved/Detail Surfaces): %d\n- F3 Node Merges (*replacenodes): %d\n- Straightened Guide-Rail Nodes: %d\n- 2D Tria Elements: %d (Aspect > %.1f: %d)\n- Free Edges: %d\n- 3D Tetra Elements: %d\n- Bad Tet Collapse(<%.2f): %d\n\nSaved: %s\nINP: %s" \
    $ELEM_SIZE [llength $caseA_wide_surfs] [llength $caseB_curved_surfs] \
    $total_f3_merged $straightened_nodes $n_2d_skin $TARGET_ASPECT $bad_2d_cnt $free_edge_cnt $n_tet \
    $MIN_TET_COLLAPSE $bad_tet_collapse_cnt $out_hm $out_inp]
if {$YL_ERR ne ""} { append msg "\n\nERROR: $YL_ERR" }
yl_log $msg

set st_msg ""
if {$status ne "OK"} {
    if {$free_edge_cnt > 0} {
        set st_msg "free edges remain ($free_edge_cnt) -> tetmesh skipped"
    } elseif {$n_tet < $::YL_TET_MIN} {
        set st_msg "tetmesh produced only $n_tet elements (need >= $::YL_TET_MIN). 표면 메시가 닫히지 않았거나 겹침 -> hm_log.txt 의 'check:' 줄(free_edges/T_edges)과 'tetmesh <...>' 줄 확인"
        if {$YL_ERR ne ""} { set st_msg "Tcl error -> $YL_ERR" }
    } else {
        set st_msg "INP export failed"
    }
}
yl_write_status [list STATUS $status MESSAGE $st_msg ELEM_SIZE $ELEM_SIZE \
    CASE_A [llength $caseA_wide_surfs] CASE_B [llength $caseB_curved_surfs] \
    F3_MERGED $total_f3_merged STRAIGHTENED $straightened_nodes \
    N_TRIA $n_2d_skin BAD_TRIA $bad_2d_cnt FREE_EDGES $free_edge_cnt \
    N_TET $n_tet N_TET_MIN $::YL_TET_MIN DEFEATURED $n_defeat AREA_API $::YL_AREA_API AREA_FAIL $::YL_AREA_FAIL BAD_TET_COLLAPSE $bad_tet_collapse_cnt HM_FILE $out_hm INP_FILE $out_inp \
    ALGO $YL_ALGO V4_ERROR $YL_ERR]

if {$YL_BATCH} {
    yl_finish
} else {
    catch { tk_messageBox -message $msg }
}
