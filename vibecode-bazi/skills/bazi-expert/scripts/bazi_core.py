import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pytz
import ephem
from jieqi_loader import load_jieqi_table, scan_events, JIE_MAP

DEFAULT_TZ = 'Asia/Shanghai'
USE_TRUE_SOLAR_TIME_DEFAULT = True
REFINE_MINUTES = 1

def to_utc(dt_local: datetime, tz_name: str):
    tz = pytz.timezone(tz_name)
    if dt_local.tzinfo is None:
        dt_local = tz.localize(dt_local)
    else:
        dt_local = dt_local.astimezone(tz)
    return dt_local.astimezone(pytz.UTC)

def from_utc(dt_utc: datetime, tz_name: str):
    tz = pytz.timezone(tz_name)
    return dt_utc.astimezone(tz)

def china_dst_adjust(dt_local: datetime) -> datetime:
    y = dt_local.year
    if 1986 <= y <= 1991:
        m = dt_local.month
        d = dt_local.day
        if (m > 4 and m < 9) or (m == 4 and d >= 15) or (m == 9 and d <= 15):
            return dt_local - timedelta(hours=1)
    return dt_local

def ecliptic_longitude_deg(date_utc: datetime) -> float:
    observer = ephem.Observer()
    observer.date = date_utc
    sun = ephem.Sun(observer)
    equ = ephem.Equatorial(sun.ra, sun.dec, epoch=observer.date)
    ecl = ephem.Ecliptic(equ)
    lon = float(ecl.lon) * 180.0 / math.pi
    return lon % 360.0

def next_zhongqi_after(dt_utc: datetime, target_deg: Optional[float] = None):
    lon0 = ecliptic_longitude_deg(dt_utc)
    if target_deg is None:
        k = math.floor(lon0 / 30.0) + 1
        target = (k * 30.0) % 360.0
        if target == 0 and lon0 < 1e-6:
            target = 30.0
    else:
        target = target_deg % 360.0
    t1 = dt_utc
    step = timedelta(hours=6)
    lon_prev = ecliptic_longitude_deg(t1)
    while True:
        t2 = t1 + step
        lon2 = ecliptic_longitude_deg(t2)
        if ((lon_prev - target) % 360.0) > ((lon2 - target) % 360.0):
            break
        t1, lon_prev = t2, lon2
    a, b = t1, t2
    while (b - a) > timedelta(minutes=REFINE_MINUTES):
        m = a + (b - a) / 2
        l_a = ecliptic_longitude_deg(a)
        l_m = ecliptic_longitude_deg(m)
        if ((l_a - target) % 360.0) > ((l_m - target) % 360.0):
            b = m
        else:
            a = m
    return b, target

def true_solar_time(dt_local: datetime, lon_deg: float) -> datetime:
    if dt_local.tzinfo is None:
        raise ValueError("dt_local must be timezone-aware for true solar time")
    offset_hours = dt_local.utcoffset().total_seconds() / 3600.0
    lon_corr_hours = lon_deg / 15.0 - offset_hours
    base = dt_local.replace(hour=12, minute=0, second=0, microsecond=0)
    best_t = base
    best_alt = -1e9
    observer = ephem.Observer()
    observer.lat = '0'
    observer.lon = str(lon_deg)
    for m in range(-90, 91, 3):
        t = base + timedelta(minutes=m)
        observer.date = t.astimezone(pytz.UTC)
        sun = ephem.Sun(observer)
        alt = float(sun.alt)
        if alt > best_alt:
            best_alt = alt
            best_t = t
    eot_minutes = (best_t - base).total_seconds() / 60.0
    return dt_local + timedelta(hours=lon_corr_hours, minutes=eot_minutes)

HEAVENLY_STEMS = "甲乙丙丁戊己庚辛壬癸"
EARTHLY_BRANCHES = "子丑寅卯辰巳午未申酉戌亥"

def sexagenary(n: int) -> str:
    return HEAVENLY_STEMS[n % 10] + EARTHLY_BRANCHES[n % 12]

