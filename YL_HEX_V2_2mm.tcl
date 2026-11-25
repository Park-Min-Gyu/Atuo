# =====================================================================
# YL_HEX_V2_2mm.tcl : HyperMesh 2019 Standalone Hexa / Quad Script
#   - Element size FIXED to 2.0 mm
#   - Pure-Quad priority (minimizes Tria/Penta mixing)
#   - Curved/Fillet refinement + Automatic unmeshed-hole patching
#   - Modular tunable functions for direct user editing
#   - Narrow CAD face defeature before SolidMap (removes thin hex layers / aspect 100+)
#   - Reports the ACTUAL hex edge length so the 2.0 mm size can be verified
#
#   Run inside HyperMesh 2019:
#     File -> Run -> Tcl/Tk Script -> Select YL_HEX_V2_2mm.tcl
# =====================================================================

# =====================================================================
# [USER TUNABLE PARAMETERS] - Edit values here to test directly!
# =====================================================================
# Element size is fixed HERE ONLY - every module reads this one value.
set ::YLH_ELEM_SIZE 2.0

proc ylh_default_params {} {
    return [list \
        elem_size      $::YLH_ELEM_SIZE \
        defeature      1       \
        defeature_width 1.0    \
        quad_mode      "quads" \
        feature_angle  20.0    \
        min_len_factor 0.20    \
        max_len_factor 1.60    \
        fillet_rows    "dense" \
        min_jac        0.50    \
        max_warp       15.0    \
        aspect         5.0     \
        max_skew       60.0    \
        smooth_passes  6       \
        elem_type      "C3D8H" \
        sm_flags       "auto"  \
        delete_2d      1       \
        equiv_global   0       \
        quad_fallback  1       \
        patch_holes    1       \
        mode           "hex"   \
        select_mode    "all"   \
        save_hm        0       \
        export_inp     0       \
        out_dir        "C:/work/mesh_hex" \
        out_name       "model_HEX_2mm" \
    ]
}

# =====================================================================
# MODULE 1 : Math & Quality Smoothers (hqc::*)
#   1) hqc::smooth_hex      : 3D Hex8 interior node Jacobian smoother
#   2) hqc::smooth_2d_quad  : 2D Quad4 interior node CAD-snapped smoother
# =====================================================================
namespace eval hqc {
    variable X; variable H; variable NH; variable BND
}

proc hqc::sub {a b} {
    list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}]
}
proc hqc::dot {a b} {
    expr {[lindex $a 0]*[lindex $b 0] + [lindex $a 1]*[lindex $b 1] + [lindex $a 2]*[lindex $b 2]}
}
proc hqc::cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc hqc::len {a} { expr {sqrt([dot $a $a])} }
proc hqc::unit {a} {
    set l [len $a]
    if {$l < 1e-14} { return {0.0 0.0 0.0} }
    list [expr {[lindex $a 0]/$l}] [expr {[lindex $a 1]/$l}] [expr {[lindex $a 2]/$l}]
}

# --- 3D Hex8 corner Scaled Jacobian (ideal cube = 1.0, inverted <= 0.0) ---
proc hqc::hex_min_jac {pts} {
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
        set det [dot [cross $e1 $e2] $e3]
        set sj  [expr {$det / ($l1 * $l2 * $l3)}]
        if {$sj < $jmin} { set jmin $sj }
    }
    return $jmin
}

# --- 2D Quad4 corner Scaled Jacobian (ideal rectangle = 1.0, concave <= 0.0) ---
proc hqc::quad_min_jac {pts {ref_nrm ""}} {
    lassign $pts p0 p1 p2 p3
    if {$ref_nrm eq ""} {
        set ref_nrm [unit [cross [sub $p2 $p0] [sub $p3 $p1]]]
    }
    if {[len $ref_nrm] < 1e-12} { return -1.0 }
    set corners [list \
        [list $p0 $p1 $p3] [list $p1 $p2 $p0] [list $p2 $p3 $p1] [list $p3 $p0 $p2]]
    set jmin 1e9
    foreach c $corners {
        lassign $c o a b
        set e1 [sub $a $o]; set e2 [sub $b $o]
        set l1 [len $e1]; set l2 [len $e2]
        if {$l1 < 1e-12 || $l2 < 1e-12} { return -1.0 }
        set sj [expr {[dot [cross $e1 $e2] $ref_nrm] / ($l1 * $l2)}]
        if {$sj < $jmin} { set jmin $sj }
    }
    return $jmin
}

proc hqc::elem_jac {e} {
    variable X; variable H
    set pts {}
    foreach n $H($e) { lappend pts $X($n) }
    return [hex_min_jac $pts]
}

# --- [TUNABLE FUNCTION 1] 3D Hex8 Interior Node Smoother ---
proc hqc::smooth_hex {min_jac {passes 6}} {
    variable X; variable H; variable NH; variable BND
    array unset X; array unset H; array unset NH; array unset BND

    *clearmark elems 1
    *createmark elems 1 "by config" hex8
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} { return 0 }

    array set face_cnt {}
    set face_idx {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}
    foreach e $eids {
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] != 8} { continue }
        set H($e) $nds
        foreach n $nds {
            lappend NH($n) $e
            if {![info exists X($n)]} { set X($n) [lindex [hm_nodevalue $n] 0] }
        }
        foreach fi $face_idx {
            set fnds {}
            foreach idx $fi { lappend fnds [lindex $nds $idx] }
            set key [join [lsort -integer $fnds] ","]
            if {[info exists face_cnt($key)]} { incr face_cnt($key) } else { set face_cnt($key) 1 }
        }
    }

    # Lock exterior surface nodes to preserve CAD geometry
    foreach k [array names face_cnt] {
        if {$face_cnt($k) == 1} {
            foreach n [split $k ","] { set BND($n) 1 }
        }
    }
    *clearmark elems 1
    catch { *createmark elems 1 "by config" penta6 pyramid5 tetra4 }
    foreach e [hm_getmark elems 1] {
        foreach n [hm_nodelist $e] { set BND($n) 1 }
    }
    *clearmark elems 1

    array set J {}
    foreach e [array names H] { set J($e) [elem_jac $e] }

    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        array unset cand; array set cand {}
        foreach e [array names J] {
            if {$J($e) < $min_jac} {
                foreach n $H($e) { if {![info exists BND($n)]} { set cand($n) 1 } }
            }
        }
        if {[array size cand] == 0} { break }
        set changed 0
        foreach n [array names cand] {
            set els $NH($n)
            array unset nbrs
            foreach e $els { foreach m $H($e) { if {$m != $n} { set nbrs($m) 1 } } }
            set nb [array names nbrs]
            if {[llength $nb] < 4} { continue }
            set cx 0.0; set cy 0.0; set cz 0.0
            foreach m $nb {
                lassign $X($m) x y z
                set cx [expr {$cx + $x}]; set cy [expr {$cy + $y}]; set cz [expr {$cz + $z}]
            }
            set k [expr {double([llength $nb])}]
            set cx [expr {$cx/$k}]; set cy [expr {$cy/$k}]; set cz [expr {$cz/$k}]
            set old $X($n)
            lassign $old ox oy oz

            set j_before 1e9
            foreach e $els { if {$J($e) < $j_before} { set j_before $J($e) } }

            set best_j $j_before; set best_pos ""; set best_js {}
            # Step-size relaxation: tries 0.5, 1.0, 0.25 of Laplacian vector
            foreach a {0.5 1.0 0.25} {
                set X($n) [list [expr {$ox + $a*($cx-$ox)}] [expr {$oy + $a*($cy-$oy)}] [expr {$oz + $a*($cz-$oz)}]]
                set jmin 1e9; set js {}
                foreach e $els {
                    set j [elem_jac $e]
                    lappend js $e $j
                    if {$j < $jmin} { set jmin $j }
                }
                if {$jmin > $best_j + 1e-4} {
                    set best_j $jmin; set best_pos $X($n); set best_js $js
                }
            }
            if {$best_pos ne ""} {
                set X($n) $best_pos
                foreach {e j} $best_js { set J($e) $j }
                set moved($n) 1
                incr changed
            } else {
                set X($n) $old
            }
        }
        if {$changed == 0} { break }
    }

    set nm 0
    foreach n [array names moved] {
        lassign $X($n) x y z
        if {![catch { *nodemodify $n $x $y $z }]} { incr nm }
    }
    return $nm
}

