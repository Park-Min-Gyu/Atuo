# =====================================================================
# YL_HEX_V6_PURE_2mm.tcl : Pure-Quad / Pure-Hexa 2.0 mm Script (HyperMesh 2019)
#
#   Targeted fixes for the exact defects seen in V5 screenshots:
#     1) ZERO Mixed/Tria fallback:
#          - Base 2D mesh, hole patcher, AND local remesh ALL use
#            'quads_only' (type=4), NEVER type=1(quads) or type=2(mixed).
#          - 3D SolidMap uses 'quads_only' (flags=131076) by default.
#     2) V-Tip Sliver Zipper Collapse (fixes grey tapering V-wedges):
#          - Pure mesh-level operation (ZERO CAD B-Rep suppression -> no crash!)
#          - Collapses ultra-short edges (< 0.40 * 2mm = 0.80mm) on elements
#            failing Aspect > 5.0, zipping narrow V-slivers shut cleanly.
#     3) Pairwise Tria -> Quad Merger:
#          - Any adjacent Tria3 pair sharing a smooth edge is merged into 1 Quad4.
#     4) Quality-Driven Quad Relaxation (fixes white kite/diamond quads):
#          - Relaxes interior AND non-corner smooth-edge nodes along tangent
#            to improve worst Aspect + Min Angle + Scaled Jacobian simultaneously.
#     5) Keeps V5 Crash-Safe Architecture:
#          - Zero *edgesmarksuppress / *verticesmarksuppress
#          - Real-time flush+close checkpoint log: C:/Temp/YL_HEX_V6_logs/
#
#     6) 3D TETRA mode (added): 2.0 mm tria shell -> sliver collapse / relax
#        -> tetmesh -> Abaqus tetra type (default C3D10MH) -> tetra quality
#        (tet collapse, volumetric skew, aspect, jacobian). Needs only a CLOSED
#        shell of surfaces - no solid entity required.
#     7) GUI: pick the ELEMENT (Hexa / Tetra) first, then the DIMENSION (2D / 3D):
#          Hexa  + 2D = quad skin      Hexa  + 3D = hex volume (Multi-Solid Map)
#          Tetra + 2D = tria skin      Tetra + 3D = tet volume (tria shell -> tetmesh)
#
#   Run inside HyperMesh 2019:
#     File -> Run -> Tcl/Tk Script -> Select YL_HEX_V6_PURE_2mm.tcl
# =====================================================================

proc ylh6_default_params {} {
    return [list \
        elem_size         2.0          \
        family            "hex"        \
        dim               "2d"         \
        mode              "quad"       \
        quad_mode         "quads_only" \
        select_mode       "all"        \
        target_aspect     5.0          \
        min_jac           0.50         \
        max_warp          15.0         \
        max_skew          60.0         \
        feature_angle     25.0         \
        sliver_edge_max   0.80         \
        do_zipper         1            \
        do_tria_merge     1            \
        do_local_remesh   1            \
        local_size_1      1.2          \
        do_smooth         1            \
        smooth_passes     8            \
        patch_holes       1            \
        elem_type         "C3D8H"      \
        tet_elem_type     "C3D10MH"    \
        tet_collapse_min  0.20         \
        tet_vskew_max     0.80         \
        sm_flags          "auto"       \
        delete_2d         1            \
        equiv_global      0            \
        quad_fallback     1            \
        save_hm           0            \
        export_inp        0            \
        out_dir           "C:/work/mesh_hex" \
        out_name          "model_PUREHEX_2mm" \
    ]
}

# =====================================================================
# MODULE 0 : Real-Time Flush Checkpoint Logger
# =====================================================================
proc ylh6_is_ascii {s} { return [regexp {^[\x00-\x7F]*$} $s] }

proc ylh6_log_dir {} {
    set cand [list "C:/Temp"]
    if {[info exists ::env(PUBLIC)] && $::env(PUBLIC) ne ""} { lappend cand $::env(PUBLIC) }
    if {[info exists ::env(TEMP)]   && $::env(TEMP)   ne ""} { lappend cand $::env(TEMP) }
    if {[info exists ::env(TMP)]    && $::env(TMP)    ne ""} { lappend cand $::env(TMP) }
    lappend cand "C:/"
    foreach base $cand {
        set norm [file normalize $base]
        if {[ylh6_is_ascii $norm]} {
            set d [file join $norm "YL_HEX_V6_logs"]
            if {![catch { file mkdir $d }]} { return $d }
        }
    }
    return [pwd]
}

proc ylh6_ckpt_init {} {
    set d [ylh6_log_dir]
    set ::YLH6_CKPT_FILE   [file join $d "YL_HEX_V6_checkpoint.log"]
    set ::YLH6_REPORT_FILE [file join $d "YL_HEX_V6_report.txt"]
    set ::YLH6_STEP_NO 0
    catch {
        set fh [open $::YLH6_CKPT_FILE w]
        puts $fh "================================================================="
        puts $fh "YL_HEX_V6_PURE_2mm Checkpoint Log"
        puts $fh "Started: [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
        puts $fh "================================================================="
        flush $fh; close $fh
    }
}

proc ylh6_ckpt {tag detail} {
    if {![info exists ::YLH6_CKPT_FILE]} { ylh6_ckpt_init }
    incr ::YLH6_STEP_NO
    set ts [clock format [clock seconds] -format {%H:%M:%S}]
    set line [format "\[%s\] \[CP-%02d\] %-24s | %s" $ts $::YLH6_STEP_NO $tag $detail]
    catch {
        set fh [open $::YLH6_CKPT_FILE a]
        puts $fh $line
        flush $fh; close $fh
    }
}

# =====================================================================
# MODULE 1 : Pure-Quad & 3D-Hexa Math + Mesh-Level Repair (hqc6::*)
# =====================================================================
namespace eval hqc6 {
    variable X; variable E; variable NE
    variable delE {}; variable newE {}; variable vid 0
    variable stats
}

proc hqc6::sub {a b} {
    list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}]
}
proc hqc6::dot {a b} {
    expr {[lindex $a 0]*[lindex $b 0] + [lindex $a 1]*[lindex $b 1] + [lindex $a 2]*[lindex $b 2]}
}
proc hqc6::cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc hqc6::len {a} { expr {sqrt([dot $a $a])} }
proc hqc6::unit {a} {
    set l [len $a]
    if {$l < 1e-14} { return {0.0 0.0 0.0} }
    list [expr {[lindex $a 0]/$l}] [expr {[lindex $a 1]/$l}] [expr {[lindex $a 2]/$l}]
}
proc hqc6::dist {a b} { len [sub $a $b] }
proc hqc6::ekey {a b} { expr {$a < $b ? "$a,$b" : "$b,$a"} }

# Returns {aspect normal area min_jac min_angle_deg}
proc hqc6::tri_metrics {p1 p2 p3} {
    set rad [expr {180.0 / acos(-1.0)}]
    set e1 [sub $p2 $p1]; set e2 [sub $p3 $p2]; set e3 [sub $p1 $p3]
    set l1 [len $e1]; set l2 [len $e2]; set l3 [len $e3]
    set lmax [expr {max($l1,$l2,$l3)}]; set lmin [expr {min($l1,$l2,$l3)}]
    set c [cross $e1 [sub $p3 $p1]]
    set a2 [len $c]
    if {$a2 < 1e-12 || $lmin < 1e-9} { return [list 1e9 {0 0 1} 0.0 0.0 0.0] }
    set hmin [expr {$a2 / $lmax}]
    set asp  [expr {max($lmax / $hmin * 0.8660254, $lmax / $lmin)}]
    set inv  [expr {1.0 / $a2}]
    lassign $c cx cy cz
    set A1 [expr {acos(max(-1.0,min(1.0,($l1*$l1+$l3*$l3-$l2*$l2)/(2.0*$l1*$l3)))) * $rad}]
    set A2 [expr {acos(max(-1.0,min(1.0,($l1*$l1+$l2*$l2-$l3*$l3)/(2.0*$l1*$l2)))) * $rad}]
    set A3 [expr {180.0 - $A1 - $A2}]
    set amin [expr {min($A1,$A2,$A3)}]
    return [list $asp [list [expr {$cx*$inv}] [expr {$cy*$inv}] [expr {$cz*$inv}]] [expr {0.5*$a2}] 1.0 $amin]
}

# Returns {aspect normal area min_jac min_angle_deg}
proc hqc6::quad_metrics {p0 p1 p2 p3} {
    set rad [expr {180.0 / acos(-1.0)}]
    set e0 [sub $p1 $p0]; set e1 [sub $p2 $p1]; set e2 [sub $p3 $p2]; set e3 [sub $p0 $p3]
    set l0 [len $e0]; set l1 [len $e1]; set l2 [len $e2]; set l3 [len $e3]
    set lmax [expr {max($l0,$l1,$l2,$l3)}]; set lmin [expr {min($l0,$l1,$l2,$l3)}]
    set nrm [unit [cross [sub $p2 $p0] [sub $p3 $p1]]]
    set a1 [expr {0.5 * [len [cross $e0 [sub $p2 $p0]]]}]
    set a2 [expr {0.5 * [len [cross [sub $p2 $p0] [sub $p3 $p0]]]}]
    set area [expr {$a1 + $a2}]
    if {$area < 1e-12 || $lmin < 1e-9 || [len $nrm] < 1e-12} {
        return [list 1e9 {0 0 1} 0.0 -1.0 0.0]
    }
    set jmin 1e9; set amin 180.0
    foreach c [list [list $p0 $p1 $p3] [list $p1 $p2 $p0] [list $p2 $p3 $p1] [list $p3 $p0 $p2]] {
        lassign $c o a b
        set v1 [sub $a $o]; set v2 [sub $b $o]
        set d1 [len $v1]; set d2 [len $v2]
        if {$d1 < 1e-12 || $d2 < 1e-12} { return [list 1e9 $nrm $area -1.0 0.0] }
        set sj [expr {[dot [cross $v1 $v2] $nrm] / ($d1 * $d2)}]
        if {$sj < $jmin} { set jmin $sj }
        set ang [expr {acos(max(-1.0, min(1.0, [dot $v1 $v2] / ($d1 * $d2)))) * $rad}]
        if {$ang < $amin} { set amin $ang }
    }
    # Altitudes to diagonals + opposite edges
    set hmin [expr {$area / $lmax}]
    set asp  [expr {max($lmax / $lmin, $lmax / max(1e-12, $hmin))}]
    return [list $asp $nrm $area $jmin $amin]
}

proc hqc6::elem_metrics {nds} {
    variable X
    if {[llength $nds] == 3} {
        lassign $nds a b c
        return [tri_metrics $X($a) $X($b) $X($c)]
    } else {
        lassign $nds a b c d
        return [quad_metrics $X($a) $X($b) $X($c) $X($d)]
    }
}

proc hqc6::hex_min_jac {pts} {
    lassign $pts p0 p1 p2 p3 p4 p5 p6 p7
    set corners [list \
        [list $p0 $p1 $p3 $p4] [list $p1 $p2 $p0 $p5] [list $p2 $p3 $p1 $p6] [list $p3 $p0 $p2 $p7] \
        [list $p4 $p7 $p5 $p0] [list $p5 $p4 $p6 $p1] [list $p6 $p5 $p7 $p2] [list $p7 $p6 $p4 $p3]]
    set jmin 1e9
    foreach c $corners {
        lassign $c o a b d
        set e1 [sub $a $o]; set e2 [sub $b $o]; set e3 [sub $d $o]
        set l1 [len $e1]; set l2 [len $e2]; set l3 [len $e3]
        if {$l1 < 1e-12 || $l2 < 1e-12 || $l3 < 1e-12} { return -1.0 }
        set sj [expr {[dot [cross $e1 $e2] $e3] / ($l1 * $l2 * $l3)}]
        if {$sj < $jmin} { set jmin $sj }
    }
    return $jmin
}

proc hqc6::quad_warp {p0 p1 p2 p3} {
    set rad [expr {180.0 / acos(-1.0)}]
    set w 0.0
    foreach {a b c d} [list $p0 $p1 $p2 $p3  $p1 $p2 $p3 $p0] {
        set n1 [cross [sub $b $a] [sub $c $a]]
        set n2 [cross [sub $c $a] [sub $d $a]]
        set l1 [len $n1]; set l2 [len $n2]
        if {$l1 < 1e-14 || $l2 < 1e-14} { continue }
        set cang [expr {max(-1.0, min(1.0, [dot $n1 $n2] / ($l1 * $l2)))}]
        set ang [expr {acos($cang) * $rad}]
        if {$ang > $w} { set w $ang }
    }
    return $w
}

