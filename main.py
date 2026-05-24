#!/usr/bin/env python3
"""WebUntis Timetable Comparison CLI.

Fetches your WebUntis timetable, compares it to a saved baseline,
and shows changes (cancellations, room swaps, teacher absences, etc.).
"""

import argparse, json, os, sys, traceback
from dataclasses import dataclass, asdict
from datetime import datetime, date, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dotenv import load_dotenv
from tabulate import tabulate
from colorama import Fore, Back, Style, init
import webuntis

init(autoreset=True)

# ── Models ────────────────────────────────────────────────────────────────

class ChangeType(Enum):
    ROOM_CHANGE = "room_change"
    CANCELLATION = "cancellation"
    TIME_CHANGE = "time_change"
    TEACHER_CHANGE = "teacher_change"
    SUBJECT_CHANGE = "subject_change"
    NEW_CLASS = "new_class"
    HOLIDAY_OR_FREE_DAY = "holiday_or_free_day"

@dataclass
class Lesson:
    id: str
    subject: str
    start_time: time
    end_time: time
    room: str
    teacher: str
    date: date
    code: str = ''
    subst_text: str = ''

    def to_dict(self):
        return {"id": self.id, "subject": self.subject, "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat(), "room": self.room, "teacher": self.teacher,
                "date": self.date.isoformat(), "code": self.code, "subst_text": self.subst_text}

    @staticmethod
    def from_dict(d):
        return Lesson(id=d["id"], subject=d["subject"], start_time=time.fromisoformat(d["start_time"]),
                      end_time=time.fromisoformat(d["end_time"]), room=d["room"], teacher=d["teacher"],
                      date=date.fromisoformat(d["date"]), code=d.get("code", ""), subst_text=d.get("subst_text", ""))

    def __hash__(self):
        return hash((self.id, self.subject, self.start_time, self.end_time))

    def __eq__(self, other):
        if not isinstance(other, Lesson): return False
        return (self.subject == other.subject and self.start_time == other.start_time
                and self.end_time == other.end_time and self.date == other.date)