# --- [TUNABLE FUNCTION 2] 2D Quad4 Interior Smoother (Snaps back to CAD surface) ---
proc hqc::smooth_2d_quad {elem_size feature_deg {passes 6}} {
    *clearmark elems 1
    *createmark elems 1 "by config" quad4
    set eids [hm_getmark elems 1]
    *clearmark elems 1
    if {[llength $eids] == 0} { return 0 }

    # Map elem -> CAD surface for exact geometry snapping
    array set SURF {}
    *clearmark surfs 1; *createmark surfs 1 "all"
    set all_surfs [hm_getmark surfs 1]
    *clearmark surfs 1
    foreach s $all_surfs {
        *clearmark surfs 1; *clearmark elems 2
        *createmark surfs 1 $s
        if {![catch { *findmark surfs 1 257 1 elems 0 2 }]} {
            foreach e [hm_getmark elems 2] { set SURF($e) $s }
        }
    }
    *clearmark surfs 1; *clearmark elems 2

    array set QX {}; array set QE {}; array set QNE {}; array set QNRM {}; array set EDGE {}; array set LOCK {}
    foreach e $eids {
        set nds [lrange [hm_nodelist $e] 0 3]
        if {[llength $nds] != 4} { continue }
        set QE($e) $nds
        foreach n $nds {
            lappend QNE($n) $e
            if {![info exists QX($n)]} { set QX($n) [lindex [hm_nodevalue $n] 0] }
        }
        lassign $nds n0 n1 n2 n3
        set QNRM($e) [unit [cross [sub $QX($n2) $QX($n0)] [sub $QX($n3) $QX($n1)]]]
        foreach {u v} [list $n0 $n1 $n1 $n2 $n2 $n3 $n3 $n0] {
            set k [expr {$u < $v ? "$u,$v" : "$v,$u"}]
            lappend EDGE($k) $e
        }
    }
    # Lock nodes attached to trias (don't distort mixed boundaries)
    *clearmark elems 1
    catch { *createmark elems 1 "by config" tria3 }
    foreach e [hm_getmark elems 1] {
        foreach n [hm_nodelist $e] { set LOCK($n) 1 }
    }
    *clearmark elems 1

    # Lock boundary / feature edges / inter-surface CAD edges
    set cosf [expr {cos($feature_deg * acos(-1.0) / 180.0)}]
    foreach k [array names EDGE] {
        set el $EDGE($k)
        set is_feat 0
        if {[llength $el] != 2} {
            set is_feat 1
        } else {
            lassign $el e1 e2
            if {[dot $QNRM($e1) $QNRM($e2)] < $cosf} { set is_feat 1 }
            if {[info exists SURF($e1)] && [info exists SURF($e2)] && $SURF($e1) ne $SURF($e2)} { set is_feat 1 }
        }
        if {$is_feat} {
            lassign [split $k ","] u v
            set LOCK($u) 1; set LOCK($v) 1
        }
    }

    array set moved {}
    for {set p 0} {$p < $passes} {incr p} {
        set changed 0
        foreach n [array names QNE] {
            if {[info exists LOCK($n)]} { continue }
            set els $QNE($n)
            if {[llength $els] < 3} { continue }
            array unset nbrs
            foreach e $els {
                set nds $QE($e)
                set idx [lsearch -exact $nds $n]
                set nb1 [lindex $nds [expr {($idx + 1) % 4}]]
                set nb2 [lindex $nds [expr {($idx + 3) % 4}]]
                set nbrs($nb1) 1; set nbrs($nb2) 1
            }
            set nb [array names nbrs]
            if {[llength $nb] < 3} { continue }
            set cx 0.0; set cy 0.0; set cz 0.0
            foreach m $nb {
                lassign $QX($m) x y z
                set cx [expr {$cx + $x}]; set cy [expr {$cy + $y}]; set cz [expr {$cz + $z}]
            }
            set k [expr {double([llength $nb])}]
            set old $QX($n)
            lassign $old ox oy oz
            set trial [list [expr {0.5*($ox + $cx/$k)}] [expr {0.5*($oy + $cy/$k)}] [expr {0.5*($oz + $cz/$k)}]]

            # Snap trial point back to CAD surface
            set s [expr {[info exists SURF([lindex $els 0])] ? $SURF([lindex $els 0]) : ""}]
            if {$s ne ""} {
                lassign $trial tx ty tz
                if {![catch { hm_findclosestpointonsurface $tx $ty $tz $s } cp] && [llength $cp] >= 3} {
                    set q [lrange $cp 0 2]
                    if {[len [sub $q $trial]] < 0.35 * $elem_size} { set trial $q }
                }
            }
            if {[len [sub $trial $old]] < 1e-4 * $elem_size} { continue }

            set j0 1e9; set sum0 0.0
            foreach e $els {
                set pts {}
                foreach m $QE($e) { lappend pts $QX($m) }
                set j [quad_min_jac $pts $QNRM($e)]
                if {$j < $j0} { set j0 $j }
                set sum0 [expr {$sum0 + $j}]
            }
            set QX($n) $trial
            set j1 1e9; set sum1 0.0
            foreach e $els {
                set pts {}
                foreach m $QE($e) { lappend pts $QX($m) }
                set j [quad_min_jac $pts $QNRM($e)]
                if {$j < $j1} { set j1 $j }
                set sum1 [expr {$sum1 + $j}]
            }
            if {$j1 > 0.20 && ($j1 > $j0 + 1e-4 || ($j1 >= $j0 - 1e-5 && $sum1 > $sum0 + 1e-4))} {
                set moved($n) 1
                incr changed
            } else {
                set QX($n) $old
            }
        }
        if {$changed == 0} { break }
    }

    set nm 0
    foreach n [array names moved] {
        lassign $QX($n) x y z
        if {![catch { *nodemodify $n $x $y $z }]} { incr nm }
    }
    return $nm
}

# =====================================================================
# MODULE 2 : Independent 3D Hex8 Statistics (yhs_*)
# =====================================================================
proc yhs_sub {a b} { list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}] }
proc yhs_dot {a b} { expr {[lindex $a 0]*[lindex $b 0]+[lindex $a 1]*[lindex $b 1]+[lindex $a 2]*[lindex $b 2]} }
proc yhs_cross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc yhs_len {a} { expr {sqrt([yhs_dot $a $a])} }
proc yhs_xyz {nd} {
    if {![info exists ::YHX($nd)]} { set ::YHX($nd) [lindex [hm_nodevalue $nd] 0] }
    return $::YHX($nd)
}
proc yhs_quant {sorted p} {
    set n [llength $sorted]
    if {$n == 0} { return 0.0 }
    return [lindex $sorted [expr {int(floor($p * ($n - 1)))}]]
}

proc yhs_quad_warp {p0 p1 p2 p3} {
    set rad [expr {180.0 / acos(-1.0)}]
    set w 0.0
    foreach {a b c d} [list $p0 $p1 $p2 $p3  $p1 $p2 $p3 $p0] {
        set n1 [yhs_cross [yhs_sub $b $a] [yhs_sub $c $a]]
        set n2 [yhs_cross [yhs_sub $c $a] [yhs_sub $d $a]]
        set l1 [yhs_len $n1]; set l2 [yhs_len $n2]
        if {$l1 < 1e-14 || $l2 < 1e-14} { continue }
        set cang [expr {max(-1.0, min(1.0, [yhs_dot $n1 $n2] / ($l1 * $l2)))}]
        set ang [expr {acos($cang) * $rad}]
        if {$ang > $w} { set w $ang }
    }
    return $w
}