# Tetra metrics: returns {vol6 collapse vol_skew lmin lmax lsum}
#   vol6     : signed 6 x volume (sign = node ordering)
#   collapse : HM tet collapse = min over 4 nodes of h / (1.24*sqrt(A_opposite)); equilateral = 1
#   vol_skew : 1 - V / V(equilateral tet with the same circumradius); equilateral = 0
proc hqc6::tet_metrics {p0 p1 p2 p3} {
    set u [sub $p1 $p0]; set v [sub $p2 $p0]; set w [sub $p3 $p0]
    set vol6 [dot [cross $u $v] $w]
    set V [expr {abs($vol6) / 6.0}]
    set col 1e9
    foreach {a b c} [list $p1 $p2 $p3  $p0 $p2 $p3  $p0 $p1 $p3  $p0 $p1 $p2] {
        set A [expr {0.5 * [len [cross [sub $b $a] [sub $c $a]]]}]
        if {$A < 1e-14} { set col 0.0; break }
        set cv [expr {(3.0 * $V / $A) / (1.24 * sqrt($A))}]
        if {$cv < $col} { set col $cv }
    }
    set num {0.0 0.0 0.0}
    foreach {s a b} [list [dot $u $u] $v $w  [dot $v $v] $w $u  [dot $w $w] $u $v] {
        set c [cross $a $b]
        set num [list [expr {[lindex $num 0] + $s*[lindex $c 0]}] [expr {[lindex $num 1] + $s*[lindex $c 1]}] [expr {[lindex $num 2] + $s*[lindex $c 2]}]]
    }
    if {abs($vol6) < 1e-14} {
        set vsk 1.0
    } else {
        set R   [expr {[len $num] / (2.0 * abs($vol6))}]
        set aeq [expr {$R * sqrt(8.0 / 3.0)}]
        set vsk [expr {max(0.0, 1.0 - $V / ($aeq*$aeq*$aeq / (6.0*sqrt(2.0))))}]
    }
    set lmin 1e30; set lmax 0.0; set lsum 0.0
    foreach {a b} [list $p0 $p1 $p0 $p2 $p0 $p3 $p1 $p2 $p1 $p3 $p2 $p3] {
        set l [dist $a $b]
        if {$l < $lmin} { set lmin $l }; if {$l > $lmax} { set lmax $l }
        set lsum [expr {$lsum + $l}]
    }
    return [list $vol6 $col $vsk $lmin $lmax $lsum]
}

proc hqc6::elem_edges {nds} {
    if {[llength $nds] == 3} {
        lassign $nds a b c
        return [list [list $a $b] [list $b $c] [list $c $a]]
    } else {
        lassign $nds a b c d
        return [list [list $a $b] [list $b $c] [list $c $d] [list $d $a]]
    }
}

proc hqc6::edge_elems {u v} {
    variable E; variable NE
    set r {}
    if {![info exists NE($u)]} { return {} }
    foreach e $NE($u) {
        if {![info exists E($e)]} { continue }
        set nds $E($e); set nn [llength $nds]
        set iu [lsearch -exact $nds $u]; set iv [lsearch -exact $nds $v]
        if {$iu < 0 || $iv < 0} { continue }
        set d [expr {abs($iu - $iv)}]
        if {$d == 1 || $d == ($nn - 1)} { lappend r $e }
    }
    return $r
}

proc hqc6::del_elem {e} {
    variable E; variable NE; variable delE; variable newE
    foreach n $E($e) {
        set i [lsearch -exact $NE($n) $e]
        if {$i >= 0} { set NE($n) [lreplace $NE($n) $i $i] }
    }
    unset E($e)
    set i [lsearch -exact $newE $e]
    if {$i >= 0} { set newE [lreplace $newE $i $i] } else { lappend delE $e }
}

proc hqc6::add_elem {nds} {
    variable E; variable NE; variable newE; variable vid
    set e "v[incr vid]"
    set E($e) $nds
    foreach n $nds { lappend NE($n) $e }
    lappend newE $e
    return $e
}

proc hqc6::load_2d_hm {} {
    variable X; variable E; variable NE; variable delE; variable newE; variable vid
    array unset X; array unset E; array unset NE
    set delE {}; set newE {}; set vid 0
    *clearmark elems 1
    catch { *createmark elems 1 "by config" quad4 tria3 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    foreach e $eids {
        set nds [hm_nodelist $e]
        if {[llength $nds] != 3 && [llength $nds] != 4} { continue }
        set E($e) $nds
        foreach n $nds {
            lappend NE($n) $e
            if {![info exists X($n)]} { set X($n) [lindex [hm_nodevalue $n] 0] }
        }
    }
    return [array size E]
}

proc hqc6::commit_2d_hm {moved_list} {
    variable X; variable E; variable delE; variable newE
    foreach n $moved_list {
        if {[info exists X($n)]} {
            lassign $X($n) x y z
            catch { *nodemodify $n $x $y $z }
        }
    }
    if {[llength $delE] == 0 && [llength $newE] == 0} { return }
    set comp ""
    if {[llength $delE] > 0} { catch { set comp [hm_getvalue elems id=[lindex $delE 0] dataname=collector.name] } }
    if {$comp ne ""} { catch { *currentcollector components $comp } }
    foreach e $newE {
        if {![info exists E($e)]} { continue }
        set nds $E($e)
        set cfg [expr {[llength $nds] == 4 ? 104 : 103}]
        *createlist nodes 1 {*}$nds
        catch { *createelement $cfg 1 1 1 }
    }
    if {[llength $delE] > 0} {
        *clearmark elems 1
        eval *createmark elems 1 $delE
        catch { *deletemark elems 1 }
        *clearmark elems 1
    }
    catch { *nodecleartempmark }
}

# --- [CORE FIX 1] V-Tip Sliver Zipper Collapse (Fixes grey tapering V-slivers!) ---
# Zips narrow V-shaped sliver strips shut by collapsing short edges (< sliver_max)
# on elements failing Aspect > asp_lim or MinAngle < 18 deg, without touching CAD B-Rep.
proc hqc6::zipper_collapse_inmem {asp_lim sliver_max feature_deg} {
    variable X; variable E; variable NE
    set cosf [expr {cos($feature_deg * acos(-1.0) / 180.0)}]
    set n_col 0

    for {set pass 0} {$pass < 25} {incr pass} {
        # Build feature node map dynamically
        array unset ED; array unset FN; array unset CORNER; array unset NRM
        foreach e [array names E] {
            set NRM($e) [lindex [elem_metrics $E($e)] 1]
            foreach uv [elem_edges $E($e)] {
                lassign $uv u v
                lappend ED([ekey $u $v]) $e
            }
        }
        foreach k [array names ED] {
            set el $ED($k)
            set sharp 0
            if {[llength $el] != 2} {
                set sharp 1
            } else {
                lassign $el e1 e2
                if {[dot $NRM($e1) $NRM($e2)] < $cosf} { set sharp 1 }
            }
            if {$sharp} {
                lassign [split $k ","] u v
                lappend FN($u) $v; lappend FN($v) $u
            }
        }
        foreach n [array names FN] {
            set fl [lsort -unique $FN($n)]; set FN($n) $fl
            if {[llength $fl] != 2} { set CORNER($n) 1; continue }
            lassign $fl p q
            set d1 [sub $X($n) $X($p)]; set d2 [sub $X($q) $X($n)]
            set l1 [len $d1]; set l2 [len $d2]
            if {$l1 < 1e-9 || $l2 < 1e-9 || [dot $d1 $d2]/($l1*$l2) < 0.90} { set CORNER($n) 1 }
        }

        # Find bad elements (Aspect > lim OR sharp pinched tria minAngle < 18)
        set bad {}
        foreach e [array names E] {
            lassign [elem_metrics $E($e)] asp nn ar jj amin
            if {$asp > $asp_lim || ([llength $E($e)] == 3 && $amin < 18.0)} {
                lappend bad [list $e $asp]
            }
        }
        if {[llength $bad] == 0} { break }
        set bad [lsort -real -decreasing -index 1 $bad]

        set did 0
        foreach item $bad {
            lassign $item e a0
            if {![info exists E($e)]} { continue }
            lassign [elem_metrics $E($e)] acur _ _ _ amin_cur
            if {$acur <= $asp_lim && !([llength $E($e)] == 3 && $amin_cur < 18.0)} { continue }

            # Shortest edge of this bad element
            set best_len 1e30; set su ""; set sv ""
            foreach uv [elem_edges $E($e)] {
                lassign $uv u v
                set d [dist $X($u) $X($v)]
                if {$d < $best_len} { set best_len $d; set su $u; set sv $v }
            }
            if {$best_len > $sliver_max} { continue }

            # Order collapse direction: move non-corner onto corner/feature node
            set r_u [expr {[info exists CORNER($su)] ? 2 : ([info exists FN($su)] ? 1 : 0)}]
            set r_v [expr {[info exists CORNER($sv)] ? 2 : ([info exists FN($sv)] ? 1 : 0)}]
            if {$r_u <= $r_v} {
                set pairs [list [list $su $sv] [list $sv $su]]
            } else {
                set pairs [list [list $sv $su] [list $su $sv]]
            }

            foreach pr $pairs {
                lassign $pr a b
                set ra [expr {[info exists CORNER($a)] ? 2 : ([info exists FN($a)] ? 1 : 0)}]
                # Preserve real corners unless edge is ultra-tiny (< 0.25 mm)
                if {$best_len > 0.25 && $ra == 2} { continue }

                set shared [edge_elems $a $b]
                if {[llength $shared] != 2} { continue }

                # Simulate merging a -> b across all elements incident to a
                set ok 1; set w_after 0.0; set new_defs {}; set n_del 0
                foreach el $NE($a) {
                    set nds $E($el)
                    set nn {}
                    foreach m $nds {
                        set q [expr {$m eq $a ? $b : $m}]
                        if {[lsearch -exact $nn $q] < 0} { lappend nn $q }
                    }
                    if {[llength $nn] < 3} {
                        if {[lsearch -exact $shared $el] < 0} { set ok 0; break }
                        lappend new_defs [list $el "DEL" {}]
                        incr n_del
                        continue
                    }
                    lassign [elem_metrics $nds] asp0 n0 ar0 j0 _
                    lassign [elem_metrics $nn]  asp1 n1 ar1 j1 _
                    if {$ar1 < 1e-10 || $j1 < 0.05} { set ok 0; break }
                    if {$asp0 < 12.0 && [dot $n0 $n1] < 0.60} { set ok 0; break }
                    if {$asp1 > $w_after} { set w_after $asp1 }
                    lappend new_defs [list $el "REP" $nn]
                }
                if {!$ok} { continue }
                # Require either elimination of a sliver tria OR strict aspect improvement
                if {$n_del == 0 && $w_after >= $acur - 1e-3 && $w_after > $asp_lim} { continue }

                # Check no duplicate face created
                array unset sigs
                foreach el $NE($b) {
                    if {[info exists E($el)] && [lsearch -exact $NE($a) $el] < 0} {
                        set sigs([join [lsort $E($el)] ","]) 1
                    }
                }
                set dup 0
                foreach nd $new_defs {
                    lassign $nd el act nn
                    if {$act eq "REP"} {
                        set sg [join [lsort $nn] ","]
                        if {[info exists sigs($sg)]} { set dup 1; break }
                        set sigs($sg) 1
                    }
                }
                if {$dup} { continue }

                # Commit in-memory collapse
                foreach nd $new_defs {
                    lassign $nd el act nn
                    del_elem $el
                    if {$act eq "REP"} { add_elem $nn }
                }
                unset NE($a)
                incr n_col; incr did
                break
            }
        }
        if {$did == 0} { break }
    }
    return $n_col
}

# --- [CORE FIX 2] Pairwise Tria3 -> Quad4 Merger (Eliminates mixed triangles!) ---
proc hqc6::merge_tria_pairs_inmem {asp_lim feature_deg} {
    variable X; variable E; variable NE
    set cosf [expr {cos($feature_deg * acos(-1.0) / 180.0)}]
    set n_merged 0

    for {set pass 0} {$pass < 10} {incr pass} {
        # Collect candidate edges shared by two Tria3s
        array unset seen_e
        set cands {}
        foreach e [array names E] {
            if {[llength $E($e)] != 3} { continue }
            foreach uv [elem_edges $E($e)] {
                lassign $uv u v
                set k [ekey $u $v]
                if {[info exists seen_e($k)]} { continue }
                set seen_e($k) 1
                set sh [edge_elems $u $v]
                if {[llength $sh] != 2} { continue }
                lassign $sh e1 e2
                if {[llength $E($e1)] != 3 || [llength $E($e2)] != 3} { continue }
                # Check normal alignment across shared edge
                set n1 [lindex [elem_metrics $E($e1)] 1]
                set n2 [lindex [elem_metrics $E($e2)] 1]
                if {[dot $n1 $n2] < $cosf} { continue }
                # Build ordered quad (u -> opp2 -> v -> opp1) keeping e1's orientation
                set nd1 $E($e1); set nd2 $E($e2)
                set iu [lsearch -exact $nd1 $u]; set iv [lsearch -exact $nd1 $v]
                # Ensure u->v follows e1's winding
                if {($iu + 1) % 3 != $iv} { set tmp $u; set u $v; set v $tmp }
                set opp1 ""; foreach m $nd1 { if {$m ne $u && $m ne $v} { set opp1 $m } }
                set opp2 ""; foreach m $nd2 { if {$m ne $u && $m ne $v} { set opp2 $m } }
                if {$opp1 eq "" || $opp2 eq "" || $opp1 eq $opp2} { continue }
                set qnds [list $u $opp2 $v $opp1]
                lassign [elem_metrics $qnds] qasp qnrm qar qjac qamin
                if {[dot $qnrm $n1] < 0} {
                    set qnds [list $u $opp1 $v $opp2]
                    lassign [elem_metrics $qnds] qasp qnrm qar qjac qamin
                }
                if {$qar < 1e-10 || $qjac < 0.25 || $qamin < 22.0 || $qasp > $asp_lim} { continue }
                # Prefer best quality quad (highest min_jac / lowest aspect)
                set score [expr {$qjac - 0.1 * $qasp}]
                lappend cands [list $score $e1 $e2 $qnds]
            }
        }
        if {[llength $cands] == 0} { break }
        set cands [lsort -real -decreasing -index 0 $cands]
        set did 0
        foreach c $cands {
            lassign $c score e1 e2 qnds
            if {![info exists E($e1)] || ![info exists E($e2)]} { continue }
            del_elem $e1; del_elem $e2
            add_elem $qnds
            incr n_merged; incr did
        }
        if {$did == 0} { break }
    }
    return $n_merged
}

# --- [CORE FIX 3] Quality-Driven Quad Relaxation (Fixes kite/diamond failed quads!) ---
# Relaxes interior nodes on tangent plane AND slides smooth-edge nodes along chord
proc hqc6::relax_2d_inmem {elem_size feature_deg {passes 8}} {
    variable X; variable E; variable NE
    set cosf [expr {cos($feature_deg * acos(-1.0) / 180.0)}]

    array set NRM {}; array set ED {}; array set FN {}; array set CORNER {}
    foreach e [array names E] {
        set NRM($e) [lindex [elem_metrics $E($e)] 1]
        foreach uv [elem_edges $E($e)] {
            lassign $uv u v
            lappend ED([ekey $u $v]) $e
        }
    }
    foreach k [array names ED] {
        set el $ED($k); set sharp 0
        if {[llength $el] != 2} {
            set sharp 1
        } else {
            lassign $el e1 e2
            if {[dot $NRM($e1) $NRM($e2)] < $cosf} { set sharp 1 }
        }
        if {$sharp} {
            lassign [split $k ","] u v
            lappend FN($u) $v; lappend FN($v) $u
        }
    }
    foreach n [array names FN] {
        set fl [lsort -unique $FN($n)]; set FN($n) $fl
        if {[llength $fl] != 2} { set CORNER($n) 1; continue }
        lassign $fl p q
        set d1 [sub $X($n) $X($p)]; set d2 [sub $X($q) $X($n)]
        set l1 [len $d1]; set l2 [len $d2]
        if {$l1 < 1e-9 || $l2 < 1e-9 || [dot $d1 $d2]/($l1*$l2) < 0.96} { set CORNER($n) 1 }
    }

    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        # Focus on nodes adjacent to elements with Aspect > 2.2 or MinAngle < 40 or Jac < 0.65
        array unset cand; array set cand {}
        foreach e [array names E] {
            lassign [elem_metrics $E($e)] asp _ _ jj amin
            if {$asp > 2.2 || $amin < 40.0 || $jj < 0.65} {
                foreach n $E($e) { if {![info exists CORNER($n)]} { set cand($n) 1 } }
            }
        }
        if {[array size cand] == 0} { break }

        set changed 0
        foreach n [array names cand] {
            set els $NE($n)
            if {[llength $els] < 2} { continue }
            set old $X($n)

            # Compute desired target direction (interior tangent vs smooth feature chord)
            if {[info exists FN($n)]} {
                lassign $FN($n) p1 p2
                set mid [list [expr {0.5*([lindex $X($p1) 0]+[lindex $X($p2) 0])}] \
                              [expr {0.5*([lindex $X($p1) 1]+[lindex $X($p2) 1])}] \
                              [expr {0.5*([lindex $X($p1) 2]+[lindex $X($p2) 2])}]]
                set tang [sub $mid $old]
                # Stay very tight on straight/smooth feature line (only slide along chord)
                set chord [unit [sub $X($p2) $X($p1)]]
                set proj [dot $tang $chord]
                set tang [list [expr {$proj*[lindex $chord 0]}] [expr {$proj*[lindex $chord 1]}] [expr {$proj*[lindex $chord 2]}]]
            } else {
                array unset nbrs
                set nx 0.0; set ny 0.0; set nz 0.0
                foreach e $els {
                    lassign $NRM($e) ex ey ez
                    set nx [expr {$nx+$ex}]; set ny [expr {$ny+$ey}]; set nz [expr {$nz+$ez}]
                    foreach uv [elem_edges $E($e)] {
                        lassign $uv u v
                        if {$u eq $n} { set nbrs($v) 1 } elseif {$v eq $n} { set nbrs($u) 1 }
                    }
                }
                set nb [array names nbrs]
                if {[llength $nb] < 3} { continue }
                set navg [unit [list $nx $ny $nz]]
                set cx 0.0; set cy 0.0; set cz 0.0
                foreach m $nb {
                    lassign $X($m) x y z
                    set cx [expr {$cx+$x}]; set cy [expr {$cy+$y}]; set cz [expr {$cz+$z}]
                }
                set k [expr {double([llength $nb])}]
                set disp [sub [list [expr {$cx/$k}] [expr {$cy/$k}] [expr {$cz/$k}]] $old]
                set dn [dot $disp $navg]
                set tang [list [expr {[lindex $disp 0]-$dn*[lindex $navg 0]}] \
                               [expr {[lindex $disp 1]-$dn*[lindex $navg 1]}] \
                               [expr {[lindex $disp 2]-$dn*[lindex $navg 2]}]]
            }
            if {[len $tang] < 1e-4 * $elem_size} { continue }

            # Evaluate current worst penalty around node n (lower = better)
            set pen0 0.0; set sum0 0.0; set nrm0 {}
            foreach e $els {
                lassign [elem_metrics $E($e)] asp nn ar jj amin
                set pen [expr {$asp + ($amin < 45.0 ? (45.0 - $amin)*0.15 : 0.0) + ($jj < 0.7 ? (0.7 - $jj)*5.0 : 0.0)}]
                if {$pen > $pen0} { set pen0 $pen }
                set sum0 [expr {$sum0 + $pen}]
                lappend nrm0 $nn
            }

            set best_pen $pen0; set best_sum $sum0; set best_pos ""
            foreach a {0.5 0.8 0.25} {
                set trial [list [expr {[lindex $old 0] + $a*[lindex $tang 0]}] \
                                [expr {[lindex $old 1] + $a*[lindex $tang 1]}] \
                                [expr {[lindex $old 2] + $a*[lindex $tang 2]}]]
                set X($n) $trial
                set pen1 0.0; set sum1 0.0; set ok 1; set i 0
                foreach e $els {
                    lassign [elem_metrics $E($e)] asp nn ar jj amin
                    if {$ar < 1e-10 || $jj < 0.20 || [dot $nn [lindex $nrm0 $i]] < 0.85} { set ok 0; break }
                    set pen [expr {$asp + ($amin < 45.0 ? (45.0 - $amin)*0.15 : 0.0) + ($jj < 0.7 ? (0.7 - $jj)*5.0 : 0.0)}]
                    if {$pen > $pen1} { set pen1 $pen }
                    set sum1 [expr {$sum1 + $pen}]
                    incr i
                }
                if {$ok && ($pen1 < $best_pen - 1e-4 || ($pen1 <= $best_pen + 1e-5 && $sum1 < $best_sum - 1e-4))} {
                    set best_pen $pen1; set best_sum $sum1; set best_pos $trial
                }
            }
            if {$best_pos ne ""} {
                set X($n) $best_pos
                set moved($n) 1; incr changed
            } else {
                set X($n) $old
            }
        }
        if {$changed == 0} { break }
    }
    return [array names moved]
}

