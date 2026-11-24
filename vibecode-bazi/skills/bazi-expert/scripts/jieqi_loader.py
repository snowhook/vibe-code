import os
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional

JIE_DEGREES = [315, 345, 15, 45, 75, 105, 135, 165, 195, 225, 255, 285]
JIE_NAMES = ["立春","惊蛰","清明","立夏","芒种","小暑","立秋","白露","寒露","立冬","大雪","小寒"]
JIE_MAP = {d: n for d, n in zip(JIE_DEGREES, JIE_NAMES)}

_TABLE: Optional[Dict[str, List[Dict]]] = None

def load_jieqi_table() -> Optional[Dict[str, List[Dict]]]:
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    base = os.path.dirname(os.path.dirname(__file__))
    p = os.path.join(base, "references", "jieqi_1900_2100.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            _TABLE = json.load(f)
            return _TABLE
    return None

def _events_for_year(y: int) -> List[Tuple[datetime, str, int]]:
    tbl = load_jieqi_table()
    if not tbl:
        return []
    key = str(y)
    if key not in tbl:
        return []
    items: List[Tuple[datetime, str, int]] = []
    for it in tbl[key]:
        t = datetime.fromisoformat(it["iso"])  # UTC ISO
        items.append((t, it["name"], it["degree"]))
    return items

def scan_events(dt_utc: datetime) -> List[Tuple[datetime, str, int]]:
    ev: List[Tuple[datetime, str, int]] = []
    ev.extend(_events_for_year(dt_utc.year - 1))
    ev.extend(_events_for_year(dt_utc.year))
    ev.extend(_events_for_year(dt_utc.year + 1))
    ev.sort(key=lambda x: x[0])
    return ev

