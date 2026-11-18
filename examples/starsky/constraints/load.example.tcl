# In the TOP DC flow, after link and creation of upstream clocks:
# Replace with the real instance path; stay in top design / instance scope.
set PLM_INSTANCE u_soc/u_pipe_lane_mapper
source [file join [file dirname [info script]] pipe_lane_mapper.sdc]
# Keep mode dynamic for synthesis. Apply fixed-mode case analysis only in
# separate top STA scenarios; do not compile away other operating modes.
