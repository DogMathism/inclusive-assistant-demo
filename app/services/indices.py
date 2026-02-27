def calc_overload_index(accuracy, norm_time, skip_rate, sensory_mismatch):
    return min(1.0, max(0.0, 0.4*(1-accuracy)+0.3*norm_time+0.2*skip_rate+0.1*sensory_mismatch))

def calc_readiness_index(accuracy, norm_time, fatigue_proxy):
    return min(1.0, max(0.0, 0.5*accuracy + 0.3*(1-norm_time) + 0.2*(1-fatigue_proxy)))