def gan_zhi_day(dt_utc: datetime) -> str:
    anchor = datetime(1986, 9, 17, 0, 0, tzinfo=pytz.UTC)
    diff_days = int((dt_utc - anchor).total_seconds() // 86400)
    return sexagenary(diff_days % 60)

FIVE_TIGERS_START = {
    "甲": "丙", "己": "丙",
    "乙": "戊", "庚": "戊",
    "丙": "庚", "辛": "庚",
    "丁": "壬", "壬": "壬",
    "戊": "甲", "癸": "甲",
}

def gan_zhi_month_from_year(year_gz: str, month_no_1_to_12: int) -> str:
    year_stem = year_gz[0]
    start_stem = FIVE_TIGERS_START[year_stem]
    start_idx = HEAVENLY_STEMS.index(start_stem)
    stem = HEAVENLY_STEMS[(start_idx + (month_no_1_to_12 - 1)) % 10]
    branches_order = "寅卯辰巳午未申酉戌亥子丑"
    branch = branches_order[(month_no_1_to_12 - 1) % 12]
    return stem + branch

def gan_zhi_year_from_lichun_local(local_tst: datetime, tz_name: str, lon: float) -> str:
    y = local_tst.year
    probe = datetime(y - 1, 11, 1, tzinfo=pytz.UTC)
    lichun_utc, _ = next_zhongqi_after(probe, target_deg=315.0)
    lichun_local_std = from_utc(lichun_utc, tz_name)
    lichun_local_tst = true_solar_time(lichun_local_std, lon)
    base_year = 1984
    if local_tst >= lichun_local_tst:
        offset = y - base_year
    else:
        offset = (y - 1) - base_year
    return sexagenary(offset % 60)

def next_new_moon(dt_utc: datetime):
    return ephem.next_new_moon(dt_utc)

def prev_new_moon(dt_utc: datetime):
    return ephem.previous_new_moon(dt_utc)

def floor_days(delta: timedelta) -> int:
    return int(delta.total_seconds() // 86400)

@dataclass
class LunarMonth:
    start_utc: datetime
    end_utc: datetime
    has_zhongqi: bool
    month_no: Optional[int] = None
    is_leap: bool = False

def build_lunar_months_around(dt_utc: datetime, tz_name: str):
    year = from_utc(dt_utc, tz_name).year
    approx_dec = datetime(year, 12, 1, 0, 0, tzinfo=pytz.UTC) - timedelta(days=60)
    ws_utc, _ = next_zhongqi_after(approx_dec, target_deg=270.0)
    if ws_utc > dt_utc:
        approx_dec = datetime(year - 1, 12, 1, 0, 0, tzinfo=pytz.UTC) - timedelta(days=60)
        ws_utc, _ = next_zhongqi_after(approx_dec, target_deg=270.0)
    start_nm = prev_new_moon(ws_utc)
    months = []
    cur_start = start_nm.datetime()
    if cur_start.tzinfo is None:
        cur_start = cur_start.replace(tzinfo=pytz.UTC)
    while len(months) < 16:
        nxt = next_new_moon(cur_start)
        cur_end = nxt.datetime()
        if cur_end.tzinfo is None:
            cur_end = cur_end.replace(tzinfo=pytz.UTC)
        has_zq = False
        zq_time, deg = next_zhongqi_after(cur_start)
        if cur_start <= zq_time < cur_end:
            has_zq = True
        months.append(LunarMonth(start_utc=cur_start, end_utc=cur_end, has_zhongqi=has_zq))
        cur_start = cur_end
        if cur_end > dt_utc + timedelta(days=370):
            break
    ws_idx = None
    for i, m in enumerate(months):
        if m.start_utc <= ws_utc < m.end_utc:
            ws_idx = i
            break
    if ws_idx is None:
        raise RuntimeError("Failed to bracket winter solstice.")
    # ---- 月序编排（历书规则）----
    # 规则1：含冬至的朔望月固定为十一月（大雪月）
    # 规则2：冬至之后**第一个无中气的朔望月**为闰月，月序**沿用前一个月**（不递增）
    #        ——2012 年 5/21-6/19 无中气，前月为四月，故是「闰四月」而非闰五月
    def _advance(n: int) -> int:
        return 12 if n == 11 else (1 if n == 12 else n + 1)

    def _retreat(n: int) -> int:
        return 12 if n == 11 else (11 if n == 12 else n - 1)

    months[ws_idx].month_no = 11
    cur_no = 11
    leap_used = False
    next_ws_utc, _ = next_zhongqi_after(ws_utc + timedelta(days=300), target_deg=270.0)
    for i in range(ws_idx + 1, len(months)):
        m = months[i]
        if m.start_utc <= next_ws_utc < m.end_utc:      # 进入新的冬至循环
            m.month_no = 11
            cur_no, leap_used, next_ws_utc = 11, False, next_zhongqi_after(next_ws_utc + timedelta(days=300), target_deg=270.0)[0]
            continue
        if (not m.has_zhongqi) and (not leap_used):
            m.is_leap = True
            m.month_no = cur_no                          # 沿用前月月序
            leap_used = True
        else:
            cur_no = _advance(cur_no)
            m.month_no = cur_no
    # 反向：冬至月之前的月份（闰月在前、名月在后，故无中气月与它**前面**那个月同号）
    cur_no = 11
    skip_decrement = False
    for i in range(ws_idx - 1, -1, -1):
        m = months[i]
        if skip_decrement:
            m.month_no = cur_no
            skip_decrement = False
            continue
        if not m.has_zhongqi:
            m.is_leap = True
            cur_no = _retreat(cur_no)
            m.month_no = cur_no
            skip_decrement = True                        # 名月与闰月同号
        else:
            cur_no = _retreat(cur_no)
            m.month_no = cur_no
    return months, ws_utc

def get_solar_month_by_jieqi_tst(local_tst: datetime, tz_name: str, lon: float) -> int:
    tbl = load_jieqi_table()
    if tbl:
        dt_utc = to_utc(local_tst, tz_name)
        ev = scan_events(dt_utc)
        prev_deg = None
        for t, n, d in ev:
            t_std = from_utc(t, tz_name)
            t_tst = true_solar_time(t_std, lon)
            if t_tst <= local_tst:
                prev_deg = d
            else:
                break
        if prev_deg is None:
            prev_deg = ev[0][2]
        mapping = {
            315: 1, 345: 2, 15: 3, 45: 4, 75: 5, 105: 6,
            135: 7, 165: 8, 195: 9, 225: 10, 255: 11, 285: 12
        }
        return mapping[prev_deg % 360]
    # fallback to ephem
    jieqi_degrees = [315, 345, 15, 45, 75, 105, 135, 165, 195, 225, 255, 285]
    jieqi_months = [1,2,3,4,5,6,7,8,9,10,11,12]
    dt_utc = to_utc(local_tst, tz_name)
    probe = datetime(dt_utc.year - 1, 11, 1, tzinfo=pytz.UTC)
    latest_jieqi_time = None
    current_month = 12
    for deg, _m in zip(jieqi_degrees, jieqi_months):
        t_utc, _ = next_zhongqi_after(probe, target_deg=deg)
        t_std = from_utc(t_utc, tz_name)
        t_tst = true_solar_time(t_std, lon)
        if t_tst <= local_tst and (latest_jieqi_time is None or t_utc > latest_jieqi_time):
            latest_jieqi_time = t_utc
            current_month = _m
    return current_month

def locate_lunar_date(dt_local: datetime, lat: float, lon: float, tz_name: str = DEFAULT_TZ,
                      use_true_solar_time: bool = USE_TRUE_SOLAR_TIME_DEFAULT, gender: str = None) -> Dict[str, Any]:
    dt_local = china_dst_adjust(dt_local)
    dt_utc = to_utc(dt_local, tz_name)
    months, ws_utc = build_lunar_months_around(dt_utc, tz_name)
    idx = None
    for i, m in enumerate(months):
        if m.start_utc <= dt_utc < m.end_utc:
            idx = i
            cur_m = m
            break
    if idx is None:
        raise RuntimeError("Failed to locate lunar month.")
    # 历书口径：朔发生的整个自然日（东八区）= 初一，按日历天计数
    day = (from_utc(dt_utc, tz_name).date() - from_utc(cur_m.start_utc, tz_name).date()).days + 1
    local_dt = from_utc(dt_utc, tz_name)
    if use_true_solar_time:
        local_dt = true_solar_time(local_dt, lon)
    gz_year = gan_zhi_year_from_lichun_local(local_dt, tz_name, lon)
    solar_month = get_solar_month_by_jieqi_tst(local_dt, tz_name, lon)
    gz_month = gan_zhi_month_from_year(gz_year, solar_month)
    day_date = local_dt.date()
    if 23 <= local_dt.hour <= 23:
        day_date = (day_date + timedelta(days=1))
    local_date_utc_midnight = datetime(day_date.year, day_date.month, day_date.day, 0, 0, tzinfo=pytz.UTC)
    gz_day_val = gan_zhi_day(local_date_utc_midnight)
    hour = local_dt.hour
    minute = local_dt.minute
    shichen_idx = ((hour + 1) // 2) % 12
    shichen_names = "子丑寅卯辰巳午未申酉戌亥"
    shichen = shichen_names[shichen_idx]
    day_stem_idx = HEAVENLY_STEMS.index(gz_day_val[0])
    stem_idx = (day_stem_idx * 2 + shichen_idx) % 10
    gz_hour = HEAVENLY_STEMS[stem_idx] + shichen
    tbl = load_jieqi_table()
    if tbl:
        ev = scan_events(to_utc(local_dt, tz_name))
        prev = None
        nxt = None
        for t, n, d in ev:
            t_std = from_utc(t, tz_name)
            t_tst = true_solar_time(t_std, lon)
            if t_tst <= local_dt:
                prev = (t, d)
            else:
                nxt = (t, d)
                break
        if prev is None:
            prev = (ev[0][0], ev[0][2])
        if nxt is None:
            nxt = (ev[-1][0], ev[-1][2])
        current_jieqi_time = prev[0]
        next_time = nxt[0]
        current_jieqi_name = JIE_MAP.get(prev[1] % 360, "立春")
        next_jieqi_name = JIE_MAP.get(nxt[1] % 360, "惊蛰")
    else:
        prev_time, prev_deg = next_zhongqi_after(datetime(local_dt.year - 1, 11, 1, tzinfo=pytz.UTC), target_deg=315.0)
        current_jieqi_time = prev_time
        next_time, next_deg = next_zhongqi_after(prev_time)
        current_jieqi_name = JIE_MAP.get(315, "立春")
        next_jieqi_name = JIE_MAP.get(next_deg % 360, "惊蛰")
    result = {
        "input": {
            "gregorian_local": local_dt.strftime("%Y-%m-%d %H:%M"),
            "timezone": tz_name,
            "lat": lat,
            "lon": lon,
            "true_solar_time": use_true_solar_time
        },
        "lunar": {
            "date": {
                "month_no": cur_m.month_no,
                "month_cn": ("闰" if cur_m.is_leap else "") + ["正","二","三","四","五","六","七","八","九","十","十一","十二"][cur_m.month_no-1] + "月",
                "is_leap_month": cur_m.is_leap,
                "day": day,
                "day_cn": "",
                "shichen": shichen,
                "minute": minute
            },
            "gan_zhi": {
                "year": gz_year,
                "month": gz_month,
                "day": gz_day_val,
                "hour": gz_hour,
                "four_pillars": f"{gz_year}年 {gz_month}月 {gz_day_val}日 {gz_hour}时"
            },
            "jieqi": {
                "current_jieqi": current_jieqi_name,
                "current_jieqi_time_utc": current_jieqi_time.strftime("%Y-%m-%d %H:%M"),
                "current_jieqi_time_local": true_solar_time(from_utc(current_jieqi_time, tz_name), lon).strftime("%Y-%m-%d %H:%M"),
                "next_jieqi": next_jieqi_name,
                "next_jieqi_time_utc": next_time.strftime("%Y-%m-%d %H:%M"),
                "next_jieqi_time_local": true_solar_time(from_utc(next_time, tz_name), lon).strftime("%Y-%m-%d %H:%M")
            },
            "anchors": {
                "prev_new_moon_utc": months[idx].start_utc.strftime("%Y-%m-%d %H:%M"),
                "next_new_moon_utc": months[idx].end_utc.strftime("%Y-%m-%d %H:%M"),
                "winter_solstice_utc": ws_utc.strftime("%Y-%m-%d %H:%M")
            }
        }
    }
    if gender:
        try:
            dy = calculate_dayun(dt_utc, tz_name, gender, gz_year, gz_month, lon)
            result["dayun"] = dy
        except Exception:
            pass
    return result

def calculate_dayun(birth_dt_utc: datetime, tz_name: str, gender: str, year_gz: str, month_gz: str, lon: float) -> Dict:
    year_stem = year_gz[0]
    yang_stems = "甲丙戊庚壬"
    is_yang_year = year_stem in yang_stems
    is_male = (gender == '男')
    shun_pai = (is_yang_year and is_male) or (not is_yang_year and not is_male)
    try:
        tbl = load_jieqi_table()
        local_birth_std = from_utc(birth_dt_utc, tz_name)
        local_birth = true_solar_time(local_birth_std, lon)
        if tbl:
            evs = scan_events(birth_dt_utc)
            prev = None
            nxt = None
            for t, n, d in evs:
                t_std = from_utc(t, tz_name)
                t_tst = true_solar_time(t_std, lon)
                if t_tst <= local_birth:
                    prev = t_tst
                else:
                    nxt = t_tst
                    break
            target_tst = nxt if shun_pai else prev
            minutes_diff = abs((target_tst - local_birth).total_seconds() / 60.0)
            hours_diff = minutes_diff / 60.0
            days_float = minutes_diff / 1440.0
        else:
            jie_degrees = [315, 345, 15, 45, 75, 105, 135, 165, 195, 225, 255, 285]
            probe = datetime(birth_dt_utc.year - 1, 11, 1, tzinfo=pytz.UTC)
            events = []
            for d in jie_degrees:
                t, _ = next_zhongqi_after(probe, target_deg=d)
                events.append(t)
                probe = t
            events.sort()
            prev_evt = max([e for e in events if e <= birth_dt_utc], default=None)
            next_evt = min([e for e in events if e >= birth_dt_utc], default=None)
            target_time = next_evt if shun_pai else prev_evt
            local_target_std = from_utc(target_time, tz_name)
            local_target = true_solar_time(local_target_std, lon)
            minutes_diff = abs((local_target - local_birth).total_seconds() / 60.0)
            hours_diff = minutes_diff / 60.0
            days_float = minutes_diff / 1440.0
    except Exception:
        days_float = 15.0
        hours_diff = 360.0
        minutes_diff = hours_diff * 60.0
    if days_float > 33.0:
        days_float = 33.0 - 1e-6
    years_float = days_float / 3.0
    y = int(math.floor(years_float))
    rem_years = years_float - y
    m = int(math.floor(rem_years * 12.0))
    rem_months = rem_years * 12.0 - m
    d = int(math.floor(rem_months * 30.0))
    month_stem = month_gz[0]
    month_branch = month_gz[1]
    ms_i = HEAVENLY_STEMS.index(month_stem)
    mb_i = EARTHLY_BRANCHES.index(month_branch)
    for i in range(60):
        if i % 10 == ms_i and i % 12 == mb_i:
            month_idx60 = i
            break
    birth_local = from_utc(birth_dt_utc, tz_name)
    birth_year = birth_local.year
    birth_month = birth_local.month
    carry_years = (birth_month - 1 + m) // 12
    base_start_year = birth_year + y + carry_years
    xu_start_age = (base_start_year - birth_year) + 1
    dayun_list = []
    if base_start_year > birth_year:
        tz = pytz.timezone(tz_name)
        liu = []
        for yy in range(birth_year, base_start_year):
            d_local = tz.localize(datetime(yy, 6, 1, 12, 0))
            d_tst = true_solar_time(d_local, lon)
            gz_y = gan_zhi_year_from_lichun_local(d_tst, tz_name, lon)
            liu.append({
                "year": yy,
                "gan_zhi": gz_y,
                "age_xu": (yy - birth_year) + 1
            })
        dayun_list.append({
            "gan_zhi": "小运",
            "start_age": 0,
            "end_age": y,
            "age_range": f"{0}岁-{y}岁",
            "start_age_xu": 1,
            "end_age_xu": xu_start_age - 1,
            "age_range_xu": f"虚岁{1}-虚岁{xu_start_age - 1}",
            "year_range": f"{birth_year}-{base_start_year - 1}",
            "liu_nian": liu
        })
    for i in range(10):
        idx = (month_idx60 + i + 1) % 60 if shun_pai else (month_idx60 - i - 1) % 60
        gz = HEAVENLY_STEMS[idx % 10] + EARTHLY_BRANCHES[idx % 12]
        sa = y + i * 10
        ea = sa + 10
        sy = base_start_year + i * 10
        ey = base_start_year + (i + 1) * 10
        dayun_list.append({
            "gan_zhi": gz,
            "start_age": sa,
            "end_age": ea,
            "age_range": f"{sa}岁-{ea}岁",
            "start_age_xu": xu_start_age + i * 10,
            "end_age_xu": xu_start_age + (i + 1) * 10,
            "age_range_xu": f"虚岁{xu_start_age + i * 10}-虚岁{xu_start_age + (i + 1) * 10}",
            "year_range": f"{sy}-{ey}"
        })
    return {
        "start_age_years": y,
        "start_age_months": m,
        "start_age_description": f"{y}岁{m}个月{d}天",
        "start_age_detail": {
            "years": y,
            "months": m,
            "days": d,
            "precise_minutes_to_jie": round(minutes_diff, 2),
            "exact_days_float": round(days_float, 6)
        },
        "direction": "顺排" if shun_pai else "逆排",
        "is_yang_year": is_yang_year,
        "dayun_list": dayun_list,
        "calculation_info": {
            "is_yang_year": is_yang_year,
            "is_male": is_male,
            "shun_pai": shun_pai,
            "hours_to_jie": round(hours_diff, 2),
            "days_equivalent": days_float,
            "total_months": round(years_float * 12.0, 6),
            "conversion_rule": "文本口径+虚岁: 年=floor(D/3), 月=floor(frac_year*12), 天=floor(frac_month*30); 年份与年龄展示采用虚岁"
        }
    }
