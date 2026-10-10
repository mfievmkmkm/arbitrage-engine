from app.recovery_rounder import choose
def test_rounder_matches_recovery_target_venue():
 a=lambda x:1;b=lambda x:2;assert choose("short","long",a,b)(0)==2
