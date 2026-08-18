"""
VIP level progression.

New users start at VIP 0 (no badge shown at all). A user's VIP level rises
based on how many of the people THEY directly invited have made at least
one successful deposit — not just signed up. That count is tracked in
users.depositing_invites, incremented once per distinct invitee's first
successful deposit (routes/deposit.py), and recomputed from scratch here
any time it changes so it's always derived consistently from that count.

Thresholds below are a starting point — adjust freely, or override any
individual user's vip_level directly from the manager dashboard (Users tab)
if you want to hand-place someone regardless of this formula.
"""

VIP_THRESHOLDS = [
    (0,  0),   # VIP 0 — default, no depositing invites yet
    (1,  1),   # VIP 1 — 1+ direct invite has deposited
    (2,  3),
    (3,  6),
    (4,  10),
    (5,  20),
    (6,  35),
    (7,  50),
]


def vip_level_for(depositing_invites):
    """Highest VIP level whose threshold is met by depositing_invites."""
    level = 0
    for lvl, needed in VIP_THRESHOLDS:
        if depositing_invites >= needed:
            level = lvl
    return level


def recompute_vip_level(db, user_id):
    """Re-derive and persist vip_level from the user's current
    depositing_invites count. Call after depositing_invites changes."""
    row = db.execute("SELECT depositing_invites FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return
    new_level = vip_level_for(row['depositing_invites'] or 0)
    db.execute("UPDATE users SET vip_level=? WHERE id=?", (new_level, user_id))
    