# --- 3D Hex8 Interior-Only Jacobian Smoother ---
proc hqc6::smooth_hex_safe {min_jac {passes 8}} {
    *clearmark elems 1
    catch { *createmark elems 1 "by config" hex8 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} { return 0 }

    array set HX {}; array set HE {}; array set HNE {}; array set BND {}; array set face_cnt {}
    set face_idx {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}
    foreach e $eids {
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] != 8} { continue }
        set HE($e) $nds
        foreach n $nds {
            lappend HNE($n) $e
            if {![info exists HX($n)]} { set HX($n) [lindex [hm_nodevalue $n] 0] }
        }
        foreach fi $face_idx {
            set fnds {}; foreach idx $fi { lappend fnds [lindex $nds $idx] }
            set key [join [lsort -integer $fnds] ","]
            if {[info exists face_cnt($key)]} { incr face_cnt($key) } else { set face_cnt($key) 1 }
        }
    }
    foreach k [array names face_cnt] {
        if {$face_cnt($k) == 1} { foreach n [split $k ","] { set BND($n) 1 } }
    }
    *clearmark elems 1
    catch { *createmark elems 1 "by config" penta6 pyramid5 tetra4 }
    foreach e [hm_getmark elems 1] { foreach n [hm_nodelist $e] { set BND($n) 1 } }
    *clearmark elems 1

    array set J {}
    foreach e [array names HE] {
        set pts {}; foreach n $HE($e) { lappend pts $HX($n) }
        set J($e) [hex_min_jac $pts]
    }
    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        array unset cand; array set cand {}
        foreach e [array names J] {
            if {$J($e) < $min_jac} {
                foreach n $HE($e) { if {![info exists BND($n)]} { set cand($n) 1 } }
            }
        }
        if {[array size cand] == 0} { break }
        set changed 0
        foreach n [array names cand] {
            set els $HNE($n)
            array unset nbrs
            foreach e $els { foreach m $HE($e) { if {$m ne $n} { set nbrs($m) 1 } } }
            set nb [array names nbrs]
            if {[llength $nb] < 4} { continue }
            set cx 0.0; set cy 0.0; set cz 0.0
            foreach m $nb { lassign $HX($m) x y z; set cx [expr {$cx+$x}]; set cy [expr {$cy+$y}]; set cz [expr {$cz+$z}] }
            set k [expr {double([llength $nb])}]
            set cx [expr {$cx/$k}]; set cy [expr {$cy/$k}]; set cz [expr {$cz/$k}]
            set old $HX($n); lassign $old ox oy oz
            set j0 1e9; foreach e $els { if {$J($e) < $j0} { set j0 $J($e) } }
            set best_j $j0; set best_pos ""; set best_js {}
            foreach a {0.5 0.8 1.0 0.25} {
                set HX($n) [list [expr {$ox + $a*($cx-$ox)}] [expr {$oy + $a*($cy-$oy)}] [expr {$oz + $a*($cz-$oz)}]]
                set jmin 1e9; set js {}
                foreach e $els {
                    set pts {}; foreach m $HE($e) { lappend pts $HX($m) }
                    set j [hex_min_jac $pts]; lappend js $e $j
                    if {$j < $jmin} { set jmin $j }
                }
                if {$jmin > $best_j + 1e-4} { set best_j $jmin; set best_pos $HX($n); set best_js $js }
            }
            if {$best_pos ne ""} {
                set HX($n) $best_pos
                foreach {e j} $best_js { set J($e) $j }
                set moved($n) 1; incr changed
            } else { set HX($n) $old }
        }
        if {$changed == 0} { break }
    }
    set nm 0
    foreach n [array names moved] {
        lassign $HX($n) x y z
        if {![catch { *nodemodify $n $x $y $z }]} { incr nm }
    }
    return $nm
}

# =====================================================================
# MODULE 2 : Utilities, Measurement & HM Quality Checks
# =====================================================================
proc ylh6_count_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}

proc ylh6_delete_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
    *clearmark elems 1
}

proc ylh6_surf_elem_count {sid} {
    *clearmark surfs 1; *clearmark elems 2
    *createmark surfs 1 $sid
    set n 0
    if {![catch { *findmark surfs 1 257 1 elems 0 2 }]} { set n [llength [hm_getmark elems 2]] }
    *clearmark surfs 1; *clearmark elems 2
    return $n
}

proc ylh6_solid_3d_count {sid} {
    *clearmark solids 1; *clearmark elems 2
    *createmark solids 1 $sid
    set n 0
    if {![catch { *findmark solids 1 257 1 elems 0 2 }]} { set n [llength [hm_getmark elems 2]] }
    *clearmark solids 1; *clearmark elems 2
    return $n
}

proc ylh6_find_unmeshed_surfs {surf_ids} {
    set r {}
    foreach s $surf_ids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        set a 0.0; catch { set a [hm_getareaofsurface surfs $s] }
        if {$a > 1e-6 && [ylh6_surf_elem_count $s] == 0} { lappend r $s }
    }
    return $r
}

proc ylh6_edges_2d {mode} {
    catch { *createmark comps 1 "^edges"; *deletemark comps 1 }
    *clearmark elems 1
    catch { *createmark elems 1 "by config" quad4 tria3 }
    set n -1
    if {[llength [hm_getmark elems 1]] > 0 && ![catch { *findedges1 elems 1 $mode }]} {
        *clearmark elems 2
        catch { *createmark elems 2 "by comp name" "^edges" }
        set n [llength [hm_getmark elems 2]]
        *clearmark elems 2
    }
    catch { *createmark comps 1 "^edges"; *deletemark comps 1 }
    *clearmark elems 1
    return $n
}

proc ylh6_measure_2d {asp_lim} {
    *clearmark elems 1
    catch { *createmark elems 1 "by config" quad4 tria3 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} {
        return [dict create n 0 lmin 0.0 lp05 0.0 lmean 0.0 lp95 0.0 lmax 0.0 max_asp 0.0 bad_asp 0 worst {}]
    }
    array set XYZ {}
    set lens {}; set lsum 0.0; set max_asp 0.0; set bad_list {}
    foreach e $eids {
        set nds [hm_nodelist $e]
        set nn [llength $nds]
        if {$nn != 3 && $nn != 4} { continue }
        set pts {}
        foreach n $nds {
            if {![info exists XYZ($n)]} { set XYZ($n) [lindex [hm_nodevalue $n] 0] }
            lappend pts $XYZ($n)
        }
        if {$nn == 3} {
            set asp [lindex [hqc6::tri_metrics {*}$pts] 0]
            set pairs {{0 1} {1 2} {2 0}}
        } else {
            set asp [lindex [hqc6::quad_metrics {*}$pts] 0]
            set pairs {{0 1} {1 2} {2 3} {3 0}}
        }
        if {$asp > $max_asp} { set max_asp $asp }
        if {$asp > $asp_lim} { lappend bad_list [list $e $asp $nn] }
        foreach pr $pairs {
            set l [hqc6::dist [lindex $pts [lindex $pr 0]] [lindex $pts [lindex $pr 1]]]
            lappend lens $l; set lsum [expr {$lsum + $l}]
        }
    }
    set lens [lsort -real $lens]; set nl [llength $lens]
    set bad_list [lsort -real -decreasing -index 1 $bad_list]
    return [dict create n [llength $eids] \
        lmin  [lindex $lens 0] \
        lp05  [lindex $lens [expr {int(floor(0.05 * ($nl - 1)))}]] \
        lmean [expr {$lsum / $nl}] \
        lp95  [lindex $lens [expr {int(floor(0.95 * ($nl - 1)))}]] \
        lmax  [lindex $lens end] \
        max_asp $max_asp bad_asp [llength $bad_list] worst [lrange $bad_list 0 7]]
}