proc yhs_hex_stats {elist {maxn 300000}} {
    set R [dict create n [llength $elist] neg_jac 0]
    if {[llength $elist] == 0} { return $R }
    set step 1
    if {[llength $elist] > $maxn} { set step [expr {int(ceil(double([llength $elist]) / $maxn))}] }

    set jacs {}; set warps {}; set asps {}; set lmeans {}
    set neg 0; set n 0; set i 0; set nsliver 0
    set sliver_len [expr {0.25 * $::YLH_ELEM_SIZE}]
    set edges {{0 1} {1 2} {2 3} {3 0} {4 5} {5 6} {6 7} {7 4} {0 4} {1 5} {2 6} {3 7}}
    set faces {{0 1 2 3} {4 5 6 7} {0 1 5 4} {1 2 6 5} {2 3 7 6} {3 0 4 7}}

    foreach e $elist {
        if {$i % $step} { incr i; continue }
        incr i
        set nds [lrange [hm_nodelist $e] 0 7]
        if {[llength $nds] < 8} { continue }
        set P {}
        foreach nd $nds { lappend P [yhs_xyz $nd] }
        set j [hqc::hex_min_jac $P]
        if {$j <= 0.0} { incr neg }
        set wmax 0.0
        foreach f $faces {
            set w [yhs_quad_warp [lindex $P [lindex $f 0]] [lindex $P [lindex $f 1]] [lindex $P [lindex $f 2]] [lindex $P [lindex $f 3]]]
            if {$w > $wmax} { set wmax $w }
        }
        set lmin 1e30; set lmax 0.0; set lsum 0.0
        foreach ed $edges {
            set l [yhs_len [yhs_sub [lindex $P [lindex $ed 1]] [lindex $P [lindex $ed 0]]]]
            if {$l < $lmin} { set lmin $l }
            if {$l > $lmax} { set lmax $l }
            set lsum [expr {$lsum + $l}]
        }
        set asp [expr {$lmin > 1e-12 ? $lmax / $lmin : 1e6}]
        lappend jacs $j; lappend warps $wmax; lappend asps $asp
        lappend lmeans [expr {$lsum / 12.0}]
        if {$lmin < $sliver_len} { incr nsliver }
        incr n
    }
    if {$n == 0} { return $R }
    set jacs  [lsort -real $jacs]
    set warps [lsort -real $warps]
    set asps  [lsort -real $asps]
    set sj 0.0; foreach x $jacs { set sj [expr {$sj + $x}] }
    dict set R sampled  $n
    dict set R neg_jac  $neg
    dict set R jac_min  [lindex $jacs 0]
    dict set R jac_mean [expr {$sj / $n}]
    dict set R warp_max [lindex $warps end]
    dict set R asp_p95  [yhs_quant $asps 0.95]
    # actual element size = mean edge length per hex (median over all hexes)
    set lmeans [lsort -real $lmeans]
    dict set R size_med [yhs_quant $lmeans 0.50]
    dict set R size_p05 [yhs_quant $lmeans 0.05]
    dict set R size_p95 [yhs_quant $lmeans 0.95]
    dict set R n_sliver $nsliver
    dict set R sliver_len $sliver_len
    return $R
}

# =====================================================================
# MODULE 3 : Common Utilities
# =====================================================================
proc ylh_is_ascii {s} { return [regexp {^[\x00-\x7F]*$} $s] }

proc ylh_ascii_temp_dir {} {
    set cand {}
    if {[info exists ::env(TEMP)] && $::env(TEMP) ne ""} { lappend cand $::env(TEMP) }
    if {[info exists ::env(TMP)]  && $::env(TMP)  ne ""} { lappend cand $::env(TMP) }
    if {[info exists ::env(PUBLIC)] && $::env(PUBLIC) ne ""} { lappend cand $::env(PUBLIC) }
    lappend cand "C:/Temp" "C:/"
    foreach base $cand {
        set norm [file normalize $base]
        if {[ylh_is_ascii $norm]} {
            set d [file join $norm "YL_HEX_V2_tmp"]
            if {![catch { file mkdir $d }]} { return $d }
        }
    }
    return [pwd]
}

proc ylh_count_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    set n [llength [hm_getmark elems 1]]
    *clearmark elems 1
    return $n
}

proc ylh_delete_elems {cfgs} {
    *clearmark elems 1
    catch { eval *createmark elems 1 {"by config"} $cfgs }
    if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
    *clearmark elems 1
}

proc ylh_solid_3d_count {sid} {
    *clearmark solids 1; *clearmark elems 2
    *createmark solids 1 $sid
    set n 0
    if {![catch { *findmark solids 1 257 1 elems 0 2 }]} { set n [llength [hm_getmark elems 2]] }
    *clearmark solids 1; *clearmark elems 2
    return $n
}

proc ylh_surf_elem_count {sid} {
    *clearmark surfs 1; *clearmark elems 2
    *createmark surfs 1 $sid
    set n 0
    if {![catch { *findmark surfs 1 257 1 elems 0 2 }]} { set n [llength [hm_getmark elems 2]] }
    *clearmark surfs 1; *clearmark elems 2
    return $n
}

proc ylh_find_unmeshed_surfs {surf_ids} {
    set r {}
    foreach s $surf_ids {
        if {![hm_entityinfo exist surfs $s]} { continue }
        set a 0.0
        catch { set a [hm_getareaofsurface surfs $s] }
        if {$a > 1e-6 && [ylh_surf_elem_count $s] == 0} { lappend r $s }
    }
    return $r
}

