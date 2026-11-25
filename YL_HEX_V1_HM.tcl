# =====================================================================
# YL_HEX_V1_HM.tcl : YL Hexa mesh - run INSIDE the HyperMesh 2019 GUI
#   Meshes the model that is currently open in HyperMesh (no PowerShell / .bat needed).
#
#   How to run : HyperMesh > File > Run > Tcl/Tk Script...  -> select this file
#                (or in the Tcl command window:  source C:/path/YL_HEX_V1_HM.tcl )
#   Save this file as plain text: Notepad encoding "ANSI" or "UTF-8".
#   Do NOT use "UTF-8 (BOM)" - the BOM causes: invalid command name "#"
#
#   WARNING: existing 2D/3D elements of the current model are deleted first.
#   Result : the meshed model is saved to YL_OUT_DIR, and a summary box is shown.
#   Same engine as YL_HEX_V1.ps1 (H1 = 3D Multi-Solid Map + smoothing + quality + Abaqus type).
# =====================================================================

# ---------------- settings (edit here) ----------------
set ::YL_ELEM_SIZE     2.0        ;# element size (mm)
set ::YL_ELEM_TYPE     "C3D8H"    ;# C3D8H C3D8RH C3D8 C3D8R C3D8I C3D20H C3D20RH C3D20 C3D20R
set ::YL_MIN_JACOBIAN  0.50       ;# min Jacobian
set ::YL_MAX_WARPAGE   20.0       ;# max warpage (deg)
set ::YL_ASPECT        5.0        ;# max aspect ratio
set ::YL_MAX_SKEW      60.0       ;# max skew (deg)
set ::YL_QUAD_MODE     "mixed"    ;# mixed / quads (SolidMap source face mode)
set ::YL_EXPORT_INP    0          ;# 1 = also write Abaqus .inp
set ::YL_OUT_DIR       "C:/YL_HEX_out"   ;# output folder - use a path WITHOUT Korean characters
set ::YL_OUT_NAME      ""         ;# output name ("" = <model name>_HEX_<type>_<size>mm)
# --------------------------------------------------------

set ::YL_QUAD_FALLBACK 0
set ::YL_BASE_NAME "YL_HM"
if {![catch {hm_info currentfile} _f] && [string trim $_f] ne ""} { set ::YL_BASE_NAME [file rootname [file tail $_f]] }

proc ylhm_show {} {
    array set R {status FAIL msg ""}
    array set R $::YL_RESULT
    set txt "Status: $R(status)\n\n$R(msg)"
    puts $txt
    catch { tk_messageBox -title "YL HEX V1 (HyperMesh)" -type ok -icon [expr {$R(status) eq "OK" ? "info" : "warning"}] -message $txt }
}

# =====================================================================
# YL_HEX_qclean.tcl : Topology-preserving 3D Hex8 interior node smoother
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

proc hqc::elem_jac {e} {
    variable X; variable H
    set pts {}
    foreach n $H($e) { lappend pts $X($n) }
    return [hex_min_jac $pts]
}