proc ylh6_measure_hex3d {{maxn 200000}} {
    *clearmark elems 1
    catch { *createmark elems 1 "by config" hex8 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} {
        return [dict create n 0 lmean 0.0 lmin 0.0 lmax 0.0 neg_jac 0 jac_min 0.0 jac_mean 0.0 warp_max 0.0]
    }
    set step 1
    if {[llength $eids] > $maxn} { set step [expr {int(ceil(double([llength $eids]) / $maxn))}] }
    array set XYZ {}
    set edges {{0 1} {1 2} {2 3} {3 0} {4 5} {5 6} {6 7} {7 4} {0 4} {1 5} {2 6} {3 7}}
    set faces {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}
    set lsum 0.0; set lcnt 0; set lmin 1e30; set lmax 0.0
    set jmin 1e9; set jsum 0.0; set neg 0; set wmax 0.0; set n 0; set i 0
    foreach e $eids {
        if {$i % $step} { incr i; continue }
        incr i
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] < 8} { continue }
        set P {}
        foreach nd $nds {
            if {![info exists XYZ($nd)]} { set XYZ($nd) [lindex [hm_nodevalue $nd] 0] }
            lappend P $XYZ($nd)
        }
        set j [hqc6::hex_min_jac $P]
        if {$j <= 0.0} { incr neg }
        if {$j < $jmin} { set jmin $j }
        set jsum [expr {$jsum + $j}]
        foreach f $faces {
            set w [hqc6::quad_warp [lindex $P [lindex $f 0]] [lindex $P [lindex $f 1]] [lindex $P [lindex $f 2]] [lindex $P [lindex $f 3]]]
            if {$w > $wmax} { set wmax $w }
        }
        foreach ed $edges {
            set l [hqc6::dist [lindex $P [lindex $ed 0]] [lindex $P [lindex $ed 1]]]
            if {$l < $lmin} { set lmin $l }; if {$l > $lmax} { set lmax $l }
            set lsum [expr {$lsum + $l}]; incr lcnt
        }
        incr n
    }
    if {$n == 0} { return [dict create n 0 lmean 0.0 lmin 0.0 lmax 0.0 neg_jac 0 jac_min 0.0 jac_mean 0.0 warp_max 0.0] }
    return [dict create n [llength $eids] lmean [expr {$lsum / $lcnt}] lmin $lmin lmax $lmax \
        neg_jac $neg jac_min $jmin jac_mean [expr {$jsum / $n}] warp_max $wmax]
}

proc ylh6_equivalence_3d {tol global} {
    if {!$global} {
        *clearmark comps 1; *createmark comps 1 "all"
        set cids [hm_getmark comps 1]
        *clearmark comps 1
        set ok 1
        foreach cid $cids {
            *clearmark elems 1
            if {[catch { *createmark elems 1 "by comp id" $cid }]} { set ok 0; break }
            if {[llength [hm_getmark elems 1]] > 1} { catch { *equivalence elems 1 $tol 1 0 0 } }
            *clearmark elems 1
        }
        *clearmark elems 1
        if {$ok} { return "per-comp" }
    }
    *clearmark elems 1
    catch { *createmark elems 1 "by config" hex8 penta6 pyramid5 tetra4 }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 $tol 1 0 0 } }
    *clearmark elems 1
    return "global"
}

proc ylh6_lookup_abaqus_type {ename tpl_path} {
    set ename [string toupper [string trim $ename]]
    if {[file exists $tpl_path]} {
        if {![catch {open $tpl_path r} fh]} {
            set txt [read $fh]; close $fh
            set pat [format {\*elements\(\s*(\d+)\s*,\s*(\d+)\s*,\s*"?%s"?\s*[,)]} $ename]
            if {[regexp -nocase $pat $txt -> cfg typ]} { return [list $cfg $typ] }
        }
    }
    array set fb {
        C3D8 {208 1} C3D8R {208 2} C3D8I {208 3} C3D8H {208 4} C3D8RH {208 5} C3D8IH {208 6}
        C3D20 {220 1} C3D20R {220 2} C3D20H {220 3} C3D20RH {220 4}
        C3D6 {206 1} C3D6H {206 2} C3D15 {215 1} C3D15H {215 2}
    }
    if {[info exists fb($ename)]} { return $fb($ename) }
    return {}
}

proc ylh6_wedge_for_hex {hex_type} {
    switch -exact -- $hex_type {
        C3D8    { return "C3D6" }   C3D8R   { return "C3D6" }   C3D8I   { return "C3D6" }
        C3D8H   { return "C3D6H" }  C3D8RH  { return "C3D6H" }  C3D8IH  { return "C3D6H" }
        C3D20   { return "C3D15" }  C3D20R  { return "C3D15" }
        C3D20H  { return "C3D15H" } C3D20RH { return "C3D15H" }
        default { return "C3D6H" }
    }
}

# Tria shell angles: {min_angle max_angle n_below_20 n_above_120}
proc ylh6_tria_angles {} {
    *clearmark elems 1
    catch { *createmark elems 1 "by config" tria3 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    set rad [expr {180.0 / acos(-1.0)}]
    set amin 180.0; set amax 0.0; set nlo 0; set nhi 0
    array set XYZ {}
    foreach e $eids {
        set P {}
        foreach n [lrange [hm_nodelist $e] 0 2] {
            if {![info exists XYZ($n)]} { set XYZ($n) [lindex [hm_nodevalue $n] 0] }
            lappend P $XYZ($n)
        }
        lassign $P a b c
        set la [hqc6::dist $b $c]; set lb [hqc6::dist $a $c]; set lc [hqc6::dist $a $b]
        if {$la < 1e-12 || $lb < 1e-12 || $lc < 1e-12} { set amin 0.0; incr nlo; continue }
        set A1 [expr {acos(max(-1.0, min(1.0, ($lb*$lb + $lc*$lc - $la*$la) / (2.0*$lb*$lc)))) * $rad}]
        set A2 [expr {acos(max(-1.0, min(1.0, ($la*$la + $lc*$lc - $lb*$lb) / (2.0*$la*$lc)))) * $rad}]
        set A3 [expr {180.0 - $A1 - $A2}]
        set lo [expr {min($A1,$A2,$A3)}]; set hi [expr {max($A1,$A2,$A3)}]
        if {$lo < $amin} { set amin $lo }; if {$hi > $amax} { set amax $hi }
        if {$lo < 20.0} { incr nlo }; if {$hi > 120.0} { incr nhi }
    }
    return [list $amin $amax $nlo $nhi]
}

# Independent tetra statistics (corner nodes 1-4, so tetra10 works too)
proc ylh6_measure_tet {col_lim vsk_lim {maxn 300000}} {
    *clearmark elems 1
    catch { *createmark elems 1 "by config" tetra4 tetra10 }
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    set R [dict create n [llength $eids] col_min 0.0 col_mean 0.0 col_bad 0 vsk_max 0.0 vsk_bad 0 \
        lmean 0.0 lp05 0.0 lp95 0.0 inverted 0]
    if {[llength $eids] == 0} { return $R }
    set step 1
    if {[llength $eids] > $maxn} { set step [expr {int(ceil(double([llength $eids]) / $maxn))}] }
    array set XYZ {}
    set cmin 1e9; set csum 0.0; set cbad 0; set vmax 0.0; set vbad 0
    set lm {}; set npos 0; set nneg 0; set ndeg 0; set n 0; set i 0
    foreach e $eids {
        if {$i % $step} { incr i; continue }
        incr i
        set nds [lrange [hm_nodelist $e] 0 3]
        if {[llength $nds] < 4} { continue }
        set P {}
        foreach nd $nds {
            if {![info exists XYZ($nd)]} { set XYZ($nd) [lindex [hm_nodevalue $nd] 0] }
            lappend P $XYZ($nd)
        }
        lassign [hqc6::tet_metrics {*}$P] vol6 col vsk lmin lmax lsum
        if {abs($vol6) < 1e-12} { incr ndeg } elseif {$vol6 > 0} { incr npos } else { incr nneg }
        if {$col < $cmin} { set cmin $col }
        set csum [expr {$csum + $col}]
        if {$col < $col_lim} { incr cbad }
        if {$vsk > $vmax} { set vmax $vsk }
        if {$vsk > $vsk_lim} { incr vbad }
        lappend lm [expr {$lsum / 6.0}]
        incr n
    }
    if {$n == 0} { return $R }
    set lm [lsort -real $lm]
    set sum 0.0; foreach x $lm { set sum [expr {$sum + $x}] }
    # inverted = tets whose orientation disagrees with the majority (+ degenerate ones)
    return [dict create n [llength $eids] col_min $cmin col_mean [expr {$csum / $n}] col_bad $cbad \
        vsk_max $vmax vsk_bad $vbad lmean [expr {$sum / $n}] \
        lp05 [lindex $lm [expr {int(floor(0.05 * ($n - 1)))}]] lp95 [lindex $lm [expr {int(floor(0.95 * ($n - 1)))}]] \
        inverted [expr {min($npos, $nneg) + $ndeg}]]
}

# =====================================================================
# MODULE 3 : 2D Pure-Quad Engine (STRICT 2.0 mm, ZERO Mixed Fallback!)
# =====================================================================
proc ylh6_find_bad_aspect_surfs {surf_ids asp_lim} {
    *clearmark elems 1; *clearmark elems 2
    catch { *createmark elems 1 "by config" quad4 tria3 }
    if {[llength [hm_getmark elems 1]] == 0} { *clearmark elems 1; return {} }
    if {[catch { *elementtestaspect elems 1 $asp_lim 2 2 0 "2D Aspect Check" }]} {
        *clearmark elems 1; *clearmark elems 2; return {}
    }
    array set bad_e {}
    foreach e [hm_getmark elems 2] { set bad_e($e) 1 }
    *clearmark elems 1; *clearmark elems 2
    if {[array size bad_e] == 0} { return {} }

    set bad_surfs {}
    foreach s $surf_ids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        *clearmark surfs 1; *clearmark elems 2
        *createmark surfs 1 $s
        if {![catch { *findmark surfs 1 257 1 elems 0 2 }]} {
            foreach e [hm_getmark elems 2] {
                if {[info exists bad_e($e)]} { lappend bad_surfs $s; break }
            }
        }
    }
    *clearmark surfs 1; *clearmark elems 2
    return $bad_surfs
}

