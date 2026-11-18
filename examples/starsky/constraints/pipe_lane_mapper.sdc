# ORIGINAL PIPE / PHY-output PCLK / NO DPR.
# Source in TOP after link and top clock creation; remain at top scope.
# Top owns root periods, uncertainty, latency, clock relationships and IO timing.
if {![info exists PLM_INSTANCE] || $PLM_INSTANCE eq ""} {
    error "Set PLM_INSTANCE to the hierarchical pipe_lane_mapper instance path"
}
namespace eval ::plm_subdesign {}
set ::plm_subdesign::instance $PLM_INSTANCE
proc ::plm_subdesign::pin {name} {
    variable instance
    set pins [get_pins ${instance}/${name}]
    if {[sizeof_collection $pins] != 1} { error "Expected one pin: ${instance}/${name}" }
    return $pins
}
proc ::plm_subdesign::forward {src_name dst_name tag} {
    variable instance
    set src [pin $src_name]
    set dst [pin $dst_name]
    set masters [get_clocks -of_objects $src]
    if {[sizeof_collection $masters] == 0} {
        error "No upstream clock reaches ${instance}/${src_name}; load top clocks first"
    }
    set names {}
    set i 0
    set prefix [string map {/ __ [ _ ] _ . _} $instance]
    foreach_in_collection master $masters {
        set name ${prefix}__${tag}__${i}
        create_generated_clock -name $name -source $src \
            -master_clock [get_object_name $master] -divide_by 1 -add $dst
        lappend names $name
        incr i
    }
    return $names
}
# Each controller uses a fixed PHY lane source, even when inactive in a mode.
foreach {ctrl source_lane} {0 0 1 2 2 4 3 6} {
    ::plm_subdesign::forward [format {phy_pclk_out[%d]} $source_lane] \
        pcie${ctrl}_pclk ctrl${ctrl}
}
set ::plm_subdesign::routes {
    {0 0} {1 0} {2 0 2} {3 0 2}
    {4 0 4} {5 0 4} {6 0 4 6} {7 0 4 6}
}
foreach route $::plm_subdesign::routes {
    set lane [lindex $route 0]
    set groups {}
    foreach source_lane [lrange $route 1 end] {
        set clocks [::plm_subdesign::forward \
            [format {phy_pclk_out[%d]} $source_lane] \
            [format {phy_pclk_in[%d]} $lane] phy${lane}_from${source_lane}]
        lappend groups -group [get_clocks $clocks]
    }
    # Only alternative owners on ONE output are logically exclusive.
    # Upstream source clocks are not declared exclusive/asynchronous here.
    if {[llength $groups] > 2} {
        eval set_clock_groups -logically_exclusive $groups
    }
}
# RESET: pcie{0..3}_rst_n -> phy_rst_n[7:0], combinational AND/OR.
# This original PIPE architecture has no perst_n or recovered RX clock ports.
# No local resettable flops, reset synchronizers or clock-gate latches exist.
# Therefore no local reset exceptions or reset clock definitions are needed.
# Do NOT blanket false-path through reset pins or case them to 1: that could
# hide top-level reset propagation and recovery/removal timing checks.
# Any justified async-reset exception belongs at actual receiving flop pins
# in the top reset methodology, not at this combinational subdesign boundary.
# No root clocks, IO/data budgets, mode case analysis or compile commands here.