proc hqc::smooth_hex {min_jac {passes 5}} {
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
        array unset cand
        array set cand {}
        foreach e [array names J] {
            if {$J($e) < $min_jac} {
                foreach n $H($e) {
                    if {![info exists BND($n)]} { set cand($n) 1 }
                }
            }
        }
        if {[array size cand] == 0} { break }
        set changed 0
        foreach n [array names cand] {
            set els $NH($n)
            array unset nbrs
            foreach e $els {
                foreach m $H($e) { if {$m != $n} { set nbrs($m) 1 } }
            }
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
            foreach a {0.5 1.0 0.25} {
                set X($n) [list [expr {$ox + $a*($cx-$ox)}] [expr {$oy + $a*($cy-$oy)}] [expr {$oz + $a*($cz-$oz)}]]
                set jmin 1e9; set js {}
                foreach e $els { set j [elem_jac $e]; lappend js $e $j; if {$j < $jmin} { set jmin $j } }
                if {$jmin > $best_j + 1e-4} { set best_j $jmin; set best_pos $X($n); set best_js $js }
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

# =====================================================================
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

    set jacs {}; set warps {}; set asps {}
    set neg 0; set n 0; set i 0
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
        set lmin 1e30; set lmax 0.0
        foreach ed $edges {
            set l [yhs_len [yhs_sub [lindex $P [lindex $ed 1]] [lindex $P [lindex $ed 0]]]]
            if {$l < $lmin} { set lmin $l }
            if {$l > $lmax} { set lmax $l }
        }
        set asp [expr {$lmin > 1e-12 ? $lmax / $lmin : 1e6}]
        lappend jacs $j; lappend warps $wmax; lappend asps $asp
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
    return $R
}

# =====================================================================
# =====================================================================
if {![info exists ::YL_BATCH]} { set ::YL_BATCH 0 }
set ::YL_RESULT [list status "FAIL" msg "not finished"]

set ELEM_SIZE        2.0
set TARGET_ASPECT    5.0
set MIN_JACOBIAN     0.50
set MAX_WARPAGE      20.0
set MAX_SKEW         60.0
set QUAD_MODE        "mixed"
set ABAQUS_ELEM_TYPE "C3D8H"
set DELETE_2D_SKIN   1
set QUAD_FALLBACK    1
set EXPORT_INP       0
set OUT_DIR          "C:/work/mesh_hex"
set BASE_NAME        "YL_H1"

if {[info exists ::YL_ELEM_SIZE] && $::YL_ELEM_SIZE > 0.05} { set ELEM_SIZE [expr {double($::YL_ELEM_SIZE)}] }
if {[info exists ::YL_ASPECT]}        { set TARGET_ASPECT    [expr {double($::YL_ASPECT)}] }
if {[info exists ::YL_MIN_JACOBIAN]}  { set MIN_JACOBIAN     [expr {double($::YL_MIN_JACOBIAN)}] }
if {[info exists ::YL_MAX_WARPAGE]}   { set MAX_WARPAGE      [expr {double($::YL_MAX_WARPAGE)}] }
if {[info exists ::YL_MAX_SKEW]}      { set MAX_SKEW         [expr {double($::YL_MAX_SKEW)}] }
if {[info exists ::YL_QUAD_MODE]}     { set QUAD_MODE        [string tolower [string trim $::YL_QUAD_MODE]] }
if {[info exists ::YL_ELEM_TYPE]}     { set ABAQUS_ELEM_TYPE [string toupper [string trim $::YL_ELEM_TYPE]] }
if {[info exists ::YL_QUAD_FALLBACK]} { set QUAD_FALLBACK    $::YL_QUAD_FALLBACK }
if {[info exists ::YL_EXPORT_INP]}    { set EXPORT_INP       $::YL_EXPORT_INP }
if {[info exists ::YL_OUT_DIR]}       { set OUT_DIR          $::YL_OUT_DIR }
if {[info exists ::YL_BASE_NAME]}     { set BASE_NAME        $::YL_BASE_NAME }
file mkdir $OUT_DIR

set EQUIV_GLOBAL [expr {[info exists ::env(YL_EQUIV_GLOBAL)] && [string trim $::env(YL_EQUIV_GLOBAL)] eq "1"}]


# ---------------- helpers ----------------
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
            if {[regexp -nocase $pat $txt -> cfg typ]} {
                return [list $cfg $typ]
            }
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

# ---------------- 1. Check solids ----------------
*clearmark solids 1; *createmark solids 1 "all"
set solid_ids [hm_getmark solids 1]
*clearmark solids 1

*clearmark surfs 1; *createmark surfs 1 "all"
set surf_ids [hm_getmark surfs 1]
*clearmark surfs 1

set n_solids [llength $solid_ids]
set n_surfs  [llength $surf_ids]

if {$n_solids == 0} {
    set ::YL_RESULT [list status "FAIL" engine "H1" mesher "SolidMap" size $ELEM_SIZE etype $ABAQUS_ELEM_TYPE \
        solids 0 unmap_solids 0 surfs $n_surfs pass_hex 0 fallback_h2 $QUAD_FALLBACK \
        msg "No solids found (surfs=$n_surfs). A closed solid CAD model is required."]
    ylhm_show
    return
}

ylh_delete_elems {tria3 tria6 quad4 quad8 tetra4 tetra10 hex8 hex20 penta6 penta15 pyramid5 pyramid13}
catch { *elementorder 1 }

# ---------------- 2. Multi-Solid Map ----------------
set sm_flags [expr {$QUAD_MODE eq "quads" ? 131072 : 131074}]
if {[info exists ::env(YL_SOLIDMAP_FLAGS)] && $::env(YL_SOLIDMAP_FLAGS) ne ""} {
    set sm_flags $::env(YL_SOLIDMAP_FLAGS)
}

set sm_err ""
*clearmark solids 1
eval *createmark solids 1 $solid_ids
if {[catch {
    *solidmap_solids_begin 1 $sm_flags $ELEM_SIZE
    *solidmap_solids_end
} err1]} {
    *clearmark solids 1
    eval *createmark solids 1 $solid_ids
    if {[catch {
        *solidmap_solids_begin 1 0 $ELEM_SIZE
        *solidmap_solids_end
    } err2]} {
        set sm_err "solidmap failed: $err1 / $err2"
    }
}
*clearmark solids 1

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
    set unmap_check_ok 0
    set n_unmap -1
    set unmap_ids {}
}

# ---------------- 3. Interior node smoothing (topology preserving) ----------------
set n_smoothed 0
set smooth_err ""
if {$n_hex > 0} {
    if {[catch { set n_smoothed [hqc::smooth_hex $MIN_JACOBIAN 5] } e]} { set smooth_err "smoothing error: $e" }
}

# ---------------- 4. 3D quality check (first-order elements) ----------------
set bad_jac -1; set bad_warp -1; set bad_asp -1; set bad_skew -1
if {$n_hex + $n_penta > 0} {
    foreach {var cmd lim lbl} [list \
        bad_jac  *elementtestjacobian $MIN_JACOBIAN "3D Jacobian" \
        bad_warp *elementtestwarpage  $MAX_WARPAGE  "3D Warpage" \
        bad_asp  *elementtestaspect   $TARGET_ASPECT "3D Aspect" \
        bad_skew *elementtestskew     $MAX_SKEW     "3D Skew"] {
        *clearmark elems 1; *clearmark elems 2
        *createmark elems 1 "by config" hex8 penta6
        if {![catch { $cmd elems 1 $lim 2 2 0 $lbl }]} {
            set $var [llength [hm_getmark elems 2]]
        }
        *clearmark elems 1; *clearmark elems 2
    }
}

array unset ::YHX
*clearmark elems 1
*createmark elems 1 "by config" hex8
set hex_eids [hm_getmark elems 1]
*clearmark elems 1
set HS [yhs_hex_stats $hex_eids 300000]
array unset ::YHX

proc ylh_fmt {d key fmt} { if {[dict exists $d $key]} { return [format $fmt [dict get $d $key]] }; return "" }
set neg_jac  [dict get $HS neg_jac]
set jac_min  [ylh_fmt $HS jac_min  %.3f]
set jac_mean [ylh_fmt $HS jac_mean %.3f]
set warp_max [ylh_fmt $HS warp_max %.2f]
set asp_p95  [ylh_fmt $HS asp_p95  %.2f]

# ---------------- 5. Abaqus element order / type ----------------
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
        if {[llength [hm_getmark elems 1]] > 0} {
            catch { *orderchangetosecond 1 1 }
        }
        *clearmark elems 1
        if {$n_hex > 0 && [ylh_count_elems {hex20}] == 0} {
            set type_err "2nd-order (Hex20) conversion failed: requested '$ABAQUS_ELEM_TYPE' but elements are still Hex8"
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
        set type_err "Element type mismatch: actual='$assigned_hex_type', requested='$ABAQUS_ELEM_TYPE'"
    }
}