# --- [TUNABLE FUNCTION 3] Node Equivalence (Per-Component vs Global) ---
proc ylh_equivalence_3d {tol global} {
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

proc ylh_lookup_abaqus_type {ename tpl_path} {
    set ename [string toupper [string trim $ename]]
    if {[file exists $tpl_path]} {
        if {![catch {open $tpl_path r} fh]} {
            set txt [read $fh]
            close $fh
            set pat [format {\*elements\(\s*(\d+)\s*,\s*(\d+)\s*,\s*"?%s"?\s*[,)]} $ename]
            if {[regexp -nocase $pat $txt -> cfg typ]} { return [list $cfg $typ] }
        }
    }
    array set fb {
        C3D8 {208 1} C3D8R {208 2} C3D8I {208 3} C3D8H {208 4} C3D8RH {208 5} C3D8IH {208 6}
        C3D20 {220 1} C3D20R {220 2} C3D20H {220 3} C3D20RH {220 4}
        C3D6 {206 1} C3D6H {206 2}
        C3D15 {215 1} C3D15H {215 2}
    }
    if {[info exists fb($ename)]} { return $fb($ename) }
    return {}
}

proc ylh_wedge_for_hex {hex_type} {
    switch -exact -- $hex_type {
        C3D8    { return "C3D6" }
        C3D8R   { return "C3D6" }
        C3D8I   { return "C3D6" }
        C3D8H   { return "C3D6H" }
        C3D8RH  { return "C3D6H" }
        C3D8IH  { return "C3D6H" }
        C3D20   { return "C3D15" }
        C3D20R  { return "C3D15" }
        C3D20H  { return "C3D15H" }
        C3D20RH { return "C3D15H" }
        default { return "C3D6H" }
    }
}

proc ylh_fmt {d key fmt} {
    if {[dict exists $d $key]} { return [format $fmt [dict get $d $key]] }
    return ""
}

# =====================================================================
# MODULE 4 : YL_H2 (2D Quad-Priority BatchMesher + Hole Patcher)
# =====================================================================

# --- [TUNABLE FUNCTION 4] BatchMesher Criteria & Param Writer ---
# Edit fillet_rows, feature_angle, min_len_factor, tria penalty here!
proc ylh2_write_quad_bm_files {dir stem s feature_angle min_f max_f quad_mode fillet_rows min_jac max_warp max_skew max_asp} {
    set cr [file join $dir "${stem}.criteria"]
    set pr [file join $dir "${stem}.param"]
    # element_type: 1 = quads only, 2 = mixed (quad-dominant)
    set etype_code [expr {$quad_mode eq "quads" ? 1 : 2}]
    set tria_wt    [expr {$quad_mode eq "quads" ? 5.0 : 2.5}]

    set f [open $cr w]
    puts $f "This Criteria file was created with Criteria Editor application\n"
    puts $f " #    Criterion     On  Wt   Ideal    Good    Warn    Fail   Worst  Solver \n"
    puts $f " 0 penalty value               0.00    0.00    1.00    2.00   10.00         "
    puts $f [format " 1 min length         1 2.0 %7.3f %7.3f %7.3f %7.3f %7.3f       2 " $s [expr {0.55*$s}] [expr {0.35*$s}] [expr {$min_f*$s}] [expr {0.7*$min_f*$s}]]
    puts $f [format " 2 max length         1 1.5 %7.3f %7.3f %7.3f %7.3f %7.3f         " $s [expr {1.20*$s}] [expr {1.40*$s}] [expr {$max_f*$s}] [expr {1.25*$max_f*$s}]]
    puts $f [format " 3 aspect ratio       1 1.5    1.00    2.00    3.00 %7.2f %7.2f       0 " $max_asp [expr {2.0*$max_asp}]]
    puts $f [format " 4 warpage            1 3.0    0.00    4.00   10.00 %7.2f %7.2f       0 " $max_warp [expr {2.0*$max_warp}]]
    puts $f " 5 max angle quad     1 2.0   90.00  105.00  120.00  135.00  150.00       0 "
    puts $f " 6 min angle quad     1 2.0   90.00   75.00   60.00   45.00   30.00       0 "
    puts $f " 7 max angle tria     1 1.0   60.00   80.00  110.00  120.00  145.00       0 "
    puts $f " 8 min angle tria     1 1.0   60.00   50.00   35.00   25.00   15.00       0 "
    puts $f [format " 9 skew               1 2.0    0.00   12.00   35.00 %7.2f   75.00       0 " $max_skew]
    puts $f [format "10 jacobian           1 3.5    1.00    0.90    0.70 %7.2f   0.35       0 " $min_jac]
    puts $f [format "11 chordal dev        1 1.5    0.00    0.05    0.15    0.25    0.50         "]
    puts $f [format "12 %% of trias         1 %3.1f    0.00    0.50    1.50    3.00    6.00         " $tria_wt]
    puts $f "13 taper              1 1.0    0.00    0.20    0.45    0.55    0.80       0 "
    close $f

    # Fillet row density table scaled to target size s (2.0 mm -> k = 0.4)
    set k [expr {$s / 5.0}]
    if {$fillet_rows eq "dense"} {
        # Dense curve capture for fillets / rubber stoppers
        set ftab [join [list \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {5*$k}]  [expr {1.0*$k}]  [expr {4.5*$k}] [expr {0.35*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {12*$k}] [expr {4.5*$k}]  [expr {10*$k}]  [expr {0.30*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(4) chordal_deviation(%.4g)" [expr {30*$k}] [expr {10*$k}]   [expr {22*$k}]  [expr {0.25*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do chordal_deviation(%.4g)"          [expr {50*$k}] [expr {22*$k}]   [expr {50*$k}]  [expr {0.20*$k}]]] "\n"]
    } else {
        set ftab [join [list \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(2) chordal_deviation(%.4g)" [expr {10*$k}] [expr {3.0*$k}]  [expr {9*$k}]   [expr {0.8*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do elems(3) chordal_deviation(%.4g)" [expr {30*$k}] [expr {9*$k}]    [expr {20*$k}]  [expr {0.6*$k}]] \
            [format "rad(0,%.4g) wid(%.4g,%.4g) do chordal_deviation(%.4g)"          [expr {50*$k}] [expr {20*$k}]   [expr {50*$k}]  [expr {0.4*$k}]]] "\n"]
    }

    set f [open $pr w]
    puts $f "#This Param file was created with Criteria/Param Editor application
geometry_cleanup_flag             1
meshing_flag                      1
element_size                      $s
element_type                      $etype_code
mesh_align                        3
element_order                     1
surf_component                    1
feature_angle                     $feature_angle
holes_table_begin
appl_surf(0)
appl_solid(0)
appl_cordsfiles(0)
appl_flanged_suppr(0)
flanged_suppr_height(2.0)
narrow_slots_type(0)
abs_fixed_nodes_count_max(-8)
holes_table_end
edge_fillet_recognition           0
max_fillet_radius                 [format %.4g [expr {15*$k}]]
surface_fillet_table_begin
surface_fillet_recognition(1)
minimize_transitions(1)
$ftab
surface_fillet_table_end
del_dupl_surfs_flag               2
del_dupl_surfs_tol                -1
edges_stitch_flags                0
max_edges_stitch_tol              -1.0
fix_overlapsurfs_flag             1
overlapsurfs_maxtangangle         -1.0
narrow_surfs_merge_width          -1.0
narrow_surfs_treat_flags          3
appl_tria_reduction               1
tria_redu_max_elemsize            auto
tria_redu_min_elemsize            auto
trias_around_holes                0
common_mesh_flow                  1
folded_elems_angle                150.0
smooth_elems_target               0.2
fillets_mesh_flow                 1
failed_elems_cleanup_flgs         8
move_nodes_across_feature_edges   1
featureedge_nodes_moveacross_max  [format %.4g [expr {0.03*$s}]]
move_nodes_across_free_edges      0
freeedge_nodes_moveacross_max     0.0
move_normal_flag                  0
divide_warped_quads               0
ignore_comps_boundary             0
gen_topology_prepare_flags        5
cleanup_comp_flag                 0
comp_select
use_wildcards_for_compsnames      0
cleanup_tolerances                auto"
    close $f
    return [list $cr $pr]
}

# --- [TUNABLE FUNCTION 5] Unmeshed Surface (White Hole) Auto-Patcher ---
proc ylh2_patch_unmeshed {surf_ids elem_size quad_mode} {
    set unmeshed [ylh_find_unmeshed_surfs $surf_ids]
    if {[llength $unmeshed] == 0} { return [list 0 0] }
    set n_before [llength $unmeshed]

    # Try 1: quads (type 1) at 0.5x size for narrow/curved missing faces
    set fine_sz [expr {max(0.4, 0.5 * $elem_size)}]
    set et1 [expr {$quad_mode eq "quads" ? 1 : 2}]
    *clearmark surfs 1
    eval *createmark surfs 1 $unmeshed
    catch { *defaultremeshsurf 1 $fine_sz $et1 $et1 2 1 1 1 1 0 0 0 0 }
    *clearmark surfs 1

    # Try 2: still unmeshed -> fallback to mixed (type 2) at 0.35x size
    set still [ylh_find_unmeshed_surfs $unmeshed]
    if {[llength $still] > 0} {
        set finer_sz [expr {max(0.25, 0.35 * $elem_size)}]
        *clearmark surfs 1
        eval *createmark surfs 1 $still
        catch { *defaultremeshsurf 1 $finer_sz 2 2 2 1 1 1 1 0 0 0 0 }
        *clearmark surfs 1
    }
    set remain [llength [ylh_find_unmeshed_surfs $unmeshed]]
    set fixed  [expr {$n_before - $remain}]
    return [list $fixed $remain]
}

proc ylh2_run {cfg_dict} {
    # Element size strictly fixed (::YLH_ELEM_SIZE)
    set ELEM_SIZE      $::YLH_ELEM_SIZE
    set TARGET_ASPECT  [expr {double([dict get $cfg_dict aspect])}]
    set MIN_JACOBIAN   [expr {double([dict get $cfg_dict min_jac])}]
    set MAX_WARPAGE    [expr {double([dict get $cfg_dict max_warp])}]
    set MAX_SKEW       [expr {double([dict get $cfg_dict max_skew])}]
    set QUAD_MODE      [dict get $cfg_dict quad_mode]
    set FEATURE_ANGLE  [expr {double([dict get $cfg_dict feature_angle])}]
    set MIN_LEN_FACTOR [expr {double([dict get $cfg_dict min_len_factor])}]
    set MAX_LEN_FACTOR [expr {double([dict get $cfg_dict max_len_factor])}]
    set FILLET_ROWS    [dict get $cfg_dict fillet_rows]
    set SMOOTH_PASSES  [dict get $cfg_dict smooth_passes]
    set PATCH_HOLES    [dict get $cfg_dict patch_holes]
    set SELECT_MODE    [dict get $cfg_dict select_mode]
    set SAVE_HM        [dict get $cfg_dict save_hm]
    set OUT_DIR        [dict get $cfg_dict out_dir]
    set OUT_NAME       [dict get $cfg_dict out_name]

    *clearmark surfs 1
    if {$SELECT_MODE eq "pick"} {
        *createmarkpanel surfs 1 "Select surfaces for 2D Quad BatchMesher (Fixed 2.0mm)"
    } else {
        *createmark surfs 1 "all"
    }
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
    if {[llength $surf_ids] == 0} {
        return [dict create status FAIL msg "No surfaces found/selected for 2D Quad mesh."]
    }

    if {$SELECT_MODE eq "all"} {
        *clearmark elems 1; *createmark elems 1 "all"
        if {[llength [hm_getmark elems 1]] > 0} { catch { *deletemark elems 1 } }
        *clearmark elems 1
    }
    catch { *elementorder 1 }

    set work_dir [ylh_ascii_temp_dir]
    set tmp_stem "_ylh2_[pid]"
    lassign [ylh2_write_quad_bm_files $work_dir $tmp_stem $ELEM_SIZE $FEATURE_ANGLE $MIN_LEN_FACTOR $MAX_LEN_FACTOR $QUAD_MODE $FILLET_ROWS $MIN_JACOBIAN $MAX_WARPAGE $MAX_SKEW $TARGET_ASPECT] bm_cr bm_pr

    *clearmark lines 1; *createmark lines 1 "all"
    catch { *maskmark lines 1 }
    *clearmark lines 1

    set bm_err ""
    *clearmark surfs 1
    eval *createmark surfs 1 $surf_ids
    if {[catch { *hm_batchmesh 1 $bm_cr $bm_pr } err]} { set bm_err "hm_batchmesh(quad): $err" }
    *clearmark surfs 1
    catch { *unmaskall2 }
    catch { file delete $bm_cr $bm_pr }
    foreach f [glob -nocomplain -directory $work_dir "*${tmp_stem}*"] { catch { file delete $f } }

    # Refresh surface IDs after cleanup & patch any unmeshed holes
    *clearmark surfs 1; *createmark surfs 1 "all"
    set cur_surfs [hm_getmark surfs 1]
    *clearmark surfs 1
    set n_patched 0; set n_unmeshed 0
    if {$PATCH_HOLES} {
        lassign [ylh2_patch_unmeshed $cur_surfs $ELEM_SIZE $QUAD_MODE] n_patched n_unmeshed
    } else {
        set n_unmeshed [llength [ylh_find_unmeshed_surfs $cur_surfs]]
    }

    *clearmark elems 1
    *createmark elems 1 "by config" quad4 tria3
    if {[llength [hm_getmark elems 1]] > 0} {
        catch { *equivalence elems 1 [expr {0.002 * $ELEM_SIZE}] 1 0 0 }
    }
    *clearmark elems 1

    # 2D CAD-snapped interior smoothing
    set n_smoothed_2d 0
    if {$SMOOTH_PASSES > 0} {
        catch { set n_smoothed_2d [hqc::smooth_2d_quad $ELEM_SIZE $FEATURE_ANGLE $SMOOTH_PASSES] }
    }

    set n_quad [ylh_count_elems quad4]
    set n_tria [ylh_count_elems tria3]
    set tria_pct [expr {($n_quad + $n_tria) > 0 ? 100.0 * $n_tria / ($n_quad + $n_tria) : 0.0}]

    set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
    if {$n_quad + $n_tria > 0} {
        foreach {var cmd lim lbl} [list \
            bad_jac  *elementtestjacobian $MIN_JACOBIAN  "2D Jacobian" \
            bad_warp *elementtestwarpage  $MAX_WARPAGE   "2D Warpage" \
            bad_asp  *elementtestaspect   $TARGET_ASPECT "2D Aspect" \
            bad_skew *elementtestskew     $MAX_SKEW      "2D Skew"] {
            *clearmark elems 1; *clearmark elems 2
            *createmark elems 1 "by config" quad4 tria3
            if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} { set $var [llength [hm_getmark elems 2]] }
            *clearmark elems 1; *clearmark elems 2
        }
    }

    set saved_txt "(not saved)"
    set write_err ""
    if {$SAVE_HM && $OUT_DIR ne ""} {
        file mkdir $OUT_DIR
        set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_H2_2DQuad_2mm"}]
        set out_hm [file join $OUT_DIR "${stem}.hm"]
        catch { hm_answernext yes }
        if {[catch { *writefile $out_hm 1 } write_err]} {
            set saved_txt "SAVE FAILED: $write_err"
        } else {
            set saved_txt $out_hm
        }
    }

    set pass_2d [expr {$bm_err eq "" && $write_err eq "" && $n_quad > 0 && $n_unmeshed == 0 && $bad_jac == 0 && $bad_warp == 0 && $bad_asp == 0 && $bad_skew == 0 ? 1 : 0}]
    set status [expr {$pass_2d ? "OK" : "FAIL"}]
    set msg [format "YL_H2 (2D Quad-Priority Skin, Fixed 2.0mm) Finished\n\n- Status: %s\n- Size: %.2f mm (FIXED), Mode: %s, FeatureAngle: %.1f deg, FilletRows: %s\n- Elements: Quad4=%d, Tria3=%d (Tria ratio: %.2f%%)\n- Hole Patching: %d surface(s) patched, %d still unmeshed\n- 2D CAD-Snapped Smoothing: %d nodes moved\n- Quality (HM):\n    Bad Jacobian (<%.2f) : %d\n    Bad Warpage  (>%.1f) : %d\n    Bad Aspect   (>%.1f) : %d\n    Bad Skew     (>%.1f) : %d\n- Saved HM: %s" \
        $status $ELEM_SIZE $QUAD_MODE $FEATURE_ANGLE $FILLET_ROWS \
        $n_quad $n_tria $tria_pct $n_patched $n_unmeshed $n_smoothed_2d \
        $MIN_JACOBIAN $bad_jac $MAX_WARPAGE $bad_warp $TARGET_ASPECT $bad_asp $MAX_SKEW $bad_skew $saved_txt]
    if {$bm_err ne ""} { append msg "\n\n*** $bm_err" }
    return [dict create status $status msg $msg]
}