proc ylh6_run_2d {cfg_dict} {
    ylh6_ckpt_init
    set ELEM_SIZE       2.0
    set TARGET_ASPECT   [expr {double([dict get $cfg_dict target_aspect])}]
    set MIN_JACOBIAN    [expr {double([dict get $cfg_dict min_jac])}]
    set MAX_WARPAGE     [expr {double([dict get $cfg_dict max_warp])}]
    set MAX_SKEW        [expr {double([dict get $cfg_dict max_skew])}]
    set FEATURE_ANGLE   [expr {double([dict get $cfg_dict feature_angle])}]
    set SLIVER_EDGE_MAX [expr {double([dict get $cfg_dict sliver_edge_max])}]
    set DO_ZIPPER       [dict get $cfg_dict do_zipper]
    set DO_TRIA_MERGE   [dict get $cfg_dict do_tria_merge]
    set DO_LOCAL_REMESH [dict get $cfg_dict do_local_remesh]
    set LOCAL_SZ1       [expr {double([dict get $cfg_dict local_size_1])}]
    set DO_SMOOTH       [dict get $cfg_dict do_smooth]
    set SMOOTH_PASSES   [dict get $cfg_dict smooth_passes]
    set PATCH_HOLES     [dict get $cfg_dict patch_holes]
    set SELECT_MODE     [dict get $cfg_dict select_mode]
    set SAVE_HM         [dict get $cfg_dict save_hm]
    set OUT_DIR         [dict get $cfg_dict out_dir]
    set OUT_NAME        [dict get $cfg_dict out_name]

    # STRICT Pure-Quad type = 4 ('quads only') everywhere!
    set am_type 4
    ylh6_ckpt "2D_PURE_START" "size=2.0mm am_type=4(quads_only) select=$SELECT_MODE"

    # 1. Gather surfaces
    *clearmark surfs 1
    if {$SELECT_MODE eq "pick"} {
        ylh6_ckpt "2D_PICK_SURFS" "Waiting for user selection"
        *createmarkpanel surfs 1 "Select surfaces for PURE-QUAD 2.0 mm Mesh"
    } else {
        *createmark surfs 1 "all"
    }
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
    set n_surfs [llength $surf_ids]
    ylh6_ckpt "2D_SURFS_READY" "count=$n_surfs"
    if {$n_surfs == 0} { return [dict create status FAIL msg "No surfaces found/selected."] }

    # 2. Clear existing elements & reset edge density
    if {$SELECT_MODE eq "all"} {
        ylh6_ckpt "2D_CLEAR_OLD" "Deleting old elements"
        *clearmark elems 1; *createmark elems 1 "all"
        if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
        *clearmark elems 1
    }
    catch { *ameshclearsurface }
    catch { *elementorder 1 }
    catch { *setedgedensitylink 0 }

    # 3. Base 2.0 mm PURE-QUAD Mesh (type = 4 = quads only!)
    set mesh_err ""
    ylh6_ckpt "2D_PUREQUAD_EXEC" "Running *defaultremeshsurf size=2.0 type=4(quads_only)"
    *clearmark surfs 1; eval *createmark surfs 1 $surf_ids
    if {[catch { *defaultremeshsurf 1 $ELEM_SIZE $am_type $am_type 2 1 1 1 1 0 0 0 0 } err]} {
        set mesh_err "defaultremeshsurf(quads_only): $err"
    }
    *clearmark surfs 1
    ylh6_ckpt "2D_PUREQUAD_DONE" "quad4=[ylh6_count_elems quad4] tria3=[ylh6_count_elems tria3]"

    # 4. Patch any unmeshed surfaces STILL using quads_only (type=4)!
    set n_patched 0; set n_unmeshed 0
    if {$PATCH_HOLES} {
        set unm [ylh6_find_unmeshed_surfs $surf_ids]
        set n0 [llength $unm]
        if {$n0 > 0} {
            ylh6_ckpt "2D_PATCH_PUREQUAD" "Patching $n0 unmeshed surf(s) at 1.0mm (quads_only)"
            *clearmark surfs 1; eval *createmark surfs 1 $unm
            catch { *defaultremeshsurf 1 1.0 4 4 2 1 1 1 1 0 0 0 0 }
            *clearmark surfs 1
            set still [ylh6_find_unmeshed_surfs $unm]
            if {[llength $still] > 0} {
                ylh6_ckpt "2D_PATCH_PUREQUAD_2" "Patching [llength $still] remaining surf(s) at 0.7mm (quads_only)"
                *clearmark surfs 1; eval *createmark surfs 1 $still
                catch { *defaultremeshsurf 1 0.7 4 4 2 1 1 1 1 0 0 0 0 }
                *clearmark surfs 1
            }
            set n_unmeshed [llength [ylh6_find_unmeshed_surfs $unm]]
            set n_patched  [expr {$n0 - $n_unmeshed}]
        }
    } else {
        set n_unmeshed [llength [ylh6_find_unmeshed_surfs $surf_ids]]
    }

    # Measure RAW pure-quad stats
    set RAW [ylh6_measure_2d $TARGET_ASPECT]
    ylh6_ckpt "2D_RAW_STATS" "quad=[ylh6_count_elems quad4] tria=[ylh6_count_elems tria3] bad_asp=[dict get $RAW bad_asp] max_asp=[format %.2f [dict get $RAW max_asp]]"

    # 5. Local fine-remesh ONLY on bad-aspect surfaces using quads_only (type=4)!
    set n_local_remeshed 0
    if {$DO_LOCAL_REMESH} {
        set bad_s [ylh6_find_bad_aspect_surfs $surf_ids $TARGET_ASPECT]
        if {[llength $bad_s] > 0} {
            ylh6_ckpt "2D_LOCAL_PUREQUAD" "Remeshing [llength $bad_s] bad surf(s) at ${LOCAL_SZ1}mm (quads_only)"
            *clearmark surfs 1; eval *createmark surfs 1 $bad_s
            catch { *defaultremeshsurf 1 $LOCAL_SZ1 4 4 2 1 1 1 1 0 0 0 0 }
            *clearmark surfs 1
            set n_local_remeshed [llength $bad_s]
        }
    }

    # Equivalence before mesh-level repair
    ylh6_ckpt "2D_EQUIV_1" "Merging coincident nodes (tol=0.01mm)"
    *clearmark elems 1
    catch { *createmark elems 1 "by config" quad4 tria3 }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 0.01 1 0 0 } }
    *clearmark elems 1

    # 6. Mesh-Level Repair (V-Tip Zipper Collapse + Tria-Pair->Quad Merge + Quality Relaxation)
    set n_zipped 0; set n_merged 0; set n_relaxed 0
    if {$DO_ZIPPER || $DO_TRIA_MERGE || $DO_SMOOTH} {
        ylh6_ckpt "2D_REPAIR_LOAD" "Loading 2D mesh into memory"
        set n_loaded [hqc6::load_2d_hm]
        if {$n_loaded > 0} {
            if {$DO_ZIPPER} {
                ylh6_ckpt "2D_ZIPPER_START" "Zipping V-tip slivers (asp>$TARGET_ASPECT, edge<${SLIVER_EDGE_MAX}mm)"
                set n_zipped [hqc6::zipper_collapse_inmem $TARGET_ASPECT $SLIVER_EDGE_MAX $FEATURE_ANGLE]
                ylh6_ckpt "2D_ZIPPER_DONE" "collapsed=$n_zipped"
            }
            if {$DO_TRIA_MERGE} {
                ylh6_ckpt "2D_TRIAMERGE_START" "Merging adjacent Tria3 pairs into Quad4"
                set n_merged [hqc6::merge_tria_pairs_inmem $TARGET_ASPECT $FEATURE_ANGLE]
                ylh6_ckpt "2D_TRIAMERGE_DONE" "merged_pairs=$n_merged"
            }
            set moved_nodes {}
            if {$DO_SMOOTH && $SMOOTH_PASSES > 0} {
                ylh6_ckpt "2D_RELAX_START" "Quality-driven quad relaxation (passes=$SMOOTH_PASSES)"
                set moved_nodes [hqc6::relax_2d_inmem $ELEM_SIZE $FEATURE_ANGLE $SMOOTH_PASSES]
                set n_relaxed [llength $moved_nodes]
                ylh6_ckpt "2D_RELAX_DONE" "nodes_relaxed=$n_relaxed"
            }
            ylh6_ckpt "2D_REPAIR_COMMIT" "Writing repaired pure-quad mesh back to HM"
            hqc6::commit_2d_hm $moved_nodes
        }
        *clearmark elems 1
        catch { *createmark elems 1 "by config" quad4 tria3 }
        if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 0.01 1 0 0 } }
        *clearmark elems 1
    }

    # 7. Final Verification
    ylh6_ckpt "2D_VERIFY" "Running final quality checks"
    set FIN      [ylh6_measure_2d $TARGET_ASPECT]
    set n_quad   [ylh6_count_elems quad4]
    set n_tria   [ylh6_count_elems tria3]
    set tria_pct [expr {($n_quad + $n_tria) > 0 ? 100.0 * $n_tria / ($n_quad + $n_tria) : 0.0}]
    set free_e   [ylh6_edges_2d 0]
    set t_e      [ylh6_edges_2d 1]

    set hm_asp -1; set hm_jac -1; set hm_warp -1; set hm_skew -1
    if {$n_quad + $n_tria > 0} {
        foreach {var cmd lim lbl} [list \
            hm_asp  *elementtestaspect   $TARGET_ASPECT "2D Aspect" \
            hm_jac  *elementtestjacobian $MIN_JACOBIAN  "2D Jacobian" \
            hm_warp *elementtestwarpage  $MAX_WARPAGE   "2D Warpage" \
            hm_skew *elementtestskew     $MAX_SKEW      "2D Skew"] {
            *clearmark elems 1; *clearmark elems 2
            *createmark elems 1 "by config" quad4 tria3
            if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
            *clearmark elems 1; *clearmark elems 2
        }
    }

    set saved_txt "(not saved)"; set write_err ""
    if {$SAVE_HM && $OUT_DIR ne ""} {
        file mkdir $OUT_DIR
        set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_V6_PUREQUAD_2mm"}]
        set out_hm [file join $OUT_DIR "${stem}.hm"]
        catch { hm_answernext yes }
        if {[catch { *writefile $out_hm 1 } write_err]} { set saved_txt "SAVE FAILED: $write_err" } else { set saved_txt $out_hm }
    }

    set pass [expr {$mesh_err eq "" && $write_err eq "" && $n_quad > 0 && $n_tria == 0 && $n_unmeshed == 0 && $hm_asp == 0 && $free_e == 0 && $t_e == 0 ? 1 : 0}]
    set status [expr {$pass ? "OK" : "CHECK"}]
    set msg [format "YL_HEX_V6 PURE-QUAD (STRICT 2.0 mm) Finished  \[%s\]\n\n- Element Mode: QUADS ONLY (type=4 everywhere, zero mixed fallback)\n- MEASURED Edge Length (mm):\n    mean = %.2f mm  (p05=%.2f, p95=%.2f, min=%.2f, max=%.2f)\n- Elements: Quad4=%d, Tria3=%d (Tria=%.2f%%)\n- Holes: %d patched, %d still unmeshed\n- Targeted Repairs Applied:\n    Local Pure-Quad Remesh: %d surf(s) at %.1f mm\n    V-Tip Sliver Zipper Collapses: %d\n    Tria-Pair -> Quad4 Merges: %d\n    Quality-Driven Relaxed Nodes: %d\n- Aspect > %.1f:\n    RAW=%d (max=%.2f)  -->  FINAL=%d (max=%.2f)\n    HM Check Elements (2-d aspect > %.1f): %d failed\n- Skin Topology: Free Edges=%d, T-Edges=%d\n- HM Quality: BadJac(<%.2f)=%d, BadWarp(>%.1f)=%d, BadSkew(>%.1f)=%d\n- Saved HM: %s\n- Checkpoint Log: %s" \
        $status \
        [dict get $FIN lmean] [dict get $FIN lp05] [dict get $FIN lp95] [dict get $FIN lmin] [dict get $FIN lmax] \
        $n_quad $n_tria $tria_pct $n_patched $n_unmeshed \
        $n_local_remeshed $LOCAL_SZ1 $n_zipped $n_merged $n_relaxed \
        $TARGET_ASPECT [dict get $RAW bad_asp] [dict get $RAW max_asp] [dict get $FIN bad_asp] [dict get $FIN max_asp] \
        $TARGET_ASPECT $hm_asp \
        $free_e $t_e \
        $MIN_JACOBIAN $hm_jac $MAX_WARPAGE $hm_warp $MAX_SKEW $hm_skew \
        $saved_txt $::YLH6_CKPT_FILE]

    set wlist [dict get $FIN worst]
    if {[llength $wlist] > 0} {
        append msg "\n\nWorst Remaining Elements (ID : aspect : type):"
        foreach item [lrange $wlist 0 4] {
            lassign $item eid asp nn
            append msg [format "\n  elem %s : aspect=%.2f (%s)" $eid $asp [expr {$nn==4 ? "quad4" : "tria3"}]]
        }
    }
    if {$mesh_err ne ""} { append msg "\n\n*** $mesh_err" }

    catch { set fh [open $::YLH6_REPORT_FILE w]; puts $fh $msg; close $fh }
    ylh6_ckpt "2D_PURE_FINISH" "status=$status quad=$n_quad tria=$n_tria hm_asp=$hm_asp"
    return [dict create status $status msg $msg]
}