@dataclass
class Timetable:
    user: str
    lessons: List[Lesson]
    fetch_date: datetime
    start_date: date
    end_date: date

    def to_dict(self):
        return {"user": self.user, "lessons": [l.to_dict() for l in self.lessons],
                "fetch_date": self.fetch_date.isoformat(), "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat()}

    @staticmethod
    def from_dict(d):
        return Timetable(user=d["user"], lessons=[Lesson.from_dict(l) for l in d["lessons"]],
                         fetch_date=datetime.fromisoformat(d["fetch_date"]),
                         start_date=date.fromisoformat(d["start_date"]),
                         end_date=date.fromisoformat(d["end_date"]))

@dataclass
class Change:
    change_type: ChangeType
    lesson_old: Optional[Lesson]
    lesson_new: Optional[Lesson]
    description: str

# ── Config ────────────────────────────────────────────────────────────────

class Config:
    def __init__(self, env_path: Optional[Path] = None):
        if env_path is None:
            env_path = Path(__file__).parent / ".env"
        self.env_path = env_path
        self._load_env()

    def _load_env(self):
        if self.env_path.exists():
            load_dotenv(self.env_path)
        else:
            print(f"⚠️  .env not found at {self.env_path}")
            self._interactive_setup()

    def _interactive_setup(self):
        print("\n🔧 WebUntis Configuration Setup\n")
        server = input("Server (e.g. schuldorf.webuntis.com): ").strip()
        school = input("School name (e.g. schuldorf): ").strip()
        user = input("Username: ").strip()
        password = input("Password: ").strip()
        skip = input("Skip weekends? (Y/n): ").strip().lower()
        skip_line = "\nSKIP_WEEKENDS=true\n" if skip != "n" else ""
        with open(self.env_path, "w") as f:
            f.write(f"WEBUNTIS_SERVER={server}\nWEBUNTIS_SCHOOL={school}\n"
                    f"WEBUNTIS_USER={user}\nWEBUNTIS_PASSWORD={password}\n"
                    f"WEBUNTIS_DEFAULT_DAYS=1{skip_line}")
        print(f"\n✅ Saved to {self.env_path}")
        load_dotenv(self.env_path)

    def get_server(self) -> str:
        return os.getenv("WEBUNTIS_SERVER", "schuldorf.webuntis.com")

    def get_school(self) -> str:
        return os.getenv("WEBUNTIS_SCHOOL", "schuldorf")

    def get_user_creds(self, user_name: Optional[str] = None) -> Tuple[str, str]:
        if user_name:
            u = os.getenv(f"WEBUNTIS_USER_{user_name.upper()}")
            p = os.getenv(f"WEBUNTIS_PASSWORD_{user_name.upper()}")
            if u and p:
                return u, p
            raise ValueError(f"User '{user_name}' not found in .env")
        u = os.getenv("WEBUNTIS_USER")
        p = os.getenv("WEBUNTIS_PASSWORD")
        if not u or not p:
            raise ValueError("Default user not configured in .env")
        return u, p

    def get_default_days(self) -> int:
        return int(os.getenv("WEBUNTIS_DEFAULT_DAYS", "1"))

    def get_ignore_weekdays(self) -> List[int]:
        raw = os.getenv("IGNORE_DAYS", "")
        if raw:
            try:
                return [int(d.strip()) for d in raw.split(",") if d.strip()]
            except ValueError:
                pass
        if os.getenv("SKIP_WEEKENDS", "").lower() in ("true", "1", "yes"):
            return [5, 6]
        return []

    def get_all_users(self) -> Dict[str, Tuple[str, str]]:
        users = {}
        try:
            users["default"] = self.get_user_creds()
        except ValueError:
            pass
        for k, v in os.environ.items():
            if k.startswith("WEBUNTIS_USER_"):
                name = k.replace("WEBUNTIS_USER_", "").lower()
                p = os.getenv(f"WEBUNTIS_PASSWORD_{name.upper()}")
                if p:
                    users[name] = (v, p)
        return users

# ── Storage ───────────────────────────────────────────────────────────────

class TimetableStorage:
    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            storage_dir = Path(__file__).parent / "timetables"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)

    def _path(self, user: str, prefix: str = "current") -> Path:
        return self.storage_dir / f"{prefix}_{user}.json"

    def save(self, tt: Timetable, user: str, as_baseline: bool = False) -> Path:
        prefix = "baseline" if as_baseline else "current"
        path = self._path(user, prefix)
        with open(path, "w") as f:
            json.dump(tt.to_dict(), f, indent=2)
        print(f"💾 Saved to {path}")
        return path

    def load(self, path: Path) -> Optional[Timetable]:
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return Timetable.from_dict(json.load(f))
        except Exception as e:
            print(f"❌ Error loading timetable: {e}")
            return None

    def load_baseline(self, user: str) -> Optional[Timetable]:
        return self.load(self._path(user, "baseline"))

    def load_current(self, user: str) -> Optional[Timetable]:
        return self.load(self._path(user, "current"))

# ── WebUntis Client ──────────────────────────────────────────────────────