# =====================================================================
# MODULE 5 : YL_H1 (3D Multi-Solid Map Hexa Engine - Fixed 2.0mm)
# =====================================================================

# --- [TUNABLE FUNCTION 6] 3D SolidMap Execution Flags ---
proc ylh1_run_solidmap {solid_ids elem_size quad_mode custom_flags} {
    # HM 2019 Multi-Solids flags: 131072 = quads only, 131074 = mixed
    set sm_flags [expr {$quad_mode eq "quads" ? 131072 : 131074}]
    if {$custom_flags ne "" && $custom_flags ne "auto"} { set sm_flags $custom_flags }

    set sm_err ""
    *clearmark solids 1
    eval *createmark solids 1 $solid_ids
    if {[catch {
        *solidmap_solids_begin 1 $sm_flags $elem_size
        *solidmap_solids_end
    } err1]} {
        *clearmark solids 1
        eval *createmark solids 1 $solid_ids
        if {[catch {
            *solidmap_solids_begin 1 0 $elem_size
            *solidmap_solids_end
        } err2]} {
            set sm_err "solidmap failed: $err1 / $err2"
        }
    }
    *clearmark solids 1
    return $sm_err
}

# --- [TUNABLE FUNCTION 7] Narrow CAD face defeature before SolidMap ---
#   CATIA solids often carry strip faces only 0.01-0.5 mm wide (tiny fillets/chamfers,
#   faces split into bands). SolidMap puts a node row on EVERY face boundary, so each
#   strip becomes a hex layer as thin as the strip (aspect 100+), and stacks of strips
#   at part ends turn into tria/penta patches.
#   BatchMesher geometry cleanup merges faces narrower than 'width' into their
#   neighbours (suppresses the shared edges); its temporary 2D mesh is deleted.
#   Duplicate/overlap surface repair is switched OFF so the solid itself is not edited.
#   Returns {ok text}. ok=0 only if the solid count changed (model must be reloaded).
proc ylh1_defeature {elem_size width} {
    *clearmark solids 1; *createmark solids 1 "all"
    set n0 [llength [hm_getmark solids 1]]
    *clearmark solids 1
    *clearmark surfs 1; *createmark surfs 1 "all"
    set surf_ids [hm_getmark surfs 1]
    *clearmark surfs 1
    if {[llength $surf_ids] == 0} { return [list 1 "skipped (no surfaces)"] }

    set work_dir [ylh_ascii_temp_dir]
    set stem "_ylh1df_[pid]"
    lassign [ylh2_write_quad_bm_files $work_dir $stem $elem_size 30.0 0.2 1.6 "mixed" "standard" 0.3 30.0 70.0 10.0] cr pr
    # cleanup-only overrides: merge narrow faces, never delete / repair surfaces
    set fh [open $pr r]; set txt [read $fh]; close $fh
    foreach {key val} [list narrow_surfs_merge_width [format %.4g $width] narrow_surfs_treat_flags 3 \
                            del_dupl_surfs_flag 0 fix_overlapsurfs_flag 0 edges_stitch_flags 0] {
        regsub -line "^${key}\[ \t\]+.*\$" $txt [format "%-34s%s" $key $val] txt
    }
    set fh [open $pr w]; puts -nonewline $fh $txt; close $fh

    *clearmark lines 1; *createmark lines 1 "all"
    catch { *maskmark lines 1 }
    *clearmark lines 1
    set err ""
    *clearmark surfs 1
    eval *createmark surfs 1 $surf_ids
    if {[catch { *hm_batchmesh 1 $cr $pr } e]} { set err $e }
    *clearmark surfs 1
    catch { *unmaskall2 }
    catch { file delete $cr $pr }
    foreach f [glob -nocomplain -directory $work_dir "*${stem}*"] { catch { file delete $f } }
    ylh_delete_elems {tria3 tria6 quad4 quad8}

    *clearmark solids 1; *createmark solids 1 "all"
    set n1 [llength [hm_getmark solids 1]]
    *clearmark solids 1
    if {$n1 != $n0} { return [list 0 "solid count changed $n0 -> $n1 during geometry cleanup"] }
    if {$err ne ""} { return [list 1 "WARN cleanup failed ($err) - SolidMap runs on the original faces"] }
    return [list 1 "OK - faces narrower than [format %.2f $width] mm merged into neighbours"]
}