# =====================================================================
# MODULE 4 : 3D Pure-Hexa Multi-Solid Map Engine (STRICT 2.0 mm)
# =====================================================================
proc ylh6_run_3d {cfg_dict} {
    ylh6_ckpt_init
    set ELEM_SIZE        2.0
    set TARGET_ASPECT    [expr {double([dict get $cfg_dict target_aspect])}]
    set MIN_JACOBIAN     [expr {double([dict get $cfg_dict min_jac])}]
    set MAX_WARPAGE      [expr {double([dict get $cfg_dict max_warp])}]
    set MAX_SKEW         [expr {double([dict get $cfg_dict max_skew])}]
    set DO_SMOOTH        [dict get $cfg_dict do_smooth]
    set SMOOTH_PASSES    [dict get $cfg_dict smooth_passes]
    set ABAQUS_ELEM_TYPE [string toupper [string trim [dict get $cfg_dict elem_type]]]
    set DELETE_2D_SKIN   [dict get $cfg_dict delete_2d]
    set EQUIV_GLOBAL     [dict get $cfg_dict equiv_global]
    set QUAD_FALLBACK    [dict get $cfg_dict quad_fallback]
    set SELECT_MODE      [dict get $cfg_dict select_mode]
    set SAVE_HM          [dict get $cfg_dict save_hm]
    set EXPORT_INP       [dict get $cfg_dict export_inp]
    set OUT_DIR          [dict get $cfg_dict out_dir]
    set OUT_NAME         [dict get $cfg_dict out_name]
    set CUSTOM_FLAGS     [string trim [dict get $cfg_dict sm_flags]]

    ylh6_ckpt "3D_PURE_START" "size=2.0mm type=$ABAQUS_ELEM_TYPE flags=quads_only(131076)"

    *clearmark solids 1
    if {$SELECT_MODE eq "pick"} {
        *createmarkpanel solids 1 "Select Mappable Solids for 3D PURE-HEXA (2.0 mm)"
    } else {
        *createmark solids 1 "all"
    }
    set solid_ids [hm_getmark solids 1]
    *clearmark solids 1
    set n_solids [llength $solid_ids]

    if {$n_solids == 0} {
        if {$QUAD_FALLBACK} {
            if {[tk_messageBox -type yesno -icon warning -title "YL HEX V6 PURE" -message "No solids found.\nRun 2D PURE-QUAD (2.0 mm) on surfaces instead?"] eq "yes"} {
                return [ylh6_run_2d $cfg_dict]
            }
        }
        return [dict create status FAIL msg "No solids found/selected."]
    }

    if {$SELECT_MODE eq "all"} {
        ylh6_delete_elems {tria3 tria6 quad4 quad8 tetra4 tetra10 hex8 hex20 penta6 penta15 pyramid5 pyramid13}
    }
    catch { *ameshclearsurface }
    catch { *elementorder 1 }
    catch { *setedgedensitylink 0 }

    # Default flags = 131076 (quads_only -> PURE HEXA, no penta wedges!)
    set sm_flags 131076
    if {$CUSTOM_FLAGS ne "" && $CUSTOM_FLAGS ne "auto"} { set sm_flags $CUSTOM_FLAGS }

    ylh6_ckpt "3D_SOLIDMAP_EXEC" "flags=$sm_flags size=2.0mm"
    set sm_err ""
    *clearmark solids 1; eval *createmark solids 1 $solid_ids
    if {[catch { *solidmap_solids_begin 1 $sm_flags $ELEM_SIZE; *solidmap_solids_end } e1]} {
        ylh6_ckpt "3D_SOLIDMAP_RETRY" "flags=$sm_flags failed ($e1), retrying flags=131073"
        *clearmark solids 1; eval *createmark solids 1 $solid_ids
        if {[catch { *solidmap_solids_begin 1 131073 $ELEM_SIZE; *solidmap_solids_end } e2]} {
            set sm_err "solidmap failed: $e1 / $e2"
        }
    }
    *clearmark solids 1

    if {$DELETE_2D_SKIN} { ylh6_delete_elems {tria3 tria6 quad4 quad8} }
    set equiv_mode [ylh6_equivalence_3d 0.002 $EQUIV_GLOBAL]
    set n_hex   [ylh6_count_elems {hex8 hex20}]
    set n_penta [ylh6_count_elems {penta6 penta15}]

    set unmap_ids {}
    foreach s $solid_ids { if {[ylh6_solid_3d_count $s] == 0} { lappend unmap_ids $s } }
    set n_unmap [llength $unmap_ids]
    if {$n_unmap == $n_solids && $n_hex + $n_penta > 0} { set n_unmap -1; set unmap_ids {} }
    ylh6_ckpt "3D_UNMAP_CHECK" "hex=$n_hex penta=$n_penta unmap=$n_unmap/$n_solids"

    if {$QUAD_FALLBACK && ($n_hex == 0 || $n_unmap > 0)} {
        set q "3D Pure-Hexa SolidMap: Hex=$n_hex, Unmappable solids=$n_unmap / $n_solids.\nSplit complex solids (Geom -> Solid Edit -> Trim) into mappable blocks for 3D Pure Hexa.\n\nSwitch to 2D PURE-QUAD (2.0 mm + V-Tip Zipper + Tria Merge) now?"
        if {[tk_messageBox -type yesno -icon question -title "YL HEX V6 - Unmappable Solid" -message $q] eq "yes"} {
            return [ylh6_run_2d $cfg_dict]
        }
    }

    set n_sm 0
    if {$DO_SMOOTH && $n_hex > 0 && $SMOOTH_PASSES > 0} {
        ylh6_ckpt "3D_SMOOTH" "Hex8 interior smoothing"
        catch { set n_sm [hqc6::smooth_hex_safe $MIN_JACOBIAN $SMOOTH_PASSES] }
    }

    set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
    if {$n_hex + $n_penta > 0} {
        foreach {var cmd lim lbl} [list \
            bad_jac  *elementtestjacobian $MIN_JACOBIAN  "3D Jacobian" \
            bad_warp *elementtestwarpage  $MAX_WARPAGE   "3D Warpage" \
            bad_asp  *elementtestaspect   $TARGET_ASPECT "3D Aspect" \
            bad_skew *elementtestskew     $MAX_SKEW      "3D Skew"] {
            *clearmark elems 1; *clearmark elems 2
            *createmark elems 1 "by config" hex8 penta6
            if {![catch { $cmd elems 1 $lim 2 2 1 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
            *clearmark elems 1; *clearmark elems 2
        }
    }
    set H3 [ylh6_measure_hex3d 200000]

    set tpl_path "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d"
    set assigned_type ""; set type_err ""
    if {$n_hex + $n_penta > 0} {
        catch { *templatefileset $tpl_path }
        set hp [ylh6_lookup_abaqus_type $ABAQUS_ELEM_TYPE $tpl_path]
        if {[llength $hp] == 2} {
            lassign $hp hcfg htyp
            set is2 [expr {$hcfg == 220}]
            if {$is2} {
                *clearmark elems 1; *createmark elems 1 "by config" hex8 penta6
                if {[llength [hm_getmark elems 1]] > 0} { catch { *orderchangetosecond 1 1 } }
                *clearmark elems 1
            }
            *clearmark elems 1; *createmark elems 1 "by config" [expr {$is2 ? "hex20" : "hex8"}]
            set e0 [lindex [hm_getmark elems 1] 0]
            if {$e0 ne ""} {
                catch { *elementtype $hcfg $htyp }; catch { *elementsettypes 1 }
                catch { set assigned_type [hm_getvalue elems id=$e0 dataname=typename] }
            }
            *clearmark elems 1
            if {$n_penta > 0} {
                set wp [ylh6_lookup_abaqus_type [ylh6_wedge_for_hex $ABAQUS_ELEM_TYPE] $tpl_path]
                if {[llength $wp] == 2} {
                    lassign $wp wcfg wtyp
                    *clearmark elems 1; *createmark elems 1 "by config" [expr {$is2 ? "penta15" : "penta6"}]
                    if {[llength [hm_getmark elems 1]] > 0} { catch { *elementtype $wcfg $wtyp }; catch { *elementsettypes 1 } }
                    *clearmark elems 1
                }
            }
        } else { set type_err "Unknown Abaqus type '$ABAQUS_ELEM_TYPE'" }
    }

    set n_hex   [ylh6_count_elems {hex8 hex20}]
    set n_penta [ylh6_count_elems {penta6 penta15}]

    set saved_hm "(not saved)"; set saved_inp "(not exported)"
    set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_V6_PUREHEX_${ABAQUS_ELEM_TYPE}_2mm"}]
    if {$SAVE_HM && $OUT_DIR ne ""} {
        file mkdir $OUT_DIR
        set out_hm [file join $OUT_DIR "${stem}.hm"]
        catch { hm_answernext yes }
        if {![catch { *writefile $out_hm 1 }]} { set saved_hm $out_hm }
    }
    if {$EXPORT_INP && $OUT_DIR ne "" && $n_hex > 0 && [dict get $H3 neg_jac] == 0} {
        file mkdir $OUT_DIR
        set out_inp [file join $OUT_DIR "${stem}.inp"]
        catch { file delete $out_inp }; catch { hm_answernext yes }
        if {![catch { *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"; *feoutputwithdata $tpl_path $out_inp 0 0 2 1 2 }] && [file exists $out_inp]} {
            set saved_inp $out_inp
        }
    }

    set status [expr {$sm_err eq "" && $type_err eq "" && $n_hex > 0 && $n_penta == 0 && $n_unmap == 0 && [dict get $H3 neg_jac] == 0 && $bad_jac == 0 && $bad_asp == 0 && $bad_warp == 0 ? "OK" : "CHECK"}]
    set msg [format "YL_HEX_V6 PURE-HEXA (STRICT 2.0 mm) Finished  \[%s\]\n\n- Target Size: 2.00 mm (LOCKED), Type: %s (actual: %s)\n- MEASURED 3D Edge Length: mean=%.2f mm (min=%.2f, max=%.2f)\n- Solids: %d (Unmappable: %d), Equivalence: %s\n- Elements: Hex=%d, Penta=%d, Smoothed nodes=%d\n- 3D Quality (HM dim=1):\n    Bad Jacobian (<%.2f) : %d\n    Bad Aspect   (>%.1f) : %d\n    Bad Warpage  (>%.1f) : %d\n    Bad Skew     (>%.1f) : %d\n- Independent Hex8 Stats: NegJac=%d, SJ min=%.3f, SJ mean=%.3f, Warp max=%.2f\n- Saved HM: %s\n- Exported INP: %s\n- Checkpoint Log: %s" \
        $status $ABAQUS_ELEM_TYPE $assigned_type \
        [dict get $H3 lmean] [dict get $H3 lmin] [dict get $H3 lmax] \
        $n_solids $n_unmap $equiv_mode $n_hex $n_penta $n_sm \
        $MIN_JACOBIAN $bad_jac $TARGET_ASPECT $bad_asp $MAX_WARPAGE $bad_warp $MAX_SKEW $bad_skew \
        [dict get $H3 neg_jac] [dict get $H3 jac_min] [dict get $H3 jac_mean] [dict get $H3 warp_max] \
        $saved_hm $saved_inp $::YLH6_CKPT_FILE]
    if {$sm_err ne ""}   { append msg "\n\n*** $sm_err" }
    if {$type_err ne ""} { append msg "\n*** $type_err" }

    catch { set fh [open $::YLH6_REPORT_FILE w]; puts $fh $msg; close $fh }
    ylh6_ckpt "3D_PURE_FINISH" "status=$status hex=$n_hex penta=$n_penta"
    return [dict create status $status msg $msg]
}

# =====================================================================
# MODULE 4B : 3D TETRA Engine (STRICT 2.0 mm)
#   closed 2.0 mm tria shell -> sliver collapse + relax (NO tria->quad merge)
#   -> tetmesh -> Abaqus tetra order/type -> tetra quality
#   Works on surfaces-only models: it needs a CLOSED shell, not a solid entity.
# =====================================================================

# --- [TUNABLE] HyperMesh tetmesh call (mark 1 = closed tria shell) ---
#   Two common argument layouts are tried in order. If both fail in your
#   HyperMesh, paste the *tetmesh line from your working tetra script (or from
#   command.tcl after one manual 3D > tetramesh run) into this proc.
proc ylh6_tetmesh_exec {} {
    set pars "pars: upd_shell fix_comp_bdr post_cln elem_order = 1 delaunay el2comp=0 fill_void=1 tet_clps='0.100000,0.300000, 0.560000, 0.660000, 0.100000, 0.300000, 0.500000'"
    set errs {}
    foreach variant {A B} {
        set before [ylh6_count_elems {tetra4 tetra10}]
        *clearmark elems 1; *clearmark elems 2
        catch { *createmark elems 1 "by config" tria3 quad4 }
        if {$variant eq "A"} {
            set rc [catch { *tetmesh elements 1 1 elements 0 -1 1 $pars } e]
        } else {
            set rc [catch { *tetmesh elements 1 1 elements 2 0 $pars } e]
        }
        *clearmark elems 1; *clearmark elems 2
        if {[ylh6_count_elems {tetra4 tetra10}] > $before} { return [list 1 "tetmesh layout $variant"] }
        lappend errs "layout $variant: [expr {$rc ? $e : {ran but created 0 tetra}}]"
    }
    return [list 0 [join $errs " | "]]
}

proc ylh6_run_tet {cfg_dict} {
    ylh6_ckpt_init
    set ELEM_SIZE       2.0
    set TARGET_ASPECT   [expr {double([dict get $cfg_dict target_aspect])}]
    set MIN_JACOBIAN    [expr {double([dict get $cfg_dict min_jac])}]
    set FEATURE_ANGLE   [expr {double([dict get $cfg_dict feature_angle])}]
    set SLIVER_EDGE_MAX [expr {double([dict get $cfg_dict sliver_edge_max])}]
    set TET_COLLAPSE    [expr {double([dict get $cfg_dict tet_collapse_min])}]
    set TET_VSKEW       [expr {double([dict get $cfg_dict tet_vskew_max])}]
    set TET_TYPE        [string toupper [string trim [dict get $cfg_dict tet_elem_type]]]
    set DO_ZIPPER       [dict get $cfg_dict do_zipper]
    set DO_SMOOTH       [dict get $cfg_dict do_smooth]
    set SMOOTH_PASSES   [dict get $cfg_dict smooth_passes]
    set PATCH_HOLES     [dict get $cfg_dict patch_holes]
    set DELETE_2D       [dict get $cfg_dict delete_2d]
    set SELECT_MODE     [dict get $cfg_dict select_mode]
    set SAVE_HM         [dict get $cfg_dict save_hm]
    set EXPORT_INP      [dict get $cfg_dict export_inp]
    set OUT_DIR         [dict get $cfg_dict out_dir]
    set OUT_NAME        [dict get $cfg_dict out_name]
    set MAX_SKEW        [expr {double([dict get $cfg_dict max_skew])}]
    set SHELL_ONLY      [expr {[dict exists $cfg_dict shell_only] && [dict get $cfg_dict shell_only]}]
    ylh6_ckpt "TET_START" "size=2.0mm type=$TET_TYPE select=$SELECT_MODE shell_only=$SHELL_ONLY"

    # 1. Surfaces forming the closed shell
    *clearmark surfs 1
    if {$SELECT_MODE eq "pick"} {
        *createmarkpanel surfs 1 "Select the CLOSED set of surfaces for TETRA (2.0 mm)"
    } else {
        *createmark surfs 1 "all"
    }
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
    if {[llength $surf_ids] == 0} { return [dict create status FAIL msg "No surfaces found/selected."] }

    # 2. Clear old elements
    if {$SELECT_MODE eq "all"} {
        *clearmark elems 1; *createmark elems 1 "all"
        if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
        *clearmark elems 1
    }
    catch { *ameshclearsurface }
    catch { *elementorder 1 }
    catch { *setedgedensitylink 0 }

    # 3. 2.0 mm TRIA shell (automesh type 0 = trias) + patch unmeshed surfaces with trias
    set mesh_err ""
    ylh6_ckpt "TET_SHELL_EXEC" "*defaultremeshsurf size=2.0 type=0(trias)"
    *clearmark surfs 1; eval *createmark surfs 1 $surf_ids
    if {[catch { *defaultremeshsurf 1 $ELEM_SIZE 0 0 2 1 1 1 1 0 0 0 0 } err]} { set mesh_err "defaultremeshsurf(trias): $err" }
    *clearmark surfs 1
    set n_unmeshed 0
    if {$PATCH_HOLES} {
        foreach sz {1.0 0.7} {
            set unm [ylh6_find_unmeshed_surfs $surf_ids]
            if {[llength $unm] == 0} { break }
            *clearmark surfs 1; eval *createmark surfs 1 $unm
            catch { *defaultremeshsurf 1 $sz 0 0 2 1 1 1 1 0 0 0 0 }
            *clearmark surfs 1
        }
    }
    set n_unmeshed [llength [ylh6_find_unmeshed_surfs $surf_ids]]
    *clearmark elems 1
    catch { *createmark elems 1 "by config" tria3 quad4 }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 0.01 1 0 0 } }
    *clearmark elems 1

    # 4. Shell repair: sliver collapse + relaxation (tria->quad merge is NOT used for tetra)
    set n_zipped 0; set n_relaxed 0
    if {$DO_ZIPPER || $DO_SMOOTH} {
        if {[hqc6::load_2d_hm] > 0} {
            if {$DO_ZIPPER} { set n_zipped [hqc6::zipper_collapse_inmem $TARGET_ASPECT $SLIVER_EDGE_MAX $FEATURE_ANGLE] }
            set moved {}
            if {$DO_SMOOTH && $SMOOTH_PASSES > 0} {
                set moved [hqc6::relax_2d_inmem $ELEM_SIZE $FEATURE_ANGLE $SMOOTH_PASSES]
                set n_relaxed [llength $moved]
            }
            hqc6::commit_2d_hm $moved
        }
        *clearmark elems 1
        catch { *createmark elems 1 "by config" tria3 quad4 }
        if {[llength [hm_getmark elems 1]] > 0} { catch { *equivalence elems 1 0.01 1 0 0 } }
        *clearmark elems 1
    }

    # 5. Shell must be closed before tetmesh
    set n_tria [ylh6_count_elems tria3]
    set n_quad [ylh6_count_elems quad4]
    set free_e [ylh6_edges_2d 0]
    set t_e    [ylh6_edges_2d 1]
    lassign [ylh6_tria_angles] ang_min ang_max ang_lo ang_hi
    ylh6_ckpt "TET_SHELL_READY" "tria=$n_tria quad=$n_quad free=$free_e tedge=$t_e minang=[format %.1f $ang_min]"
    set shell_txt [format "tria3=%d, quad4=%d, unmeshed surfs=%d, free edges=%d, T-edges=%d\n    repairs: %d sliver collapses, %d nodes relaxed\n    tria angles: min=%.1f (%d below 20), max=%.1f (%d above 120)" \
        $n_tria $n_quad $n_unmeshed $free_e $t_e $n_zipped $n_relaxed $ang_min $ang_lo $ang_max $ang_hi]
    # 2D TRIA SKIN (tetra preparation): report the shell and stop here - no tetmesh
    if {$SHELL_ONLY} {
        set FIN [ylh6_measure_2d $TARGET_ASPECT]
        set hm_asp -1; set hm_skew -1
        foreach {var cmd lim lbl} [list \
            hm_asp  *elementtestaspect $TARGET_ASPECT "2D Aspect" \
            hm_skew *elementtestskew   $MAX_SKEW      "2D Skew"] {
            *clearmark elems 1; *clearmark elems 2
            catch { *createmark elems 1 "by config" tria3 quad4 }
            if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
            *clearmark elems 1; *clearmark elems 2
        }
        set closed [expr {$n_tria > 0 && $n_quad == 0 && $free_e == 0 && $t_e == 0}]
        set saved_hm "(not saved)"
        if {$SAVE_HM && $OUT_DIR ne ""} {
            file mkdir $OUT_DIR
            set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_V6_TRIASKIN_2mm"}]
            set out_hm [file join $OUT_DIR "${stem}.hm"]
            catch { hm_answernext yes }
            if {![catch { *writefile $out_hm 1 }]} { set saved_hm $out_hm }
        }
        set pass [expr {$mesh_err eq "" && $closed && $n_unmeshed == 0 && [dict get $FIN bad_asp] == 0 \
            && $hm_asp <= 0 && $ang_lo == 0 && $ang_hi == 0 ? 1 : 0}]
        set status [expr {$pass ? "OK" : "CHECK"}]
        set na {x { expr {$x < 0 ? "n/a" : $x} }}
        set msg [format "YL_HEX_V6 2D TRIA SKIN (STRICT 2.0 mm, tetra preparation) Finished  \[%s\]\n\n- Element Mode: TRIAS (automesh type 0) - surface skin only, no tetra created\n- MEASURED Edge Length: mean=%.2f mm (p05=%.2f, p95=%.2f, min=%.2f, max=%.2f)\n- Tria shell: %s\n- Aspect > %.1f: independent=%d (max=%.2f), HM check=%s\n- HM Skew > %.1f: %s\n- Ready for 3D Tetra (closed shell, trias only): %s\n- Saved HM: %s\n- Checkpoint Log: %s" \
            $status [dict get $FIN lmean] [dict get $FIN lp05] [dict get $FIN lp95] [dict get $FIN lmin] [dict get $FIN lmax] \
            $shell_txt $TARGET_ASPECT [dict get $FIN bad_asp] [dict get $FIN max_asp] [apply $na $hm_asp] \
            $MAX_SKEW [apply $na $hm_skew] [expr {$closed ? "YES" : "NO"}] $saved_hm $::YLH6_CKPT_FILE]
        if {$mesh_err ne ""} { append msg "\n\n*** $mesh_err" }
        catch { set fh [open $::YLH6_REPORT_FILE w]; puts $fh $msg; close $fh }
        ylh6_ckpt "TRIA_SKIN_FINISH" "status=$status tria=$n_tria free=$free_e tedge=$t_e"
        return [dict create status $status msg $msg]
    }

    if {$n_tria == 0 || $free_e > 0 || $t_e > 0} {
        set m "YL_HEX_V6 TETRA (STRICT 2.0 mm) stopped before tetmesh  \[CHECK\]\n\n- Tria shell: $shell_txt\n\nTetmesh needs a CLOSED tria shell (free edges = 0, T-edges = 0).\nThe shell is kept so you can inspect it (Tool > edges > free edges).\nFix the gaps (Geom > autocleanup / edge edit > equivalence) and run again."
        if {$mesh_err ne ""} { append m "\n\n*** $mesh_err" }
        return [dict create status CHECK msg $m]
    }

    # 6. Tetmesh
    ylh6_ckpt "TET_TETMESH_EXEC" "shell tria=$n_tria"
    lassign [ylh6_tetmesh_exec] tm_ok tm_txt
    set n_tet [ylh6_count_elems {tetra4 tetra10}]
    ylh6_ckpt "TET_TETMESH_DONE" "ok=$tm_ok tetra=$n_tet ($tm_txt)"
    if {$n_tet == 0} {
        return [dict create status FAIL msg "YL_HEX_V6 TETRA (STRICT 2.0 mm)  \[FAIL\]\n\n- Tria shell: $shell_txt\n- tetmesh created 0 tetra:\n    $tm_txt\n\nPaste the *tetmesh line from your working tetra script into proc ylh6_tetmesh_exec."]
    }
    if {$DELETE_2D} { ylh6_delete_elems {tria3 tria6 quad4 quad8} }

    # 7. Abaqus tetra order / type (C3D10* = 2nd order: tetra4 -> tetra10, midside nodes on geometry)
    set tpl_path "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d"
    set assigned_type ""; set type_err ""
    catch { *templatefileset $tpl_path }
    set tp [ylh6_lookup_abaqus_type $TET_TYPE $tpl_path]
    if {[llength $tp] == 2} {
        lassign $tp tcfg ttyp
        set is2 [expr {$tcfg == 210}]
        if {$is2} {
            *clearmark elems 1; *createmark elems 1 "by config" tetra4
            if {[llength [hm_getmark elems 1]] > 0} { catch { *orderchangetosecond 1 1 } }
            *clearmark elems 1
            if {[ylh6_count_elems tetra10] == 0} { set type_err "2nd-order (tetra10) conversion failed; elements remain tetra4" }
        }
        *clearmark elems 1; *createmark elems 1 "by config" [expr {$is2 ? "tetra10" : "tetra4"}]
        set e0 [lindex [hm_getmark elems 1] 0]
        if {$e0 ne ""} {
            catch { *elementtype $tcfg $ttyp }; catch { *elementsettypes 1 }
            catch { set assigned_type [hm_getvalue elems id=$e0 dataname=typename] }
        }
        *clearmark elems 1
        if {$type_err eq "" && $assigned_type ne "" && $assigned_type ne $TET_TYPE} {
            set type_err "Element type mismatch: actual='$assigned_type', wanted='$TET_TYPE'"
        }
    } else {
        set type_err "'$TET_TYPE' not found in the Abaqus template - type not assigned"
    }
    set n_tet4  [ylh6_count_elems tetra4]
    set n_tet10 [ylh6_count_elems tetra10]

    # 8. Tetra quality: HM checks + independent metrics
    set hm_asp -1; set hm_jac -1; set hm_col -1
    foreach {var cmd lim lbl} [list \
        hm_asp *elementtestaspect        $TARGET_ASPECT "3D Aspect" \
        hm_jac *elementtestjacobian      $MIN_JACOBIAN  "3D Jacobian" \
        hm_col *elementtesttetracollapse $TET_COLLAPSE  "3D Tet Collapse"] {
        *clearmark elems 1; *clearmark elems 2
        catch { *createmark elems 1 "by config" tetra4 tetra10 }
        if {![catch { $cmd elems 1 $lim 2 2 1 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
        *clearmark elems 1; *clearmark elems 2
    }
    set T [ylh6_measure_tet $TET_COLLAPSE $TET_VSKEW]

    # 9. Save / export
    set saved_hm "(not saved)"; set saved_inp "(not exported)"
    set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_V6_TETRA_${TET_TYPE}_2mm"}]
    if {$SAVE_HM && $OUT_DIR ne ""} {
        file mkdir $OUT_DIR
        set out_hm [file join $OUT_DIR "${stem}.hm"]
        catch { hm_answernext yes }
        if {![catch { *writefile $out_hm 1 }]} { set saved_hm $out_hm }
    }
    if {$EXPORT_INP && $OUT_DIR ne "" && $type_err eq "" && [dict get $T inverted] == 0} {
        file mkdir $OUT_DIR
        set out_inp [file join $OUT_DIR "${stem}.inp"]
        catch { file delete $out_inp }; catch { hm_answernext yes }
        if {![catch { *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"; *feoutputwithdata $tpl_path $out_inp 0 0 2 1 2 }] && [file exists $out_inp]} {
            set saved_inp $out_inp
        }
    }

    # 10. Report
    set fmt_hm {x { expr {$x < 0 ? "n/a" : $x} }}
    set pass [expr {$mesh_err eq "" && $type_err eq "" && $n_tet > 0 && [dict get $T inverted] == 0 \
        && [dict get $T col_bad] == 0 && [dict get $T vsk_bad] == 0 && $hm_asp <= 0 && $hm_jac <= 0 ? 1 : 0}]
    set status [expr {$pass ? "OK" : "CHECK"}]
    set msg [format "YL_HEX_V6 TETRA (STRICT 2.0 mm) Finished  \[%s\]\n\n- Element: Tetra, Abaqus type %s (actual: %s)  -> tetra4=%d, tetra10=%d\n- Tria shell: %s\n- tetmesh: %s\n- MEASURED tet edge length: mean=%.2f mm (p05=%.2f, p95=%.2f)\n- Tetra quality:\n    Tet collapse (>= %.2f): min=%.3f, mean=%.3f, failed=%d  | HM check: %s\n    Vol skew     (<= %.2f): max=%.3f, failed=%d\n    HM Aspect    (<= %.1f): failed=%s\n    HM Jacobian  (>= %.2f): failed=%s  (only meaningful for 2nd-order C3D10*)\n    Inverted / degenerate tets: %d\n- Saved HM: %s\n- Exported INP: %s\n- Checkpoint Log: %s" \
        $status $TET_TYPE $assigned_type $n_tet4 $n_tet10 $shell_txt $tm_txt \
        [dict get $T lmean] [dict get $T lp05] [dict get $T lp95] \
        $TET_COLLAPSE [dict get $T col_min] [dict get $T col_mean] [dict get $T col_bad] [apply $fmt_hm $hm_col] \
        $TET_VSKEW [dict get $T vsk_max] [dict get $T vsk_bad] \
        $TARGET_ASPECT [apply $fmt_hm $hm_asp] $MIN_JACOBIAN [apply $fmt_hm $hm_jac] \
        [dict get $T inverted] $saved_hm $saved_inp $::YLH6_CKPT_FILE]
    if {$mesh_err ne ""} { append msg "\n\n*** $mesh_err" }
    if {$type_err ne ""} { append msg "\n*** $type_err" }
    catch { set fh [open $::YLH6_REPORT_FILE w]; puts $fh $msg; close $fh }
    ylh6_ckpt "TET_FINISH" "status=$status tetra=$n_tet"
    return [dict create status $status msg $msg]
}

# =====================================================================
# MODULE 5 : GUI (Fresh ::ylh6_cfg)
# =====================================================================
# Element + dimension -> engine:  hex+2d=quad  hex+3d=hex  tet+2d=tria  tet+3d=tet
proc ylh6_gui_mode {} {
    set fam [expr {[info exists ::ylh6_cfg(family)] ? $::ylh6_cfg(family) : "hex"}]
    set dim [expr {[info exists ::ylh6_cfg(dim)]    ? $::ylh6_cfg(dim)    : "2d"}]
    if {$fam eq "tet"} { return [expr {$dim eq "3d" ? "tet" : "tria"}] }
    return [expr {$dim eq "3d" ? "hex" : "quad"}]
}

# Show what will run and grey out the fields that this combination does not use
proc ylh6_gui_mode_update {args} {
    set w .ylhex6
    if {![winfo exists $w]} { return }
    set m [ylh6_gui_mode]
    set ::ylh6_cfg(mode) $m
    set desc [dict get {
        quad "2D Quad skin   (surface mesh for HEXA preparation, quads only)"
        hex  "3D Hexa volume   (Multi-Solid Map - needs mappable solids)"
        tria "2D Tria skin   (closed surface mesh for TETRA preparation)"
        tet  "3D Tetra volume   (tria shell -> tetmesh - no solid needed)"} $m]
    catch { $w.f1.run configure -text $desc }
    # default output name follows the mode (a name typed by the user is kept)
    set dflt [dict get {quad model_QUADSKIN_2mm hex model_PUREHEX_2mm tria model_TRIASKIN_2mm tet model_TETRA_2mm} $m]
    if {[lsearch -exact {"" model_PUREHEX_2mm model_QUADSKIN_2mm model_TRIASKIN_2mm model_TETRA_2mm} $::ylh6_cfg(out_name)] >= 0} {
        set ::ylh6_cfg(out_name) $dflt
    }
    # widget -> modes in which it is used
    foreach {wid used} [list \
        $w.f2.esl {quad tria tet}  $w.f2.el1 {quad}  $w.f2.eas {quad hex tria tet} \
        $w.f2.ejc {quad hex tet}   $w.f2.ewp {quad hex}  $w.f2.efa {quad tria tet} \
        $w.f2.esm {quad hex tria tet}  $w.f2.oet {hex}  $w.f2.etc {tet}  $w.f2.evs {tet}  $w.f2.ott {tet} \
        $w.f3.c1 {quad tria tet}  $w.f3.c2 {quad}  $w.f3.c3 {quad hex tria tet}  $w.f3.c4 {quad}  $w.f3.c5 {quad tria tet}] {
        catch { $wid configure -state [expr {[lsearch -exact $used $m] >= 0 ? "normal" : "disabled"}] }
    }
}

proc ylh6_launch_gui {} {
    if {![info exists ::ylh6_cfg(family)] && [info exists ::ylh6_cfg(mode)]} {
        switch -- $::ylh6_cfg(mode) {
            hex     { set ::ylh6_cfg(family) hex; set ::ylh6_cfg(dim) 3d }
            tet     { set ::ylh6_cfg(family) tet; set ::ylh6_cfg(dim) 3d }
            tria    { set ::ylh6_cfg(family) tet; set ::ylh6_cfg(dim) 2d }
            default { set ::ylh6_cfg(family) hex; set ::ylh6_cfg(dim) 2d }
        }
    }
    foreach {k v} [ylh6_default_params] {
        if {![info exists ::ylh6_cfg($k)]} { set ::ylh6_cfg($k) $v }
    }
    set ::ylh6_cfg(elem_size) "2.0"

    catch { destroy .ylhex6 }
    set w [toplevel .ylhex6]
    wm title $w "YL Mesh V6 PURE (STRICT 2.0 mm - Hexa / Tetra, 2D / 3D)"
    wm resizable $w 0 0

    set f1 [labelframe $w.f1 -text " 1. Element & Dimension (STRICT 2.0 mm) " -padx 8 -pady 6]
    pack $f1 -fill x -padx 10 -pady 5
    label $f1.lfam -text "Element:"
    radiobutton $f1.fh -text "Hexa   (quad skin / hex volume)"  -variable ::ylh6_cfg(family) -value "hex"
    radiobutton $f1.ft -text "Tetra   (tria skin / tet volume)" -variable ::ylh6_cfg(family) -value "tet"
    label $f1.ldim -text "Dimension:"
    radiobutton $f1.d2 -text "2D   (surface skin only)" -variable ::ylh6_cfg(dim) -value "2d"
    radiobutton $f1.d3 -text "3D   (volume mesh)"       -variable ::ylh6_cfg(dim) -value "3d"
    label $f1.lrun -text "Will run:"
    label $f1.run -text "" -foreground "#1a4f9c" -anchor w
    label $f1.ltg -text "Target:"
    radiobutton $f1.s1 -text "All Solids / Surfaces" -variable ::ylh6_cfg(select_mode) -value "all"
    radiobutton $f1.s2 -text "Pick Manually"         -variable ::ylh6_cfg(select_mode) -value "pick"
    grid $f1.lfam $f1.fh $f1.ft -sticky w -padx 4 -pady 2
    grid $f1.ldim $f1.d2 $f1.d3 -sticky w -padx 4 -pady 2
    grid $f1.lrun $f1.run -     -sticky w -padx 4 -pady 2
    grid $f1.ltg  $f1.s1 $f1.s2 -sticky w -padx 4 -pady 2

    set f2 [labelframe $w.f2 -text " 2. Sliver & Quality Criteria (greyed = not used in this mode) " -padx 8 -pady 6]
    pack $f2 -fill x -padx 10 -pady 5

    label $f2.lsz -text "Elem Size (mm):"
    entry $f2.esz -textvariable ::ylh6_cfg(elem_size) -width 8 -state disabled
    label $f2.lsl -text "Sliver Edge Max:"
    entry $f2.esl -textvariable ::ylh6_cfg(sliver_edge_max) -width 8
    label $f2.ll1 -text "Local Remesh mm:"
    entry $f2.el1 -textvariable ::ylh6_cfg(local_size_1) -width 8

    label $f2.las -text "Aspect Limit:"
    entry $f2.eas -textvariable ::ylh6_cfg(target_aspect) -width 8
    label $f2.ljc -text "Min Jacobian:"
    entry $f2.ejc -textvariable ::ylh6_cfg(min_jac) -width 8
    label $f2.lwp -text "Max Warpage:"
    entry $f2.ewp -textvariable ::ylh6_cfg(max_warp) -width 8

    label $f2.lfa -text "Feature Angle:"
    entry $f2.efa -textvariable ::ylh6_cfg(feature_angle) -width 8
    label $f2.lsm -text "Relax Passes:"
    entry $f2.esm -textvariable ::ylh6_cfg(smooth_passes) -width 8
    label $f2.let -text "Abaqus 3D:"
    eval tk_optionMenu $f2.oet ::ylh6_cfg(elem_type) C3D8H C3D8RH C3D8 C3D8R C3D8I C3D20H C3D20RH C3D20 C3D20R

    grid $f2.lsz $f2.esz $f2.lsl $f2.esl $f2.ll1 $f2.el1 -sticky w -padx 4 -pady 3
    grid $f2.las $f2.eas $f2.ljc $f2.ejc $f2.lwp $f2.ewp -sticky w -padx 4 -pady 3
    grid $f2.lfa $f2.efa $f2.lsm $f2.esm $f2.let $f2.oet -sticky w -padx 4 -pady 3

    label $f2.ltc -text "Tet Collapse Min:"
    entry $f2.etc -textvariable ::ylh6_cfg(tet_collapse_min) -width 8
    label $f2.lvs -text "Vol Skew Max:"
    entry $f2.evs -textvariable ::ylh6_cfg(tet_vskew_max) -width 8
    label $f2.ltt -text "Abaqus Tetra:"
    eval tk_optionMenu $f2.ott ::ylh6_cfg(tet_elem_type) C3D10MH C3D10H C3D10M C3D10 C3D4H C3D4
    grid $f2.ltc $f2.etc $f2.lvs $f2.evs $f2.ltt $f2.ott -sticky w -padx 4 -pady 3

    set f3 [labelframe $w.f3 -text " 3. Mesh Repair (greyed = not used in this mode) " -padx 8 -pady 6]
    pack $f3 -fill x -padx 10 -pady 5
    checkbutton $f3.c1 -text "Zipper-collapse V-tip sliver strips (fixes grey tapering V-wedges)" -variable ::ylh6_cfg(do_zipper)
    checkbutton $f3.c2 -text "Merge adjacent Tria3 pairs into Quad4 (quad skin only)"                -variable ::ylh6_cfg(do_tria_merge)
    checkbutton $f3.c3 -text "Quality-driven node relaxation (2D) / interior hex smoothing (3D Hexa)" -variable ::ylh6_cfg(do_smooth)
    checkbutton $f3.c4 -text "Local pure-quad remesh on bad-aspect surfaces (quad skin only)"        -variable ::ylh6_cfg(do_local_remesh)
    checkbutton $f3.c5 -text "Auto-patch unmeshed surfaces (finer size, same element type)"          -variable ::ylh6_cfg(patch_holes)
    grid $f3.c1 -row 0 -column 0 -sticky w -padx 4 -pady 1
    grid $f3.c2 -row 1 -column 0 -sticky w -padx 4 -pady 1
    grid $f3.c3 -row 2 -column 0 -sticky w -padx 4 -pady 1
    grid $f3.c4 -row 3 -column 0 -sticky w -padx 4 -pady 1
    grid $f3.c5 -row 4 -column 0 -sticky w -padx 4 -pady 1

    set f4 [labelframe $w.f4 -text " 4. Optional Save / Export " -padx 8 -pady 6]
    pack $f4 -fill x -padx 10 -pady 5
    checkbutton $f4.cs -text "Save .hm"    -variable ::ylh6_cfg(save_hm)
    checkbutton $f4.ci -text "Export .inp" -variable ::ylh6_cfg(export_inp)
    grid $f4.cs -row 0 -column 0 -sticky w -padx 4
    grid $f4.ci -row 0 -column 1 -sticky w -padx 12

    label $f4.ldir -text "Output Folder:"
    entry $f4.edir -textvariable ::ylh6_cfg(out_dir) -width 40
    button $f4.bdir -text "Browse..." -command {
        set d [tk_chooseDirectory -initialdir $::ylh6_cfg(out_dir) -title "Select Output Folder"]
        if {$d ne ""} { set ::ylh6_cfg(out_dir) $d }
    }
    label $f4.lnm -text "Output Name:"
    entry $f4.enm -textvariable ::ylh6_cfg(out_name) -width 26
    grid $f4.ldir $f4.edir $f4.bdir -sticky w -padx 4 -pady 3
    grid $f4.lnm  $f4.enm  -sticky w -padx 4 -pady 3

    set fb [frame $w.fb]
    pack $fb -fill x -padx 10 -pady 10
    set ::ylh6_gui_action "cancel"
    button $fb.run -text "Run V6 PURE (2.0 mm)" -width 20 -command { set ::ylh6_gui_action "run"; destroy .ylhex6 }
    button $fb.can -text "Cancel"               -width 12 -command { set ::ylh6_gui_action "cancel"; destroy .ylhex6 }
    pack $fb.can -side right -padx 4
    pack $fb.run -side right -padx 4

    wm protocol $w WM_DELETE_WINDOW { set ::ylh6_gui_action "cancel"; destroy .ylhex6 }
    trace add variable ::ylh6_cfg(family) write ylh6_gui_mode_update
    trace add variable ::ylh6_cfg(dim)    write ylh6_gui_mode_update
    ylh6_gui_mode_update
    tkwait window $w
    trace remove variable ::ylh6_cfg(family) write ylh6_gui_mode_update
    trace remove variable ::ylh6_cfg(dim)    write ylh6_gui_mode_update

    if {$::ylh6_gui_action ne "run"} { return }

    foreach {k lbl} {sliver_edge_max "Sliver Edge Max" local_size_1 "Local Remesh" target_aspect "Aspect Limit" min_jac "Min Jacobian" max_warp "Max Warpage" feature_angle "Feature Angle" smooth_passes "Relax Passes" tet_collapse_min "Tet Collapse Min" tet_vskew_max "Vol Skew Max"} {
        if {![string is double -strict $::ylh6_cfg($k)]} {
            tk_messageBox -icon error -title "YL HEX V6" -message "Invalid number for $lbl: '$::ylh6_cfg($k)'"
            return
        }
    }

    set ::ylh6_cfg(mode) [ylh6_gui_mode]
    set cfg_dict [array get ::ylh6_cfg]
    switch -- $::ylh6_cfg(mode) {
        hex     { set res [ylh6_run_3d $cfg_dict] }
        tet     { set res [ylh6_run_tet $cfg_dict] }
        tria    { dict set cfg_dict shell_only 1; set res [ylh6_run_tet $cfg_dict] }
        default { set res [ylh6_run_2d $cfg_dict] }
    }

    set icon [expr {[dict get $res status] eq "OK" ? "info" : "warning"}]
    tk_messageBox -icon $icon -title "YL HEX V6 Result ([dict get $res status])" -message [dict get $res msg]
}

ylh6_launch_gui