class WebUntisClient:
    def __init__(self, server: str, school: str, username: str, password: str):
        self.server = server
        self.school = school
        self.username = username
        self.password = password
        self._session = None
        self._subjects: Dict[int, str] = {}
        self._teachers: Dict[int, str] = {}
        self._rooms: Dict[int, str] = {}

    def connect(self) -> bool:
        try:
            self._session = webuntis.Session(server=self.server, school=self.school,
                                             username=self.username, password=self.password,
                                             useragent="WebUntis Timetable Checker")
            self._session.login()
            print(f"✅ Connected to WebUntis as {self.username}")
            return True
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False

    def _load_mappings(self):
        if not self._subjects:
            try:
                for s in self._session.subjects():
                    self._subjects[int(s.id)] = s.name
            except:
                pass
        if not self._teachers:
            try:
                for t in self._session.teachers():
                    self._teachers[int(t.id)] = t.name
            except:
                pass
        if not self._rooms:
            try:
                for r in self._session.rooms():
                    self._rooms[int(r.id)] = r.name
            except:
                pass

    def _resolve(self, items: list, cache: dict, detect_absent: bool = False) -> str:
        if not items:
            return "Unknown"
        absent = []
        for item in items:
            if isinstance(item, dict):
                iid = item.get("id", 0)
                org = item.get("orgid")
                if iid and iid in cache:
                    return cache[iid]
                if org and org in cache:
                    if detect_absent:
                        absent.append(cache[org])
                    else:
                        return cache[org]
        if absent:
            return f"{absent[0]} (absent)"
        return "Unknown"

    def _parse_period(self, period) -> Lesson:
        d = period._data if hasattr(period, "_data") else {}
        su_list = d.get("su", [])
        subject = self._resolve(su_list, self._subjects, detect_absent=False)
        if subject == "Unknown" and d.get("lstext"):
            subject = d["lstext"]
        room = self._resolve(d.get("ro", []), self._rooms, detect_absent=False)
        teacher = self._resolve(d.get("te", []), self._teachers, detect_absent=True)
        st = d.get("startTime", 0)
        et = d.get("endTime", 0)
        dr = str(d.get("date", 0))
        if len(dr) == 8:
            ld = date(int(dr[:4]), int(dr[4:6]), int(dr[6:]))
        else:
            ld = date.today()
        return Lesson(id=str(d.get("id", 0)), subject=subject, start_time=time(st // 100, st % 100),
                      end_time=time(et // 100, et % 100), room=room, teacher=teacher,
                      date=ld, code=d.get("code", ""), subst_text=d.get("substText", "") or "")

    def get_my_classes(self, start_date: date, end_date: date) -> List[Lesson]:
        if not self._session:
            raise RuntimeError("Not connected. Call connect() first.")
        try:
            self._load_mappings()
            lessons = []
            for period in self._session.my_timetable(start=start_date, end=end_date):
                lesson = self._parse_period(period)
                if lesson.subject != "Unknown":
                    lessons.append(lesson)
            return lessons
        except Exception as e:
            print(f"❌ Error fetching timetable: {e}")
            traceback.print_exc()
            raise

    def disconnect(self):
        if self._session:
            try:
                self._session.logout()
            except:
                pass
            self._session = None

# ── Comparator ───────────────────────────────────────────────────────────

class TimetableComparator:
    @staticmethod
    def compare(baseline: Timetable, current: Timetable, ignore_weekdays: Optional[List[int]] = None) -> List[Change]:
        changes = []
        ignore_weekdays = ignore_weekdays or []
        bl_by_wd = TimetableComparator._group_by_weekday(baseline.lessons)
        cur_by_date = TimetableComparator._group_lessons(current.lessons)

        all_dates = []
        d = current.start_date
        while d <= current.end_date:
            if d.weekday() in ignore_weekdays:
                d += timedelta(days=1)
                continue
            if d not in cur_by_date:
                cur_by_date[d] = []
            all_dates.append(d)
            d += timedelta(days=1)

        for target_date in all_dates:
            wd = target_date.weekday()
            bl = bl_by_wd.get(wd, [])
            cur = cur_by_date[target_date]
            if not cur and bl:
                changes.append(Change(ChangeType.HOLIDAY_OR_FREE_DAY, bl[0], None,
                                      f"No lessons on {target_date} (normally {len(bl)} lessons)"))
                continue
            matched = set()
            for old in bl:
                match = TimetableComparator._find_match(old, cur)
                if match:
                    matched.add(id(match))
                    changes.extend(TimetableComparator._detect(old, match))
                else:
                    changes.append(Change(ChangeType.CANCELLATION, old, None,
                                          f"{old.subject} {old.start_time}-{old.end_time} cancelled"))
            for new_lesson in cur:
                if id(new_lesson) not in matched:
                    changes.append(Change(ChangeType.NEW_CLASS, None, new_lesson,
                                          f"New: {new_lesson.subject} {new_lesson.start_time}-{new_lesson.end_time}"))
        return changes

    @staticmethod
    def _group_by_weekday(lessons: List[Lesson]) -> Dict[int, List[Lesson]]:
        g = {}
        for l in lessons:
            g.setdefault(l.date.weekday(), []).append(l)
        return g

    @staticmethod
    def _group_lessons(lessons: List[Lesson]) -> Dict[date, List[Lesson]]:
        g = {}
        for l in lessons:
            g.setdefault(l.date, []).append(l)
        return g

    @staticmethod
    def _find_match(lesson: Lesson, candidates: List[Lesson]) -> Optional[Lesson]:
        for c in candidates:
            if c.subject == lesson.subject and c.start_time == lesson.start_time and c.end_time == lesson.end_time:
                return c
        return None

    @staticmethod
    def _detect(old: Lesson, new: Lesson) -> List[Change]:
        changes = []
        if new.code == "cancelled":
            return [Change(ChangeType.CANCELLATION, old, new, f"{old.subject} {old.start_time}-{old.end_time} cancelled")]
        if old.room != new.room:
            changes.append(Change(ChangeType.ROOM_CHANGE, old, new, f"{old.subject}: room {old.room} → {new.room}"))
        if old.teacher != new.teacher:
            changes.append(Change(ChangeType.TEACHER_CHANGE, old, new, f"{old.subject}: teacher {old.teacher} → {new.teacher}"))
        if old.subject != new.subject:
            changes.append(Change(ChangeType.SUBJECT_CHANGE, old, new, f"Subject: {old.subject} → {new.subject}"))
        if old.start_time != new.start_time or old.end_time != new.end_time:
            changes.append(Change(ChangeType.TIME_CHANGE, old, new,
                                  f"{old.subject}: {old.start_time}-{old.end_time} → {new.start_time}-{new.end_time}"))
        return changes

# ── Formatter ────────────────────────────────────────────────────────────

class TimetableFormatter:
    LABELS = {ChangeType.CANCELLATION: ("❌", "Cancellations", Fore.RED), ChangeType.ROOM_CHANGE: ("🔄", "Room Changes", Fore.YELLOW),
              ChangeType.TIME_CHANGE: ("⏱️", "Time Changes", Fore.YELLOW), ChangeType.TEACHER_CHANGE: ("👨‍🏫", "Teacher Changes", Fore.CYAN),
              ChangeType.SUBJECT_CHANGE: ("📚", "Subject Changes", Fore.MAGENTA), ChangeType.NEW_CLASS: ("✨", "New Classes", Fore.GREEN),
              ChangeType.HOLIDAY_OR_FREE_DAY: ("🎉", "Holidays/Free Days", Fore.GREEN)}
    ORDER = [ChangeType.CANCELLATION, ChangeType.ROOM_CHANGE, ChangeType.TIME_CHANGE,
             ChangeType.TEACHER_CHANGE, ChangeType.SUBJECT_CHANGE, ChangeType.NEW_CLASS, ChangeType.HOLIDAY_OR_FREE_DAY]

    @staticmethod
    def timetable(tt: Timetable, title: str = "Timetable") -> str:
        if not tt.lessons:
            return f"{Fore.YELLOW}No lessons found{Style.RESET_ALL}"
        by_date = {}
        for l in sorted(tt.lessons, key=lambda x: (x.date, x.start_time)):
            by_date.setdefault(l.date, []).append(l)
        out = [f"\n{Fore.CYAN}{Style.BRIGHT}{title}{Style.RESET_ALL}",
               f"{Fore.CYAN}User: {tt.user} | {tt.fetch_date.strftime('%Y-%m-%d %H:%M')}{Style.RESET_ALL}", ""]
        for dk in sorted(by_date):
            out.append(f"{Fore.BLUE}{Style.BRIGHT}{dk.strftime('%A, %Y-%m-%d')}{Style.RESET_ALL}")
            rows = []
            for l in by_date[dk]:
                status = ""
                if l.code == "cancelled":
                    status = "❌ CANCELLED"
                if l.subst_text:
                    status = f"🔄 {l.subst_text}"
                subj = f"{l.subject} {Fore.RED}{status}{Style.RESET_ALL}" if status else l.subject
                rows.append([f"{l.start_time} - {l.end_time}", subj, l.room, l.teacher])
            out.append(tabulate(rows, headers=["Time", "Subject", "Room", "Teacher"], tablefmt="grid", disable_numparse=True))
            out.append("")
        return "\n".join(out)

    @staticmethod
    def changes(changes: List[Change]) -> str:
        if not changes:
            return f"{Fore.GREEN}✅ No changes detected{Style.RESET_ALL}\n"
        by_type = {}
        for c in changes:
            by_type.setdefault(c.change_type, []).append(c)
        out = ["", f"{Fore.RED}{Style.BRIGHT}⚠️  CHANGES DETECTED: {len(changes)}{Style.RESET_ALL}", ""]
        for ct in TimetableFormatter.ORDER:
            if ct not in by_type:
                continue
            icon, label, color = TimetableFormatter.LABELS.get(ct, ("•", ct.value, Fore.WHITE))
            out.append(f"{color}{icon}  {label} ({len(by_type[ct])}){Style.RESET_ALL}")
            for c in by_type[ct]:
                ds = ""
                if c.lesson_old:
                    ds = f" [{c.lesson_old.date}]"
                elif c.lesson_new:
                    ds = f" [{c.lesson_new.date}]"
                out.append(f"   • {c.description}{ds}")
            out.append("")
        return "\n".join(out)

    @staticmethod
    def summary(baseline: Timetable, current: Timetable, changes: List[Change]) -> str:
        return (f"\n{Fore.CYAN}{Style.BRIGHT}📊 COMPARISON SUMMARY{Style.RESET_ALL}\n"
                f"Baseline lessons: {len(baseline.lessons)}\n"
                f"Current lessons: {len(current.lessons)}\n"
                f"Total changes: {len(changes)}\n")

# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="WebUntis Timetable Comparison Tool",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="""Examples:
  python main.py fetch
  python main.py fetch --days 7
  python main.py fetch --date 2026-05-27
  python main.py fetch --user Benny
  python main.py fetch --username john --password secret123
  python main.py save
  python main.py show
  python main.py config""")
    sub = p.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user", help="Custom user name (e.g. Benny)")
    common.add_argument("--username", help="Override username")
    common.add_argument("--password", help="Override password")
    common.add_argument("--server", help="Override server")

    date_args = argparse.ArgumentParser(add_help=False)
    dg = date_args.add_mutually_exclusive_group()
    dg.add_argument("--date", type=str, help="Specific date (YYYY-MM-DD)")
    dg.add_argument("--days", type=int, default=1, help="Number of days (default: 1)")
    dg.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    date_args.add_argument("--end", type=str, help="End date (used with --start)")

    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--subject", help="Filter by subject")
    filters.add_argument("--teacher", help="Filter by teacher")
    filters.add_argument("--room", help="Filter by room")

    fp = sub.add_parser("fetch", parents=[common, date_args, filters])
    fp.add_argument("--baseline", type=str, help="Custom baseline file")
    fp.add_argument("--no-save", action="store_true", help="Don't save fetched timetable")

    sub.add_parser("save", parents=[common])
    sub.add_parser("show", parents=[common, date_args, filters])
    cp = sub.add_parser("config", parents=[common])
    cp.add_argument("--setup", action="store_true", help="Interactive setup wizard")

    return p.parse_args()

def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid date: {s}. Use YYYY-MM-DD")

def _date_range(args, ignore: Optional[List[int]] = None) -> Tuple[date, date]:
    today = date.today()
    ignore = ignore or []
    if getattr(args, "date", None):
        d = _parse_date(args.date)
        return d, d
    if getattr(args, "start", None) and getattr(args, "end", None):
        return _parse_date(args.start), _parse_date(args.end)
    if getattr(args, "start", None):
        s = _parse_date(args.start)
        d = args.days if getattr(args, "days", None) else 1
        return s, s + timedelta(days=d - 1)
    d = args.days if getattr(args, "days", None) else 1
    s = today
    while s.weekday() in ignore:
        s += timedelta(days=1)
    return s, s + timedelta(days=d - 1)

def _has_valid_dates(start: date, end: date, ignore: List[int]) -> bool:
    d = start
    while d <= end:
        if d.weekday() not in ignore:
            return True
        d += timedelta(days=1)
    return False

def _filter(lessons: List[Lesson], args) -> List[Lesson]:
    if getattr(args, "subject", None):
        lessons = [l for l in lessons if args.subject.lower() in l.subject.lower()]
    if getattr(args, "teacher", None):
        lessons = [l for l in lessons if args.teacher.lower() in l.teacher.lower()]
    if getattr(args, "room", None):
        lessons = [l for l in lessons if args.room.lower() in l.room.lower()]
    return lessons

def cmd_fetch(args):
    try:
        cfg = Config()
        store = TimetableStorage()
        try:
            un, pw = cfg.get_user_creds(args.user) if args.user else cfg.get_user_creds()
        except ValueError as e:
            print(f"❌ {e}"); return False
        if args.username: un = args.username
        if args.password: pw = args.password
        server = args.server or cfg.get_server()
        school = cfg.get_school()
        print(f"\n🔌 Connecting to WebUntis ({server})...")
        client = WebUntisClient(server, school, un, pw)
        if not client.connect():
            return False
        ignore = cfg.get_ignore_weekdays()
        start, end = _date_range(args, ignore=ignore)
        if not args.date and not _has_valid_dates(start, end, ignore):
            print("📭 All dates are weekends/ignored — nothing to fetch"); return True
        print(f"📅 Fetching {start} to {end}...")
        try:
            lessons = client.get_my_classes(start, end)
            client.disconnect()
        except Exception as e:
            print(f"❌ Fetch error: {e}"); return False
        lessons = _filter(lessons, args)
        cur = Timetable(user=un, lessons=lessons, fetch_date=datetime.now(), start_date=start, end_date=end)
        if not args.no_save:
            store.save(cur, un, as_baseline=False)
        bl_path = getattr(args, "baseline", None)
        bl = store.load(Path(bl_path)) if bl_path else store.load_baseline(un)
        if bl:
            print("\n🔍 Comparing to baseline...")
            comp_ignore = [] if args.date else ignore
            changes = TimetableComparator.compare(bl, cur, ignore_weekdays=comp_ignore)
            print(TimetableFormatter.changes(changes))
            print(TimetableFormatter.summary(bl, cur, changes))
        else:
            print(f"\n⚠️  No baseline for {un} — use 'save' to create one")
        print(TimetableFormatter.timetable(cur, "Current Timetable"))
        return True
    except Exception as e:
        print(f"❌ Error: {e}"); traceback.print_exc(); return False

def cmd_save(args):
    try:
        store = TimetableStorage()
        cfg = Config()
        try:
            un, _ = cfg.get_user_creds(args.user) if args.user else cfg.get_user_creds()
        except ValueError as e:
            print(f"❌ {e}"); return False
        cur = store.load_current(un)
        if not cur:
            print(f"❌ No current timetable for {un} — run 'fetch' first"); return False
        store.save(cur, un, as_baseline=True)
        print(f"✅ Baseline saved for {un}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}"); return False

def cmd_show(args):
    try:
        store = TimetableStorage()
        cfg = Config()
        try:
            un, _ = cfg.get_user_creds(args.user) if args.user else cfg.get_user_creds()
        except ValueError as e:
            print(f"❌ {e}"); return False
        tt = store.load_current(un)
        if not tt:
            print(f"❌ No timetable for {un}"); return False
        tt.lessons = _filter(tt.lessons, args)
        print(TimetableFormatter.timetable(tt))
        return True
    except Exception as e:
        print(f"❌ Error: {e}"); return False

def cmd_config(args):
    try:
        if args.setup:
            Config(); return True
        cfg = Config()
        try:
            un, pw = cfg.get_user_creds(args.user) if args.user else cfg.get_user_creds()
        except ValueError as e:
            print(f"❌ {e}"); return False
        server = args.server or cfg.get_server()
        school = cfg.get_school()
        print(f"\n🔧 Server: {server}\nSchool: {school}\nUser: {un}\n")
        print("🔌 Testing connection...")
        client = WebUntisClient(server, school, un, pw)
        ok = client.connect()
        client.disconnect()
        print("✅ Connection successful!" if ok else "❌ Connection failed!")
        return ok
    except Exception as e:
        print(f"❌ Error: {e}"); return False

def main():
    args = parse_args()
    if not args.command:
        print("No command. Use --help for usage."); return 1
    cmds = {"fetch": cmd_fetch, "save": cmd_save, "show": cmd_show, "config": cmd_config}
    return 0 if cmds.get(args.command, lambda _: False)(args) else 1

if __name__ == "__main__":
    sys.exit(main())