proc ylh1_run {cfg_dict} {
    # Element size strictly fixed (::YLH_ELEM_SIZE)
    set ELEM_SIZE        $::YLH_ELEM_SIZE
    set DEFEATURE        [expr {[dict exists $cfg_dict defeature] ? [dict get $cfg_dict defeature] : 1}]
    set DEFEATURE_WIDTH  [expr {[dict exists $cfg_dict defeature_width] ? double([dict get $cfg_dict defeature_width]) : 0.5 * $::YLH_ELEM_SIZE}]
    set TARGET_ASPECT    [expr {double([dict get $cfg_dict aspect])}]
    set MIN_JACOBIAN     [expr {double([dict get $cfg_dict min_jac])}]
    set MAX_WARPAGE      [expr {double([dict get $cfg_dict max_warp])}]
    set MAX_SKEW         [expr {double([dict get $cfg_dict max_skew])}]
    set QUAD_MODE        [dict get $cfg_dict quad_mode]
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

    # 1. Select solids
    *clearmark solids 1
    if {$SELECT_MODE eq "pick"} {
        *createmarkpanel solids 1 "Select solids for 3D Multi-Solid Map Hexa (Fixed 2.0mm)"
    } else {
        *createmark solids 1 "all"
    }
    set solid_ids [hm_getmark solids 1]
    *clearmark solids 1
    set n_solids [llength $solid_ids]

    if {$n_solids == 0} {
        if {$QUAD_FALLBACK} {
            set ans [tk_messageBox -type yesno -icon warning -title "YL HEX V2 (2mm)" \
                -message "No solids found/selected.\nRun H2 (2D Quad Skin, 2.0mm) on surfaces instead?"]
            if {$ans eq "yes"} { return [ylh2_run $cfg_dict] }
        }
        return [dict create status FAIL msg "No solids found/selected. Solid geometry is required for Multi-Solid Map Hexa."]
    }

    if {$SELECT_MODE eq "all"} {
        ylh_delete_elems {tria3 tria6 quad4 quad8 tetra4 tetra10 hex8 hex20 penta6 penta15 pyramid5 pyramid13}
    }
    catch { *elementorder 1 }

    # 1b. Narrow CAD face defeature (thin hex layers / aspect 100+ / tria-penta tips)
    set df_txt "off"
    if {$DEFEATURE && $SELECT_MODE ne "all"} {
        set df_txt "skipped (pick mode: only runs on 'All Solids / Surfaces')"
    } elseif {$DEFEATURE} {
        lassign [ylh1_defeature $ELEM_SIZE $DEFEATURE_WIDTH] df_ok df_txt
        if {!$df_ok} {
            return [dict create status FAIL msg "YL_H1 stopped before SolidMap.\n\nDefeature: $df_txt\n\nReload the model and run again with 'Defeature narrow CAD faces' unchecked."]
        }
        *clearmark solids 1; *createmark solids 1 "all"
        set solid_ids [hm_getmark solids 1]
        *clearmark solids 1
    }

    # 2. Multi-Solid Map at 2.0 mm
    set sm_err [ylh1_run_solidmap $solid_ids $ELEM_SIZE $QUAD_MODE $CUSTOM_FLAGS]

    if {$DELETE_2D_SKIN} {
        ylh_delete_elems {tria3 tria6 quad4 quad8}
    }

    set equiv_mode [ylh_equivalence_3d [expr {0.001 * $ELEM_SIZE}] $EQUIV_GLOBAL]
    set n_hex   [ylh_count_elems {hex8 hex20}]
    set n_penta [ylh_count_elems {penta6 penta15}]

    set unmap_ids {}
    foreach s $solid_ids {
        if {[ylh_solid_3d_count $s] == 0} { lappend unmap_ids $s }
    }
    set n_unmap [llength $unmap_ids]
    set unmap_check_ok 1
    if {$n_unmap == $n_solids && $n_hex + $n_penta > 0} {
        set unmap_check_ok 0; set n_unmap -1; set unmap_ids {}
    }

    if {$QUAD_FALLBACK && ($n_hex == 0 || $n_unmap > 0)} {
        if {$n_hex == 0} {
            set q "3D SolidMap created 0 Hex elements (Unmappable solids: $n_unmap / $n_solids).\nThis solid must be split (Geom -> Solid Edit -> Trim) into mappable blocks first.\n\nSwitch to H2 (2D Quad Skin, 2.0mm) for inspection/manual sweep?"
        } else {
            set q "Partial SolidMap: Hex=$n_hex created, but $n_unmap of $n_solids solid(s) are Unmappable (IDs: [join [lrange $unmap_ids 0 9] ,]).\n\nClick YES to replace with 2D Quad Skin (H2), or NO to keep the current 3D Hex mesh."
        }
        if {[tk_messageBox -type yesno -icon question -title "YL HEX V2 - Unmappable Solid" -message $q] eq "yes"} {
            set fb_cfg $cfg_dict
            if {[dict get $fb_cfg out_name] ne ""} {
                dict set fb_cfg out_name "[dict get $fb_cfg out_name]_2DQuad"
            }
            return [ylh2_run $fb_cfg]
        }
    }

    # 3. 3D Hex8 Interior Node Smoothing
    set n_smoothed 0
    set smooth_err ""
    if {$n_hex > 0 && $SMOOTH_PASSES > 0} {
        if {[catch { set n_smoothed [hqc::smooth_hex $MIN_JACOBIAN $SMOOTH_PASSES] } e]} {
            set smooth_err "smoothing error: $e"
        }
    }

    # 4. 3D Quality Checks
    set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
    if {$n_hex + $n_penta > 0} {
        foreach {var cmd lim lbl} [list \
            bad_jac  *elementtestjacobian $MIN_JACOBIAN  "3D Jacobian" \
            bad_warp *elementtestwarpage  $MAX_WARPAGE   "3D Warpage" \
            bad_asp  *elementtestaspect   $TARGET_ASPECT "3D Aspect" \
            bad_skew *elementtestskew     $MAX_SKEW      "3D Skew"] {
            *clearmark elems 1; *clearmark elems 2
            *createmark elems 1 "by config" hex8 penta6
            if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} {
                set $var [llength [hm_getmark elems 2]]
            }
            *clearmark elems 1; *clearmark elems 2
        }
    }

    array unset ::YHX
    *clearmark elems 1; *createmark elems 1 "by config" hex8
    set hex_eids [hm_getmark elems 1]
    *clearmark elems 1
    set HS [yhs_hex_stats $hex_eids 300000]
    array unset ::YHX

    set neg_jac  [dict get $HS neg_jac]
    set jac_min  [ylh_fmt $HS jac_min  %.3f]
    set jac_mean [ylh_fmt $HS jac_mean %.3f]
    set warp_max [ylh_fmt $HS warp_max %.2f]
    set asp_p95  [ylh_fmt $HS asp_p95  %.2f]
    set size_med [ylh_fmt $HS size_med %.2f]
    set size_p05 [ylh_fmt $HS size_p05 %.2f]
    set size_p95 [ylh_fmt $HS size_p95 %.2f]
    set n_sliver [expr {[dict exists $HS n_sliver] ? [dict get $HS n_sliver] : 0}]
    set sliver_len [expr {0.25 * $ELEM_SIZE}]

    # 5. Abaqus Element Order & Type Assignment
    set tpl_path "[hm_info -appinfo SPECIFIEDPATH TEMPLATES_DIR]/feoutput/abaqus/standard.3d"
    set type_err ""
    set assigned_hex_type ""
    if {$n_hex + $n_penta > 0} {
        catch { *templatefileset $tpl_path }
        set hex_pair [ylh_lookup_abaqus_type $ABAQUS_ELEM_TYPE $tpl_path]
        if {[llength $hex_pair] != 2} {
            set type_err "Unknown Abaqus element type '$ABAQUS_ELEM_TYPE'"
            set hex_pair {208 4}
        }
        lassign $hex_pair hex_cfg hex_typ
        set is_second [expr {$hex_cfg == 220}]

        if {$is_second} {
            *clearmark elems 1
            *createmark elems 1 "by config" hex8 penta6
            if {[llength [hm_getmark elems 1]] > 0} { catch { *orderchangetosecond 1 1 } }
            *clearmark elems 1
            if {$n_hex > 0 && [ylh_count_elems {hex20}] == 0} {
                set type_err "2nd-order (Hex20) conversion failed; elements remain Hex8."
            }
        }

        *clearmark elems 1
        *createmark elems 1 "by config" [expr {$is_second ? "hex20" : "hex8"}]
        set e0 [lindex [hm_getmark elems 1] 0]
        if {$e0 ne ""} {
            catch { *elementtype $hex_cfg $hex_typ }
            catch { *elementsettypes 1 }
            catch { set assigned_hex_type [hm_getvalue elems id=$e0 dataname=typename] }
        }
        *clearmark elems 1

        if {$n_penta > 0} {
            set wname [ylh_wedge_for_hex $ABAQUS_ELEM_TYPE]
            set wpair [ylh_lookup_abaqus_type $wname $tpl_path]
            if {[llength $wpair] == 2} {
                lassign $wpair w_cfg w_typ
                *clearmark elems 1
                *createmark elems 1 "by config" [expr {$is_second ? "penta15" : "penta6"}]
                if {[llength [hm_getmark elems 1]] > 0} {
                    catch { *elementtype $w_cfg $w_typ }
                    catch { *elementsettypes 1 }
                }
                *clearmark elems 1
            }
        }
        if {$type_err eq "" && $assigned_hex_type ne "" && $assigned_hex_type ne $ABAQUS_ELEM_TYPE} {
            set type_err "Element type mismatch: actual='$assigned_hex_type', wanted='$ABAQUS_ELEM_TYPE'"
        }
    }

    set n_hex   [ylh_count_elems {hex8 hex20}]
    set n_penta [ylh_count_elems {penta6 penta15}]
    set penta_pct [expr {($n_hex + $n_penta) > 0 ? 100.0 * $n_penta / ($n_hex + $n_penta) : 0.0}]

    # 6. Optional Save / INP Export
    set saved_hm "(not saved)"; set saved_inp "(not exported)"
    set write_err ""; set inp_err ""
    set stem [expr {$OUT_NAME ne "" ? $OUT_NAME : "YL_HEX_${ABAQUS_ELEM_TYPE}_2mm"}]

    if {$SAVE_HM && $OUT_DIR ne ""} {
        file mkdir $OUT_DIR
        set out_hm [file join $OUT_DIR "${stem}.hm"]
        catch { hm_answernext yes }
        if {[catch { *writefile $out_hm 1 } write_err]} { set saved_hm "SAVE FAILED: $write_err" } else { set saved_hm $out_hm }
    }
    if {$EXPORT_INP && $OUT_DIR ne ""} {
        if {$n_hex > 0 && $neg_jac == 0 && $type_err eq ""} {
            file mkdir $OUT_DIR
            set out_inp [file join $OUT_DIR "${stem}.inp"]
            catch { file delete $out_inp }
            catch { hm_answernext yes }
            if {[catch {
                *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"
                *feoutputwithdata $tpl_path $out_inp 0 0 2 1 2
            } inp_err]} {
                set saved_inp "INP EXPORT FAILED: $inp_err"
            } elseif {![file exists $out_inp]} {
                set inp_err "INP file was not created"
                set saved_inp "INP EXPORT FAILED: file not written"
            } else {
                set inp_err ""; set saved_inp $out_inp
            }
        } else {
            set saved_inp "SKIPPED (no hex / negative Jacobian / type error)"
        }
    }

    # 7. Summary Report
    set _why {}
    if {$write_err ne ""} { lappend _why "SAVE FAILED: $write_err" }
    if {$sm_err ne ""}    { lappend _why $sm_err }
    if {$type_err ne ""}  { lappend _why $type_err }
    if {$inp_err ne ""}   { lappend _why "INP: $inp_err" }
    if {$n_hex == 0}      { lappend _why "NO HEX ELEMENTS CREATED (split solids into mappable blocks)" }
    if {$n_unmap > 0}     { lappend _why "$n_unmap SOLID(S) UNMAPPABLE (IDs: [join [lrange $unmap_ids 0 9] ,])" }
    if {!$unmap_check_ok} { lappend _why "UNMAPPABLE CHECK INDETERMINATE" }
    if {$neg_jac > 0}     { lappend _why "$neg_jac NEGATIVE JACOBIAN HEX(ES)" }
    if {$bad_jac > 0}     { lappend _why "$bad_jac HEX(ES) BELOW JACOBIAN $MIN_JACOBIAN" }
    if {$bad_warp > 0}    { lappend _why "$bad_warp HEX(ES) ABOVE WARPAGE $MAX_WARPAGE" }
    if {$bad_asp > 0}     { lappend _why "$bad_asp HEX(ES) ABOVE ASPECT $TARGET_ASPECT" }
    if {$bad_skew > 0}    { lappend _why "$bad_skew HEX(ES) ABOVE SKEW $MAX_SKEW" }
    if {$size_med ne "" && abs($size_med - $ELEM_SIZE) > 0.3 * $ELEM_SIZE} {
        lappend _why "ACTUAL HEX SIZE $size_med mm IS NOT THE TARGET $ELEM_SIZE mm"
    }
    if {$n_sliver > 0}    { lappend _why "$n_sliver SLIVER HEX(ES): shortest edge < [format %.2f $sliver_len] mm (narrow CAD faces -> raise Defeature width)" }
    if {$n_hex + $n_penta > 0} {
        foreach {lbl v} [list Jacobian $bad_jac Warpage $bad_warp Aspect $bad_asp Skew $bad_skew] {
            if {$v < 0} { lappend _why "3D $lbl check command failed" }
        }
    }

    set status [expr {[llength $_why] == 0 ? "OK" : "FAIL"}]
    set unmap_txt [expr {$unmap_check_ok ? ($n_unmap > 0 ? [join [lrange $unmap_ids 0 9] ","] : "none") : "indeterminate"}]
    set msg [format "YL_H1 (Multi-Solid Map Hexa, Fixed 2.0mm) Finished\n\n- Status: %s\n- Size: %.2f mm (FIXED), Type: %s (actual: %s)\n- QuadMode: %s, Equivalence: %s\n- Defeature: %s\n- Solids: %d (Unmappable: %d -> %s)\n- Elements: Hex=%d, Penta=%d (Wedge ratio: %.2f%%)\n- Actual size: hex edge median %s mm (p5 %s / p95 %s), target %.1f mm\n- Sliver hexes (shortest edge < %.2f mm): %d\n- 3D Interior Smoothing: %d nodes moved\n- Quality (HM):\n    Bad Jacobian (<%.2f) : %d\n    Bad Warpage  (>%.1f) : %d\n    Bad Aspect   (>%.1f) : %d\n    Bad Skew     (>%.1f) : %d\n- Stats (Scaled Jacobian):\n    NegJac=%d, SJ min=%s, SJ mean=%s, Warp max=%s, Asp p95=%s\n- Saved HM: %s\n- Exported INP: %s" \
        $status $ELEM_SIZE $ABAQUS_ELEM_TYPE $assigned_hex_type \
        $QUAD_MODE $equiv_mode $df_txt $n_solids $n_unmap $unmap_txt \
        $n_hex $n_penta $penta_pct $size_med $size_p05 $size_p95 $ELEM_SIZE $sliver_len $n_sliver $n_smoothed \
        $MIN_JACOBIAN $bad_jac $MAX_WARPAGE $bad_warp $TARGET_ASPECT $bad_asp $MAX_SKEW $bad_skew \
        $neg_jac $jac_min $jac_mean $warp_max $asp_p95 $saved_hm $saved_inp]
    if {$smooth_err ne ""} { append msg "\n- WARN: $smooth_err" }
    foreach w $_why { append msg "\n*** $w" }
    return [dict create status $status msg $msg]
}