set n_hex   [ylh_count_elems {hex8 hex20}]
set n_penta [ylh_count_elems {penta6 penta15}]

# ---------------- 6. Pass/fail, save, INP ----------------
set pass_hex [expr {$sm_err eq "" && $type_err eq "" && $n_hex > 0 && $n_unmap == 0 && $neg_jac == 0 && $bad_jac == 0 && $bad_warp == 0 && $bad_asp == 0 && $bad_skew == 0 ? 1 : 0}]
set fallback_h2 [expr {($n_hex == 0 || $n_unmap > 0) && $QUAD_FALLBACK ? 1 : 0}]

set out_stem "${BASE_NAME}_HEX_${ABAQUS_ELEM_TYPE}_${ELEM_SIZE}mm"
if {[info exists ::YL_OUT_NAME] && [string trim $::YL_OUT_NAME] ne ""} { set out_stem [string trim $::YL_OUT_NAME] }
set out_hm "$OUT_DIR/${out_stem}.hm"
set out_inp ""
set inp_err ""

catch { hm_answernext yes }
set write_failed [catch { *writefile "$out_hm" 1 } write_err]

set inp_note ""
if {$EXPORT_INP} {
    if {$n_hex > 0 && $neg_jac == 0 && $type_err eq ""} {
        set out_inp "$OUT_DIR/${out_stem}.inp"
        catch { file delete $out_inp }
        catch { hm_answernext yes }
        if {[catch {
            *createstringarray 2 "HMBOMCOMMENTS_XML" "EXPORTIDS_SKIP"
            *feoutputwithdata $tpl_path $out_inp 0 0 2 1 2
        } inp_err]} { set inp_err "INP EXPORT FAILED: $inp_err" } else { set inp_err "" }
        if {$inp_err eq "" && ![file exists $out_inp]} { set inp_err "INP EXPORT FAILED: file was not created ($out_inp)" }
        if {$inp_err ne ""} { set out_inp "" }
    } else {
        set inp_note "INP skipped: no hex / negative Jacobian / element type error"
    }
}

