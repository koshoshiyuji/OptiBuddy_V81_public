

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _container(cid, yard=None, ship=None, order=None, weight=0, attrs=None):
    return {
        "id": cid, "yard": yard or {}, "ship": ship or {},
        "order": order, "weight": weight, "attrs": attrs or [],
    }