# =====================================================================
# MODULE 6 : Native HyperMesh Tk GUI (2.0 mm Fixed)
# =====================================================================
proc ylh_launch_gui {} {
    variable ::ylh_cfg
    # fill keys that a previous run in this HyperMesh session did not have yet
    foreach {k v} [ylh_default_params] {
        if {![info exists ::ylh_cfg($k)]} { set ::ylh_cfg($k) $v }
    }
    set ::ylh_cfg(elem_size) $::YLH_ELEM_SIZE

    catch { destroy .ylhex2 }
    set w [toplevel .ylhex2]
    wm title $w "YL Mesh HEX V2 (Fixed 2.0 mm - Tunable Edition)"
    wm resizable $w 0 0
    catch { wm attributes $w -topmost 1 }

    # 1. Mode & Target
    set f1 [labelframe $w.f1 -text " 1. Mode & Target (Elem Size Fixed = 2.0 mm) " -padx 8 -pady 6]
    pack $f1 -fill x -padx 10 -pady 5
    radiobutton $f1.m1 -text "3D Hexa (Multi-Solid Map)" -variable ::ylh_cfg(mode) -value "hex"
    radiobutton $f1.m2 -text "2D Quad Skin Only (BatchMesher + Hole Patch)" -variable ::ylh_cfg(mode) -value "quad"
    grid $f1.m1 -row 0 -column 0 -sticky w -padx 4
    grid $f1.m2 -row 0 -column 1 -sticky w -padx 12

    radiobutton $f1.s1 -text "All Solids / Surfaces" -variable ::ylh_cfg(select_mode) -value "all"
    radiobutton $f1.s2 -text "Pick Manually (Interactive Panel)" -variable ::ylh_cfg(select_mode) -value "pick"
    grid $f1.s1 -row 1 -column 0 -sticky w -padx 4 -pady 2
    grid $f1.s2 -row 1 -column 1 -sticky w -padx 12 -pady 2

    # 2. Curve & Quality Parameters
    set f2 [labelframe $w.f2 -text " 2. Curve Capture & Quality Criteria " -padx 8 -pady 6]
    pack $f2 -fill x -padx 10 -pady 5

    label $f2.lsz -text "Elem Size (mm):"
    entry $f2.esz -textvariable ::ylh_cfg(elem_size) -width 9 -state disabled
    label $f2.lfa -text "Feature Angle (deg):"
    entry $f2.efa -textvariable ::ylh_cfg(feature_angle) -width 9
    label $f2.lml -text "Min Len Factor:"
    entry $f2.eml -textvariable ::ylh_cfg(min_len_factor) -width 9

    label $f2.ljc -text "Min Jacobian:"
    entry $f2.ejc -textvariable ::ylh_cfg(min_jac) -width 9
    label $f2.lwp -text "Max Warpage (deg):"
    entry $f2.ewp -textvariable ::ylh_cfg(max_warp) -width 9
    label $f2.las -text "Max Aspect:"
    entry $f2.eas -textvariable ::ylh_cfg(aspect) -width 9

    label $f2.lsk -text "Max Skew (deg):"
    entry $f2.esk -textvariable ::ylh_cfg(max_skew) -width 9
    label $f2.lsm -text "Smooth Passes:"
    entry $f2.esm -textvariable ::ylh_cfg(smooth_passes) -width 9
    label $f2.lfl -text "SolidMap Flags:"
    entry $f2.efl -textvariable ::ylh_cfg(sm_flags) -width 9

    grid $f2.lsz $f2.esz $f2.lfa $f2.efa $f2.lml $f2.eml -sticky w -padx 4 -pady 3
    grid $f2.ljc $f2.ejc $f2.lwp $f2.ewp $f2.las $f2.eas -sticky w -padx 4 -pady 3
    grid $f2.lsk $f2.esk $f2.lsm $f2.esm $f2.lfl $f2.efl -sticky w -padx 4 -pady 3

    # 3. Element Type & Options
    set f3 [labelframe $w.f3 -text " 3. Element Type & Options " -padx 8 -pady 6]
    pack $f3 -fill x -padx 10 -pady 5

    label $f3.let -text "Abaqus Element:"
    eval tk_optionMenu $f3.oet ::ylh_cfg(elem_type) C3D8H C3D8RH C3D8 C3D8R C3D8I C3D20H C3D20RH C3D20 C3D20R

    label $f3.lqm -text "2D Source:"
    tk_optionMenu $f3.oqm ::ylh_cfg(quad_mode) quads mixed

    label $f3.lfr -text "Fillet Rows:"
    tk_optionMenu $f3.ofr ::ylh_cfg(fillet_rows) dense standard

    grid $f3.let $f3.oet $f3.lqm $f3.oqm $f3.lfr $f3.ofr -sticky w -padx 4 -pady 3

    checkbutton $f3.c1 -text "Delete 2D skin after 3D Hex creation" -variable ::ylh_cfg(delete_2d)
    checkbutton $f3.c2 -text "Auto-patch unmeshed surfaces (removes white holes in 2D mode)" -variable ::ylh_cfg(patch_holes)
    checkbutton $f3.c3 -text "Global equivalence across components (default: per-comp)" -variable ::ylh_cfg(equiv_global)
    checkbutton $f3.c4 -text "Offer 2D Quad fallback (H2) if Solid is Unmappable" -variable ::ylh_cfg(quad_fallback)
    grid $f3.c1 -row 1 -column 0 -columnspan 6 -sticky w -padx 4 -pady 1
    grid $f3.c2 -row 2 -column 0 -columnspan 6 -sticky w -padx 4 -pady 1
    grid $f3.c3 -row 3 -column 0 -columnspan 6 -sticky w -padx 4 -pady 1
    grid $f3.c4 -row 4 -column 0 -columnspan 6 -sticky w -padx 4 -pady 1
    checkbutton $f3.c5 -text "Defeature narrow CAD faces before SolidMap (fixes thin hex layers / aspect)" -variable ::ylh_cfg(defeature)
    label $f3.ldw -text "narrower than (mm):"
    entry $f3.edw -textvariable ::ylh_cfg(defeature_width) -width 6
    grid $f3.c5  -row 5 -column 0 -columnspan 4 -sticky w -padx 4 -pady 1
    grid $f3.ldw -row 5 -column 4 -sticky e -padx 2
    grid $f3.edw -row 5 -column 5 -sticky w -padx 2

    # 4. Optional Output
    set f4 [labelframe $w.f4 -text " 4. Optional Save / Export " -padx 8 -pady 6]
    pack $f4 -fill x -padx 10 -pady 5

    checkbutton $f4.cs -text "Save .hm after meshing" -variable ::ylh_cfg(save_hm)
    checkbutton $f4.ci -text "Export Abaqus .inp" -variable ::ylh_cfg(export_inp)
    grid $f4.cs -row 0 -column 0 -sticky w -padx 4
    grid $f4.ci -row 0 -column 1 -sticky w -padx 12

    label $f4.ldir -text "Output Folder:"
    entry $f4.edir -textvariable ::ylh_cfg(out_dir) -width 42
    button $f4.bdir -text "Browse..." -command {
        set d [tk_chooseDirectory -initialdir $::ylh_cfg(out_dir) -title "Select Output Folder"]
        if {$d ne ""} { set ::ylh_cfg(out_dir) $d }
    }
    label $f4.lnm -text "Output Name:"
    entry $f4.enm -textvariable ::ylh_cfg(out_name) -width 26

    grid $f4.ldir -row 1 -column 0 -sticky w -padx 4 -pady 3
    grid $f4.edir -row 1 -column 1 -sticky ew -padx 4 -pady 3
    grid $f4.bdir -row 1 -column 2 -sticky w -padx 4 -pady 3
    grid $f4.lnm  -row 2 -column 0 -sticky w -padx 4 -pady 3
    grid $f4.enm  -row 2 -column 1 -sticky w -padx 4 -pady 3

    # Buttons
    set fb [frame $w.fb]
    pack $fb -fill x -padx 10 -pady 10
    set ::ylh_gui_action "cancel"
    button $fb.run -text "Run (2.0 mm)" -width 14 -command { set ::ylh_gui_action "run"; destroy .ylhex2 }
    button $fb.can -text "Cancel"       -width 12 -command { set ::ylh_gui_action "cancel"; destroy .ylhex2 }
    pack $fb.can -side right -padx 4
    pack $fb.run -side right -padx 4

    wm protocol $w WM_DELETE_WINDOW { set ::ylh_gui_action "cancel"; destroy .ylhex2 }
    tkwait window $w

    if {$::ylh_gui_action ne "run"} { return }

    foreach {k lbl} {feature_angle "Feature Angle" min_len_factor "Min Len Factor" min_jac "Min Jacobian" max_warp "Max Warpage" aspect "Max Aspect" max_skew "Max Skew" smooth_passes "Smooth Passes" defeature_width "Defeature Width"} {
        if {![string is double -strict $::ylh_cfg($k)]} {
            tk_messageBox -icon error -title "YL HEX V2" -message "Invalid number for $lbl: '$::ylh_cfg($k)'"
            return
        }
    }

    set cfg_dict [array get ::ylh_cfg]
    if {$::ylh_cfg(mode) eq "hex"} {
        set res [ylh1_run $cfg_dict]
    } else {
        set res [ylh2_run $cfg_dict]
    }

    set icon [expr {[dict get $res status] eq "OK" ? "info" : "warning"}]
    tk_messageBox -icon $icon -title "YL HEX V2 Result ([dict get $res status])" -message [dict get $res msg]
}

# Entry point
ylh_launch_gui