set unmap_txt [expr {$unmap_check_ok ? [join [lrange $unmap_ids 0 9] ","] : "unknown"}]
set msg [format "YL_H1 (Multi-Solid Map Hexa) done\n- Size: %.2f mm, Type: %s (actual: %s), QuadMode: %s, Equivalence: %s\n- Solids: %d (Unmappable: %d -> %s)\n- Elements: Hex=%d, Penta=%d (Smoothed interior nodes: %d)\n- Quality(HM): BadJac(<%.2f)=%d, BadWarp(>%.1f)=%d, BadAsp(>%.1f)=%d, BadSkew(>%.1f)=%d\n- Stats(Scaled Jacobian): NegJac=%d, SJ min=%s mean=%s, Warp max=%s, Asp p95=%s\n- Saved: %s" \
    $ELEM_SIZE $ABAQUS_ELEM_TYPE $assigned_hex_type $QUAD_MODE $equiv_mode \
    $n_solids $n_unmap $unmap_txt \
    $n_hex $n_penta $n_smoothed \
    $MIN_JACOBIAN $bad_jac $MAX_WARPAGE $bad_warp $TARGET_ASPECT $bad_asp $MAX_SKEW $bad_skew \
    $neg_jac $jac_min $jac_mean $warp_max $asp_p95 $out_hm]
if {$smooth_err ne ""} { append msg "\n- WARN: $smooth_err" }
if {$inp_note ne ""}   { append msg "\n- NOTE: $inp_note" }

set _status "OK"
set _why {}
if {$write_failed} { lappend _why "SAVE FAILED: $write_err" }
if {$sm_err ne ""} { lappend _why $sm_err }
if {$type_err ne ""} { lappend _why $type_err }
if {$inp_err ne ""} { lappend _why $inp_err }
if {$n_hex == 0} { lappend _why "NO HEX ELEMENTS CREATED (split solids into mappable pieces)" }
if {$n_unmap > 0} { lappend _why "$n_unmap SOLID(S) UNMAPPABLE (ID: [join [lrange $unmap_ids 0 9] ,])" }
if {!$unmap_check_ok} { lappend _why "UNMAPPABLE CHECK FAILED (solid->element association lookup failed: check *findmark solids->elems)" }
if {$neg_jac > 0} { lappend _why "$neg_jac NEGATIVE JACOBIAN HEX(ES)" }
if {$bad_jac > 0} { lappend _why "$bad_jac HEX(ES) BELOW JACOBIAN $MIN_JACOBIAN" }
if {$bad_warp > 0} { lappend _why "$bad_warp HEX(ES) ABOVE WARPAGE $MAX_WARPAGE" }
if {$bad_asp > 0} { lappend _why "$bad_asp HEX(ES) ABOVE ASPECT $TARGET_ASPECT" }
if {$bad_skew > 0} { lappend _why "$bad_skew HEX(ES) ABOVE SKEW $MAX_SKEW" }
if {$n_hex + $n_penta > 0} {
    foreach {lbl v} [list Jacobian $bad_jac Warpage $bad_warp Aspect $bad_asp Skew $bad_skew] {
        if {$v < 0} { lappend _why "3D $lbl quality check command failed" }
    }
}
if {[llength $_why] > 0} {
    set _status "FAIL"
    foreach w $_why { append msg "\n*** $w" }
}

set ::YL_RESULT [list status $_status engine "H1" mesher "SolidMap" size $ELEM_SIZE etype $ABAQUS_ELEM_TYPE \
    solids $n_solids unmap_solids $n_unmap surfs $n_surfs n_hex $n_hex n_penta $n_penta n_quad 0 n_tria 0 \
    bad_jac $bad_jac bad_warp $bad_warp bad_asp $bad_asp bad_skew $bad_skew neg_jac $neg_jac \
    jac_min $jac_min jac_mean $jac_mean warp_max $warp_max asp_p95 $asp_p95 smoothed_nodes $n_smoothed \
    pass_hex $pass_hex fallback_h2 $fallback_h2 out $out_hm inp $out_inp msg $msg]

ylhm_show
