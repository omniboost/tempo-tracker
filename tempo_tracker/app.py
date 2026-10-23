"""Menubalk-app: koppelt de tracker aan timers, dialogen en de Jira/Tempo-API's."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from PyObjCTools import AppHelper

from . import credentials, dialogs, ui
from .config import (
    DB_PATH,
    ISSUE_KEY_RE,
    format_fixed_options,
    load_config,
    normalize_site,
    parse_clock,
    parse_fixed_options,
    save_config,
)
from .dialogs import FormField, OverviewLine, PickItem
from .calls import in_call
from .git import BranchSwitch, BranchWatcher, CommitLog, describe, find_repos
from .hotkey import HotKey
from .hotkey import parse as parse_shortcut
from .icon import app_icon
from .idle import idle_seconds, screen_off
from .jira import JiraClient, JiraError
from .meetings import LOOKBACK_SECONDS, MEETING_MAX_SECONDS, suggest
from .store import CachedIssue, Store, day_of, today
from .summary import build_overview, default_description, format_duration, parse_duration
from .tempo import TempoClient, TempoError
from .week import WORK_DAYS, DaySummary, attention, format_work_days, parse_reminder, parse_work_days, reminder_moment, visible, week_days
from .tracker import AWAY, PAUSED, TRACKING, Activity, CameBack, CheckinDue, DayStart, EndOfDay, Tracker

log = logging.getLogger(__name__)

TICK_SECONDS = 15
ISSUE_REFRESH_SECONDS = 30 * 60
REPO_SCAN_SECONDS = 10 * 60
WEEK_REFRESH_SECONDS = 15 * 60
WEEK_SNOOZE_SECONDS = 60 * 60
BRANCH_MODES = ("ask", "auto", "off")
SNOOZE_SECONDS = 15 * 60
SINCE_CHOICES = (("Now", 0), ("5m ago", 5), ("10m", 10), ("15m", 15), ("30m", 30))
RECENT_IN_MENU = 5
PAUSE_ITEM = PickItem("", "Break / private (don't log)", "")
EMPTY_OVERVIEW_LINES = 2
SENT_DONE, SENT_PAUSE, SENT_KEEP = range(3)
KEEP_SENT_DAYS = 35  # helemaal verstuurde dagen daarna wissen (ze staan in Tempo); het leren kijkt 4 weken terug


def _with_icon(item: rumps.MenuItem, symbol_name: str) -> rumps.MenuItem:
    item._menuitem.setImage_(ui.symbol_image(symbol_name, 13))
    return item


def _today_date() -> date:
    return datetime.fromtimestamp(time.time()).date()


def hhmm(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def clock_today(value: str, now: float) -> float:
    """'9:00', '9.30' of '9' -> tijdstip vandaag."""
    value = value.strip()
    hours, minutes = (int(value), 0) if value.isdigit() and len(value) <= 2 else parse_clock(value)
    if hours > 23:
        raise ValueError(f"Invalid time: {value!r}")
    return datetime.fromtimestamp(now).replace(hour=hours, minute=minutes, second=0, microsecond=0).timestamp()


@dataclass
class _Line:
    issue_key: str
    label: str
    target_key: str
    seconds: int
    tracked_seconds: int
    description: str
    first_started_at: float | None
    default_description: str = ""
    auto_seconds: int = 0


class TempoTrackerApp(rumps.App):
    def __init__(self):
        super().__init__("Tempo Tracker", title="⏱", quit_button=None)
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        NSApplication.sharedApplication().setApplicationIconImage_(app_icon())
        dialogs.install_edit_menu()
        self.config = load_config()
        self.store = Store(DB_PATH)
        self.tracker = Tracker(self.store, 0, 0, 0)
        self._apply_config()
        self.tracker.restore(time.time())

        self.busy = False
        self._issue_keys: dict[str, str] = {}
        self.branches = BranchWatcher(json.loads(self.store.get_state("branches") or "{}"))
        self._pending_switch: BranchSwitch | None = None
        self.commits = CommitLog()
        self._repos: list = []
        self._last_repo_scan = 0.0
        self._checkin_snoozed_until = 0.0
        self._waiting_for_call = False
        self._jira_client: tuple[tuple, JiraClient] | None = None
        self._call_started: float | None = None
        self._call_ended_at = 0.0
        self._pending_call: tuple[float, float] | None = None
        self._recent_titles: list[str] = []
        self._recent: list[tuple[str, str]] | None = None
        self.needs_setup = not self._has_credentials()
        self._refresh_lock = threading.Lock()
        self._refresh_result: tuple | None = None
        self._last_refresh = 0.0
        self._week_entries: list[tuple[str, str | None]] | None = None
        self._week_tempo: dict[str, list] | None = None  # dag -> worklogs uit Tempo (deze week); None = nog niet gelezen
        self._week_result: dict[str, list] | None = None
        self._week_lock = threading.Lock()
        self._last_week_refresh = 0.0
        self._week_snoozed_until = 0.0

        # Kop van het menu: status, huidig ticket en de voortgang van vandaag (title = dezelfde info als tekst).
        self.header = ui.MenuHeader.alloc().init()
        self.current_item = rumps.MenuItem("Not tracking")
        self.current_item._menuitem.setView_(self.header)
        self.pause_item = _with_icon(rumps.MenuItem("Pause", callback=self.on_toggle_pause), "pause.circle")
        self.week_menu = _with_icon(rumps.MenuItem("This week"), "calendar.badge.clock")
        self.switch_item = _with_icon(rumps.MenuItem("Switch ticket…", callback=self.on_switch), "arrow.left.arrow.right")
        self.menu = [
            self.current_item,
            None,
            self.switch_item,
            _with_icon(rumps.MenuItem("Add time…", callback=self.on_add_time), "plus.circle"),
            self.pause_item,
            None,
            _with_icon(rumps.MenuItem("Day overview…", callback=self.on_overview_today), "calendar"),
            self.week_menu,
            None,
            _with_icon(rumps.MenuItem("Refresh Jira issues", callback=self.on_refresh_issues), "arrow.clockwise"),
            _with_icon(rumps.MenuItem("Settings…", callback=self.on_settings), "gearshape"),
            _with_icon(rumps.MenuItem("Quit Tempo Tracker", callback=self.on_quit), "power"),
        ]
        try:
            self.hotkey: HotKey | None = HotKey()
        except OSError:
            log.exception("Shortcuts are not available")
            self.hotkey = None
        self._register_shortcut()
        self.timer = rumps.Timer(self.on_tick, TICK_SECONDS)
        self.timer.start()
        self._update_menu()

    # -- configuratie ---------------------------------------------------------

    def _apply_config(self) -> None:
        self.tracker.checkin_seconds = self.config.checkin_minutes * 60
        self.tracker.idle_seconds = self.config.idle_minutes * 60
        self.tracker.end_of_day_minutes = self.config.end_of_day_minutes()
        self.tracker.work_days = set(self._work_days())

    def _work_days(self) -> frozenset[int]:
        try:
            return parse_work_days(self.config.work_days)
        except ValueError:
            return WORK_DAYS

    def _register_shortcut(self) -> None:
        self.switch_item.title = "Switch ticket…"
        if self.hotkey is None:
            return
        if not self.config.shortcut:
            self.hotkey.unregister()
            return
        if self.hotkey.register(self.config.shortcut, self._on_shortcut):
            self.switch_item.title = f"Switch ticket…  {parse_shortcut(self.config.shortcut)[2]}"

    def _on_shortcut(self) -> None:
        """Sneltoets: een openstaand venster naar voren halen, anders de ticketkiezer openen."""
        if dialogs.bring_to_front():
            return
        AppHelper.callAfter(self.on_switch, None)

    def _has_credentials(self) -> bool:
        return (
            self.config.is_complete
            and bool(credentials.get_token(credentials.JIRA))
            and bool(credentials.get_token(credentials.TEMPO))
        )

    def _jira(self) -> JiraClient:
        """Eén Jira-verbinding die blijft bestaan (welke server werkt, zoekt hij maar één keer uit); nieuw na andere instellingen."""
        token = credentials.get_token(credentials.JIRA)
        if not token:
            raise JiraError("No Jira token set (Settings…).")
        identity = (self.config.jira_site, self.config.email, token)
        if self._jira_client is None or self._jira_client[0] != identity:
            self._jira_client = (identity, JiraClient(*identity))
        client = self._jira_client[1]
        client.account_id = client.account_id or self.store.get_state("account_id")
        return client

    def _tempo(self) -> TempoClient:
        token = credentials.get_token(credentials.TEMPO)
        if not token:
            raise TempoError("No Tempo token set (Settings…).")
        return TempoClient(self.config.tempo_base_url, token)

    # -- klok -----------------------------------------------------------------

    def on_tick(self, _sender=None) -> None:
        if self.busy:
            return
        self._store_refreshed_issues()
        if self.needs_setup:
            self.needs_setup = False
            self._dialog(self._first_run)
            return
        if time.time() - self._last_refresh > ISSUE_REFRESH_SECONDS:
            self._start_issue_refresh()
        self._store_week_result()
        if time.time() - self._last_week_refresh > WEEK_REFRESH_SECONDS:
            self._start_week_refresh()

        self._prune_daily()
        now = time.time()
        if screen_off():
            # Scherm uit of vergrendeld (ook als de Mac met de klep dicht even half wakker is): je bent er niet.
            self.tracker.tick(now, idle_seconds(), present=False)
            self._update_menu()
            return
        calling = in_call()
        self._follow_call(calling, now)
        # Tijdens een gesprek typ je vaak niets; dat is geen afwezigheid (ook niet direct erna).
        idle = 0 if calling else min(idle_seconds(), now - self._call_ended_at)
        event = self.tracker.tick(now, idle)
        switch = self._poll_branches()
        if switch:
            self._pending_switch = switch  # bewaren tot hij gevraagd kan worden (niet tijdens een andere vraag of afwezigheid)
        if event is None and self._pending_switch and idle < 60 and self.tracker.status != AWAY:
            switch, self._pending_switch = self._pending_switch, None
            self._dialog(self._on_branch_switch, switch)
        if isinstance(event, CameBack):
            self._dialog(self._ask_away, event)
        elif self._pending_call and (event is None or isinstance(event, CheckinDue)) and idle < 60:
            call, self._pending_call = self._pending_call, None
            self._dialog(self._ask_call, *call)  # een check-in die al moest, komt de volgende tick
        elif isinstance(event, CheckinDue):
            if time.time() < self._checkin_snoozed_until:
                pass
            elif calling:
                self._waiting_for_call = True  # na het gesprek
            else:
                self._waiting_for_call = False
                self._dialog(self._checkin, event)
        elif isinstance(event, DayStart):
            self._dialog(self._ask_start, "Good morning! What are you starting with?")
        elif isinstance(event, EndOfDay):
            self._dialog(self._overview, event.day, True)
        elif event is None and idle < 60 and self._week_reminder_due():
            self._dialog(self._week_reminder)
        self._update_menu()

    def _prune_daily(self) -> None:
        """Eén keer per dag: dagen ouder dan KEEP_SENT_DAYS die helemaal verstuurd zijn, wissen."""
        current_day = today()
        if self.store.get_state("pruned") == current_day:
            return
        self.store.set_state("pruned", current_day)
        removed = self.store.prune((_today_date() - timedelta(days=KEEP_SENT_DAYS)).isoformat())
        if removed:
            log.info("Removed %d old day(s) that are already in Tempo", removed)

    def _follow_call(self, calling: bool, now: float) -> None:
        """Begin en eind van een gesprek bijhouden; na een gesprek dat lang genoeg was, volgt de vraag wat het was."""
        if calling and self._call_started is None:
            self._call_started = now
        elif not calling and self._call_started is not None:
            started, self._call_started = self._call_started, None
            self._call_ended_at = now
            if self.config.call_min_minutes and now - started >= self.config.call_min_minutes * 60:
                self._pending_call = (started, now)

    def _dialog(self, function, *args) -> None:
        """Eén dialoog tegelijk; fouten loggen zonder de app te laten crashen."""
        if self.busy:
            return
        self.busy = True
        try:
            function(*args)
        except Exception:
            log.exception("Error in dialog %s", function.__name__)
            dialogs.message("Something went wrong", "Details are in ~/Library/Logs/TempoTracker.log.")
        finally:
            self.busy = False
            self.tracker.touch(time.time())
            self._update_menu()

    def _update_menu(self) -> None:
        now = time.time()
        today_seconds = self.tracker.seconds_today(now)
        total = format_duration(today_seconds)
        current = self.tracker.current
        short = (current.label if current.is_fixed else current.key) if current else ""
        if self.tracker.status == TRACKING:
            state, icon, title = "tracking", "timer", f"{short}  {total}"
            ticket = current.label
            detail = f"since {hhmm(self.tracker.open_since)} · {format_duration(self.tracker.open_seconds(now))}"
            if self._waiting_for_call:
                detail += " · check-in after your call"
            elif now < self._checkin_snoozed_until:
                detail += f" · check-in at {hhmm(self._checkin_snoozed_until)}"
            self.pause_item.title = "Pause"
            _with_icon(self.pause_item, "pause.circle")
        elif self.tracker.status == AWAY:
            state, icon, title = "away", "moon.zzz", f"away  {total}"
            ticket, detail = "Away", f"since {hhmm(self.tracker.away_since)} · nothing is tracked"
            self.pause_item.title = "Pause"
            _with_icon(self.pause_item, "pause.circle")
        else:
            state, icon, title = "paused", "pause.circle", total
            ticket = "Paused"
            detail = f"last: {current.label}" if current else "Start tracking from this menu"
            if self.store.get_state("done_day") == today():
                ticket, detail = "Done for today", "see you tomorrow · start again from this menu"
            self.pause_item.title = f"Resume {short}" if current else "Start…"
            _with_icon(self.pause_item, "play.circle")
        self.title = title
        self._set_status_icon(icon)
        self.current_item.title = f"{ticket} — {detail}"
        target = parse_duration(self.config.day_target) if self.config.day_target else 0
        self.header.update(
            state, ticket, detail,
            f"{total} of {format_duration(target)}" if target else total,
            today_seconds / target if target else 0.0,
        )
        self._update_week_menu()
        self._update_recent()

    def _update_recent(self) -> None:
        """De laatste tickets direct in het menu, onder 'Switch ticket…': één klik om te wisselen."""
        current = self.tracker.current.key if self.tracker.current and self.tracker.status == TRACKING else None
        recent = [(key, label) for key, label in self.store.recent_activities(RECENT_IN_MENU + 1) if key != current][:RECENT_IN_MENU]
        if recent == self._recent:
            return
        for title in self._recent_titles:
            del self.menu[title]
        self._recent, self._recent_titles = recent, []
        for key, label in reversed(recent):
            title = "    " + (label if len(label) <= 60 else label[:59] + "…")
            item = rumps.MenuItem(title, callback=lambda _s, k=key, l=label: self._switch_now(Activity(k, l)))
            self.menu.insert_after("Switch ticket…", item)  # rumps gebruikt de titel als sleutel: opmaak pas daarna
            item._menuitem.setAttributedTitle_(ui.ticket_text(label if len(label) <= 60 else label[:59] + "…", 13))
            item._menuitem.setIndentationLevel_(1)
            self._recent_titles.append(title)

    def _switch_now(self, activity: Activity) -> None:
        if self.busy:
            dialogs.bring_to_front()
            return
        self.tracker.start(activity, time.time())
        self._update_menu()

    def _set_status_icon(self, name: str) -> None:
        """Icoon links van de tekst in de menubalk (past zich aan licht/donker aan)."""
        status_item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if status_item is None or getattr(self, "_status_icon", None) == name:
            return
        image = ui.symbol_image(name, 14)
        image.setTemplate_(True)
        status_item.button().setImage_(image)
        status_item.button().setImagePosition_(2)  # NSImageLeft
        self._status_icon = name

    # -- week ---------------------------------------------------------------------

    def _start_week_refresh(self) -> None:
        """Worklogs van deze week uit Tempo lezen, op de achtergrond (alleen lezen)."""
        account_id = self.store.get_state("account_id")
        if not account_id or not self._has_credentials() or not self._week_lock.acquire(blocking=False):
            return
        self._last_week_refresh = time.time()
        days = week_days(_today_date())
        tempo = self._tempo()

        def work() -> None:
            try:
                by_day: dict[str, list] = {day: [] for day in days}
                for worklog in tempo.worklogs(account_id, days[0], days[-1]):
                    by_day.setdefault(worklog.day, []).append(worklog)
                self._week_result = by_day
            except TempoError as error:
                log.warning("Reading this week from Tempo failed: %s", error)
            finally:
                self._week_lock.release()

        threading.Thread(target=work, daemon=True).start()

    def _store_week_result(self) -> None:
        result, self._week_result = self._week_result, None
        if result is not None:
            self._week_tempo = result

    def _week_summaries(self, now: float) -> list[DaySummary]:
        """Per dag van deze week: al in Tempo + nog te versturen (zoals in het dagoverzicht, met aftrek van meetings)."""
        own = self.store.own_worklog_ids()
        summaries = []
        current_day = _today_date().isoformat()
        for day in week_days(_today_date()):
            worklogs = (self._week_tempo or {}).get(day, [])
            booked = self._booked_periods(day, [w for w in worklogs if w.worklog_id not in own])
            to_send = sum(row.seconds for row in build_overview(self.store, self.config, day, booked))
            if day == current_day:
                to_send += int(self.tracker.open_seconds(now))
            summaries.append(DaySummary(day, sum(w.seconds for w in worklogs), to_send))
        return summaries

    def _week_target(self) -> int:
        return parse_duration(self.config.day_target) * len(self._work_days()) if self.config.day_target else 0

    def _update_week_menu(self) -> None:
        """'This week · 31:30 of 40:00' met per dag de stand; daaronder eerdere dagen die nog niet verstuurd zijn."""
        now = time.time()
        summaries = self._week_summaries(now)
        week_total = sum(d.total for d in summaries)
        target = self._week_target()
        self.week_menu.title = f"This week  ·  {format_duration(week_total)}" + (f" of {format_duration(target)}" if target else "")
        entries: list[tuple[str, str | None]] = []
        rows = []
        for summary in visible(summaries, self._work_days()):
            row = self._week_row(summary)
            rows.append(row)
            entries.append((f"{row.day}  {row.total}  {row.status}", summary.day if row.kind != "future" else None))
        earlier = self.store.days_with_unsent(before=week_days(_today_date())[0])
        for day in earlier:
            entries.append((f"{datetime.strptime(day, '%Y-%m-%d').strftime('%a %-d %b')}  not sent", day))
        if entries == self._week_entries:
            return
        if self._week_entries is not None:
            self.week_menu.clear()
        self._week_entries = entries
        colors = {"done": ui.SUCCESS, "open": ui.WARNING, "empty": ui.TERTIARY_TEXT, "future": ui.TERTIARY_TEXT}
        for (title, day), row in zip(entries, rows):
            item = rumps.MenuItem(title, callback=(lambda _s, d=day: self._dialog(self._overview, d, False)) if day else None)
            self.week_menu.add(item)
            item._menuitem.setAttributedTitle_(ui.columns_text(
                [(row.day, ui.font(13, ui.MEDIUM), ui.PRIMARY_TEXT), (row.total, ui.digits(13), ui.SECONDARY_TEXT),
                 (row.status, ui.font(12.5), colors[row.kind])],
                [64, 120],
            ))
        if earlier:
            self.week_menu.add(rumps.separator)
            self.week_menu.add(rumps.MenuItem("Earlier, not sent yet"))
            for title, day in entries[len(rows):]:
                self.week_menu.add(rumps.MenuItem(title, callback=lambda _s, d=day: self._dialog(self._overview, d, False)))

    def _week_row(self, summary: DaySummary) -> dialogs.WeekRow:
        label = summary.date.strftime("%a %-d")
        current = _today_date()
        if summary.date > current:
            return dialogs.WeekRow(label, "", "–", "future")
        if summary.to_send:
            return dialogs.WeekRow(label, format_duration(summary.total), f"{format_duration(summary.to_send)} not sent", "open")
        if not summary.total:
            return dialogs.WeekRow(label, "0:00", "nothing tracked" if summary.date < current else "today", "empty")
        return dialogs.WeekRow(label, format_duration(summary.total), "✓ in Tempo", "done")

    def _week_reminder_due(self) -> bool:
        try:
            reminder = parse_reminder(self.config.week_reminder)
        except ValueError:
            return False
        if reminder is None or time.time() < self._week_snoozed_until:
            return False
        now = datetime.fromtimestamp(time.time())
        monday = week_days(now.date())[0]
        return now >= reminder_moment(reminder, now) and self.store.get_state("week_reminded") != monday

    def _week_reminder(self) -> None:
        """Vrijdagseintje: hoe staat de week ervoor en welke dagen hebben nog aandacht nodig."""
        days = week_days(_today_date())

        def read_week(_report) -> dict[str, list]:
            by_day: dict[str, list] = {day: [] for day in days}
            for worklog in tempo.worklogs(jira.myself(), days[0], days[-1]):
                by_day.setdefault(worklog.day, []).append(worklog)
            return by_day

        try:  # verse cijfers uit Tempo (alleen lezen); lukt dat niet, dan met wat we al hadden
            jira, tempo = self._jira(), self._tempo()
            self._week_tempo = dialogs.progress("Your week", "Reading your worklogs from Tempo…", read_week)
        except (JiraError, TempoError) as error:
            log.warning("Reading this week from Tempo failed: %s", error)
        summaries = self._week_summaries(time.time())
        target = self._week_target()
        notes = attention(summaries, parse_duration(self.config.day_target) if self.config.day_target else 0, _today_date(), self._work_days())
        week_total = sum(d.total for d in summaries)
        summary = f"{format_duration(week_total)}" + (f" of {format_duration(target)}" if target else "") + " this week"
        first_day = notes[0][0] if notes else None
        buttons = [f"Open {datetime.strptime(first_day, '%Y-%m-%d').strftime('%A')}", "Later"] if first_day else ["Nice"]
        choice = dialogs.week(
            "Your week", "Everything that's in Tempo plus what's still waiting to be sent.",
            [self._week_row(d) for d in visible(summaries, self._work_days())], summary,
            week_total / target if target else 0.0, [text for _, text in notes], buttons,
        )
        monday = week_days(_today_date())[0]
        if first_day and choice == len(buttons) - 1:
            self._week_snoozed_until = time.time() + WEEK_SNOOZE_SECONDS
            return
        self.store.set_state("week_reminded", monday)
        if first_day and choice == 0:
            self._overview(first_day, False)

    # -- Jira-issues --------------------------------------------------------------

    def _start_issue_refresh(self) -> None:
        if not self._has_credentials() or not self._refresh_lock.acquire(blocking=False):
            return
        self._last_refresh = time.time()
        jira = self._jira()

        def work() -> None:
            try:
                account_id = jira.myself()
                self._refresh_result = (account_id, jira.assigned_issues(), jira.recent_issues())
            except JiraError as error:
                log.warning("Refreshing Jira issues failed: %s", error)
            finally:
                self._refresh_lock.release()

        threading.Thread(target=work, daemon=True).start()

    def _store_refreshed_issues(self) -> None:
        result, self._refresh_result = self._refresh_result, None
        if not result:
            return
        account_id, assigned, recent = result
        self.store.set_state("account_id", account_id)
        self.store.replace_issues("assigned", [CachedIssue(i.key, i.issue_id, i.summary, "assigned") for i in assigned])
        self.store.replace_issues("recent", [CachedIssue(i.key, i.issue_id, i.summary, "recent") for i in recent])

    def _pick_items(self) -> list[PickItem]:
        items: dict[str, PickItem] = {}
        for key, label in self.store.recent_activities():
            items.setdefault(key, PickItem(key, label, "recently used"))
        for source, hint in (("assigned", "assigned"), ("recent", "recent in Jira")):
            for issue in self.store.cached_issues(source):
                items.setdefault(issue.key, PickItem(issue.key, f"{issue.key} · {issue.summary}", hint))
        for option in self.config.fixed_options:
            key = f"@{option['label']}"
            items[key] = PickItem(key, option["label"], "fixed")
        return list(items.values())

    def _search_jira(self, text: str) -> list[PickItem]:
        """Voor de ticketkiezer: tekst zonder key ook in Jira zoeken (draait op een achtergrondthread)."""
        return [PickItem(i.key, f"{i.key} · {i.summary}", "Jira") for i in self._jira().search_text(text)]

    def _to_activity(self, item: PickItem) -> Activity | None:
        if item.key.startswith("@"):
            return Activity(item.key, item.label)
        if item.hint == "manual":
            try:
                issue = self._jira().get_issue(item.key)
            except JiraError as error:
                dialogs.message(f"{item.key} not found", str(error))
                return None
            return Activity(issue.key, f"{issue.key} · {issue.summary}")
        return Activity(item.key, item.label)

    # -- git --------------------------------------------------------------------

    def _scan_repos(self) -> None:
        self._repos = find_repos(self.config.git_roots)
        self.branches.set_repos(self._repos)
        self.store.set_state("branches", json.dumps(self.branches.snapshot()))
        self._last_repo_scan = time.time()

    def _poll_branches(self) -> BranchSwitch | None:
        """Wissel naar een branch met een ticketkey. De branches worden bewaard, zodat een herstart niets mist."""
        if not self.config.git_roots or self.config.branch_switch == "off":
            return None
        if time.time() - self._last_repo_scan > REPO_SCAN_SECONDS:
            self._scan_repos()
        before = self.branches.snapshot()
        switch = self.branches.poll()
        if self.branches.snapshot() != before:
            self.store.set_state("branches", json.dumps(self.branches.snapshot()))
        return switch

    def _on_branch_switch(self, switch: BranchSwitch) -> None:
        now = time.time()
        tracking = self.tracker.status == TRACKING
        current = self.tracker.current
        if tracking and current and current.key == switch.key:
            return
        # Tijdens tracking vanaf de checkout (niet vóór de open periode); gepauzeerd vanaf nu, want dan werkte je misschien niet.
        since = max(min(switch.at, now), self.tracker.earliest_start(now)) if tracking else now
        if self.config.branch_switch != "auto" or not tracking:
            current = self.tracker.current
            text = f"You switched to {switch.branch} in {switch.repo.name} at {hhmm(since)}."
            if tracking:
                text += f"\nThe time since then moves from {current.label} to {switch.key}."
            verb = "Switch to" if tracking else "Start"
            if dialogs.choose(f"Track {switch.key}?", text, [f"{verb} {switch.key}", "Not now"]) != 0:
                return
        activity = self._activity_for_key(switch.key, quiet=self.config.branch_switch == "auto" and tracking)
        if activity:
            self.tracker.start(activity, time.time(), since)
            log.info("Switched to %s after a branch checkout", switch.key)

    def _activity_for_key(self, key: str, quiet: bool = False) -> Activity | None:
        item = next((i for i in self._pick_items() if i.key == key), None)
        if item and item.hint != "manual":
            return self._to_activity(item)
        try:
            issue = self._jira().get_issue(key)
        except JiraError as error:
            log.warning("Looking up %s failed: %s", key, error)
            if not quiet:
                dialogs.message(f"{key} not found", str(error))
            return None
        return Activity(issue.key, f"{issue.key} · {issue.summary}")

    def _commit_descriptions(self, day: str) -> dict[str, str]:
        """Per ticketkey een omschrijving uit je commits van die dag."""
        if not self.config.git_roots:
            return {}
        if not self._repos:
            self._scan_repos()
        return {key: describe(subjects) for key, subjects in self.commits.messages_by_key(self._repos, day).items()}

    def _pick_activity_since(
        self, title: str, text: str, preselect: str | None = None, whole_since: float | None = None
    ) -> tuple[Activity, float | None] | None:
        """Ticket kiezen met 'Started: Now / 5m ago / … / or at hh:mm'. Geeft (activiteit, begintijd); None = nu.

        Met `whole_since` (bij een check-in) is er ook een knop voor de hele periode sinds dat moment, en die staat aan.
        """
        choices = [label for label, _ in SINCE_CHOICES]
        if whole_since is not None:
            choices.append(f"All since {hhmm(whole_since)}")
        selected, clock = len(choices) - 1 if whole_since is not None else 0, ""
        while True:
            item, segment, clock = dialogs.pick_with_since(
                title, text, self._pick_items(), preselect, choices, selected, clock, remote=self._search_jira
            )
            if not item:
                return None
            now = time.time()
            earliest = self.tracker.earliest_start(now)
            if clock.strip():
                try:
                    since = clock_today(clock, now)
                except ValueError as error:
                    dialogs.message("Invalid time", str(error))
                    continue
                if since > now:
                    dialogs.message("Invalid time", "That time is in the future.")
                    continue
                if since < earliest:
                    dialogs.message(
                        "Too early",
                        f"Can't start before {hhmm(earliest)}: the time before that is already tracked. "
                        "Adjust earlier time in the day overview.",
                    )
                    continue
            else:
                segment = selected if segment is None else segment
                if segment >= len(SINCE_CHOICES):
                    since = whole_since
                else:
                    minutes = SINCE_CHOICES[segment][1]
                    # 'Langer geleden' dan er open tijd is: dan vanaf het begin daarvan.
                    since = max(now - minutes * 60, earliest) if minutes else None
            preselect, selected = item.key, segment if segment is not None else selected
            activity = self._to_activity(item)
            if activity:
                return activity, since

    # -- dialogen ---------------------------------------------------------------

    def _first_run(self) -> None:
        dialogs.message(
            "Welcome to Tempo Tracker",
            "First enter your Jira site, email address and the two API tokens. "
            "The tokens are stored in your macOS Keychain.",
        )
        self._settings()

    def _ask_start(self, title: str) -> None:
        recent = self.store.recent_activities(1)
        choice = self._pick_activity_since(
            title, "Pick a ticket or fixed option. Started earlier? Enter the start time.", recent[0][0] if recent else None
        )
        if choice:
            self.tracker.start(choice[0], time.time(), choice[1])

    def _checkin(self, event: CheckinDue) -> None:
        current = self.tracker.current
        shown_at = time.time()
        activity = None
        extras: list[tuple[Activity, int]] = []
        while True:
            choice = dialogs.checkin(
                current.label,
                format_duration(self.tracker.open_seconds(time.time())),
                hhmm(event.since),
                self._checkin_split(current, extras),
                timeout=self.tracker.idle_seconds,
            )
            if choice != 1:
                break
            available = self.tracker.open_seconds(time.time()) - sum(seconds for _, seconds in extras)
            extra = self._ask_extra(available, f"This comes out of the time since {hhmm(event.since)}; the rest stays on {current.label}.")
            if extra:
                extras.append(extra)
        if choice == dialogs.TIMED_OUT:
            self._checkin_unanswered(shown_at, extras)
            return
        if choice == 4:
            self._checkin_snoozed_until = time.time() + SNOOZE_SECONDS
            return
        since = None
        if choice == 2:
            picked = self._pick_activity_since(
                "What are you working on?",
                f"Since when? 'All since {hhmm(self.tracker.open_since)}' moves the whole period to this ticket; "
                f"otherwise the time before that stays on {current.label}.",
                current.key,
                whole_since=self.tracker.open_since,
            )
            if picked:
                activity, since = picked
        answered_at = time.time()
        was_away = answered_at - shown_at > self.tracker.idle_seconds
        at = shown_at if was_away else answered_at

        if choice == 3:
            self.tracker.pause(at, tuple(extras))
            return
        if choice == 2 and activity:
            switch_at = min(at if since is None else since, at)
            if switch_at <= self.tracker.open_since:
                self.tracker.reassign(activity, at, tuple(extras))
            else:
                self.tracker.confirm(switch_at, tuple(extras))  # tot de wissel: het huidige ticket
                self.tracker.start(activity, at, switch_at)
        else:
            self.tracker.confirm(at, tuple(extras))
        if was_away:
            self.tracker.go_away(shown_at)
            self._ask_away(CameBack(shown_at, answered_at))

    def _checkin_unanswered(self, shown_at: float, extras: list[tuple[Activity, int]]) -> None:
        """Geen antwoord op de check-in. Was je weg, dan geldt de tijd vanaf de vraag als afwezig
        (bij terugkomst vraagt hij wat je deed); was je actief maar zag je de vraag niet, dan geldt 'Yes'."""
        now = time.time()
        if idle_seconds() >= self.tracker.idle_seconds:
            self.tracker.confirm(shown_at, tuple(extras))
            self.tracker.go_away(shown_at)
            log.info("Check-in unanswered while idle; counted as away from %s", hhmm(shown_at))
        else:
            self.tracker.confirm(now, tuple(extras))
            log.info("Check-in unanswered while active; counted as yes")

    def _checkin_split(self, current: Activity, extras: list[tuple[Activity, int]]) -> list[tuple[str, str]]:
        """Gesprekken en de stukken die je bij 'Plus something else' afsplitste, plus wat er op het huidige ticket overblijft."""
        calls = [(f"{label} · call {hhmm(start)}–{hhmm(end)}", format_duration(int(end - start))) for start, end, label in self.tracker.carved]
        if not calls and not extras:
            return []
        rest = self.tracker.open_seconds(time.time()) - sum(seconds for _, seconds in extras)
        parts = calls + [(activity.label, format_duration(seconds)) for activity, seconds in extras]
        return parts + [(f"{current.label} (rest)", format_duration(max(rest, 0)))]

    def _ask_extra(self, available: int | None, text: str) -> tuple[Activity, int] | None:
        """Ticket + duur voor een los stuk tijd. `available` = maximum in seconden (None = geen maximum)."""
        if available is not None:
            text += f"\nLeft to split: {format_duration(available)}."
        duration = "0:15"
        while True:
            item, duration = dialogs.pick_with_duration(
                "Something else in between", text, self._pick_items(), duration, remote=self._search_jira
            )
            if not item:
                return None
            try:
                seconds = parse_duration(duration)
            except ValueError as error:
                dialogs.message("Invalid duration", str(error))
                continue
            if seconds <= 0:
                dialogs.message("Invalid duration", "Enter a duration greater than 0.")
                continue
            if available is not None and seconds > available:
                dialogs.message("Too much time", f"There is only {format_duration(available)} to split.")
                continue
            activity = self._to_activity(item)
            if activity:
                return activity, seconds

    def _ask_call(self, started: float, ended: float) -> None:
        """'You were in a call 9:30–9:47': welke meeting of welk ticket? Je vaste opties staan bovenaan.

        Koos je rond deze tijd de laatste twee keer dezelfde meeting, dan staat die voorgeselecteerd (zie meetings.suggest).
        Geen antwoord: naar die meeting als er een voorstel is, anders blijft de tijd op het huidige ticket.
        """
        if self.tracker.status != TRACKING:
            return
        started = max(started, self.tracker.open_since)
        if ended - started < self.config.call_min_minutes * 60:
            return
        current = self.tracker.current
        short = current.label if current.is_fixed else current.key
        suggestion = suggest(self.store.fixed_segments(started - LOOKBACK_SECONDS, MEETING_MAX_SECONDS), started)
        fixed = [PickItem(f"@{o['label']}", o["label"], o.get("issue_key") or "fixed") for o in self.config.fixed_options]
        shown = {item.key for item in fixed} | {current.key}
        items = fixed + [PickItem(current.key, current.label, "current ticket")] + [
            item for item in self._pick_items() if item.key not in shown
        ]
        text = f"{hhmm(started)}–{hhmm(ended)} ({format_duration(int(ended - started))}). What was it?"
        if suggestion:
            text += f"\nSuggested: you picked {suggestion[1]} the last 2 times around {hhmm(started)}."
        while True:
            item, button = dialogs.pick(
                "You were in a call", text, items, suggestion[0] if suggestion else current.key,
                buttons=("Log", f"Keep on {short}"), timeout=self.tracker.idle_seconds, remote=self._search_jira,
            )
            if button == dialogs.TIMED_OUT:
                if not suggestion:
                    log.info("Call question unanswered; the call stays on the current ticket")
                    return
                activity = Activity(*suggestion)
                break
            if item is None or item.key == current.key:
                return
            activity = self._to_activity(item)
            if activity:
                if activity.is_fixed:
                    self._ask_fixed_key(activity.label)
                break
        self.tracker.carve(activity, started, ended)
        log.info("Call %s–%s logged on %s%s", hhmm(started), hhmm(ended), activity.key,
                 " (unanswered, suggested)" if button == dialogs.TIMED_OUT else "")

    def _ask_fixed_key(self, label: str) -> None:
        """Eenmalig: op welk Jira-ticket komt deze vaste optie? Zoekt in Jira op de naam en onthoudt het in de instellingen."""
        if self.config.fixed_issue_key(label):
            return
        name = re.sub(r'["\\]', "", label)
        try:
            found = self._jira().search(f'summary ~ "{name}" ORDER BY updated DESC', limit=20)
        except JiraError as error:
            log.warning("Searching Jira for a fixed option failed: %s", error)
            found = []
        items = [PickItem(issue.key, f"{issue.key} · {issue.summary}", "Jira") for issue in found]
        items += [item for item in self._pick_items() if not item.key.startswith("@") and item.key not in {i.key for i in items}]
        item, _ = dialogs.pick(
            f"Which Jira ticket is {label}?",
            "Asked once: it's remembered under Fixed options in Settings. Not listed? Type the key, e.g. IN-1234.",
            items, None, buttons=("Use", "Later"),
        )
        activity = self._to_activity(item) if item else None
        if activity is None or activity.is_fixed:
            return
        for option in self.config.fixed_options:
            if option["label"] == label:
                option["issue_key"] = activity.key
        save_config(self.config)

    def _ask_away(self, event: CameBack) -> None:
        """'Welcome back': wat deed je terwijl je weg was? Pas veel later beantwoord, dan telt die tijd ook als weg."""
        minutes = format_duration(int(event.back_at - event.away_since))
        current = self.tracker.current
        shown_at = time.time()
        item, button = dialogs.pick(
            "Welcome back",
            f"You were away from {hhmm(event.away_since)} to {hhmm(event.back_at)} ({minutes}). What did you do?"
            + (f"\nAfterwards you continue with {current.label}." if current else ""),
            [PAUSE_ITEM, *self._pick_items()],
            "",
            buttons=("Log and continue", "Log and stop for today", "Cancel"), remote=self._search_jira,
        )
        activity = self._to_activity(item) if item and item.key else None
        self.tracker.resolve_away(activity, event.back_at, resume=button != 1 and current is not None)
        answered_at = time.time()
        if answered_at - shown_at <= self.tracker.idle_seconds or self.tracker.status != TRACKING:
            return
        self.tracker.go_away(shown_at)
        if day_of(shown_at) != day_of(answered_at):
            self.tracker.resolve_away(None, answered_at, resume=False)  # over middernacht: de dag begint opnieuw
            log.info("Welcome back answered the next day; stopped at %s", hhmm(shown_at))
            return
        self._ask_away(CameBack(shown_at, answered_at))

    def _overview(self, day: str, end_of_day: bool) -> None:
        if day == today():
            self.tracker.flush(time.time())
        booked, booked_text, in_tempo, chips = self._tempo_bookings(day)
        title = datetime.strptime(day, "%Y-%m-%d").strftime("%A %-d %B")
        rows = build_overview(self.store, self.config, day, booked)
        from_commits = self._commit_descriptions(day)
        sent = self.store.sent_seconds(day)
        if not rows:
            if not end_of_day:
                dialogs.message(title, "Nothing left to send.\n" + self._totals_text([], in_tempo))
            return

        lines = [
            OverviewLine(
                issue_key=row.issue_key,
                label=row.label,
                tracked=format_duration(row.tracked_seconds) if row.first_started_at is not None else "",
                target_key=row.target_key,
                target_key_editable=row.is_fixed,
                duration=format_duration(row.seconds),
                description=row.description,
                description_placeholder=from_commits.get(row.target_key or row.issue_key)
                or default_description(row.issue_key, row.label),
                auto_duration=format_duration(row.auto_seconds) if row.edited else "",
            )
            for row in rows
        ] + [OverviewLine("", "", "", "", True, "", "") for _ in range(EMPTY_OVERVIEW_LINES)]
        first_started = {row.issue_key: row.first_started_at for row in rows}
        tracked = {row.issue_key: row.tracked_seconds for row in rows}
        auto = {row.issue_key: row.auto_seconds for row in rows if row.first_started_at is not None}

        while True:
            notes = ["Day overview"]
            if any(line.auto_duration for line in lines):
                notes.append("✎ = a time you changed")
            if sent:
                notes.append(f"already sent {format_duration(sent)}")
            intro = " · ".join(notes) + ("\n" + booked_text if booked_text else "")
            action, lines = dialogs.overview(
                title, intro, lines, totals=lambda entries: self._totals(entries, in_tempo), chips=chips
            )
            if action is None:
                return
            try:
                parsed = self._parse_lines(lines, first_started, tracked, require_keys=action == "send", auto=auto)
            except ValueError as error:
                dialogs.message("Check the overview", str(error))
                continue
            self._remember_fixed_keys(parsed)
            if action == "save":
                self._save_drafts(day, parsed)
                return
            result = self._send(day, parsed)
            if result == "back":
                continue
            return

    def _totals_text(self, entries: list[tuple[str, bool]], in_tempo: int | None) -> str:
        return self._totals(entries, in_tempo)[0]

    def _totals(self, entries: list[tuple[str, bool]], in_tempo: int | None) -> tuple[str, int | None, int, int]:
        """('In Tempo 1:15 + to send 5:45 = 7:00 of 8:00  ·  1:00 missing', in Tempo, te versturen, doel) in seconden."""
        to_send = 0
        for text, removed in entries:
            if not removed:
                try:
                    to_send += parse_duration(text)
                except ValueError:
                    pass
        if in_tempo is None:
            total, text = to_send, f"To send {format_duration(to_send)} (couldn't read Tempo)"
        else:
            total = in_tempo + to_send
            text = f"In Tempo {format_duration(in_tempo)} + to send {format_duration(to_send)} = {format_duration(total)}"
        target = parse_duration(self.config.day_target) if self.config.day_target else 0
        if target:
            text += f" of {format_duration(target)}  ·  "
            if total < target:
                text += f"{format_duration(target - total)} missing"
            elif total > target:
                text += f"{format_duration(total - target)} over"
            else:
                text += "complete ✓"
        return text, in_tempo, to_send, target

    def _tempo_bookings(self, day: str) -> tuple[list[tuple[float, float]], str, int | None, list[str]]:
        """Worklogs die al in Tempo staan.

        Geeft terug: de periodes die je zelf in Tempo zette (bijv. een meeting uit de suggesties; die gaan af
        van je getrackte tijd), een tekst voor in het overzicht, en het totaal in Tempo (None = niet te lezen).
        Alleen lezen; als Tempo niet bereikbaar is, wordt er niets afgetrokken.
        """
        own = self.store.own_worklog_ids()

        def read(_report) -> tuple[str, list, dict[str, str]]:
            """Op de achtergrond: worklogs van die dag en de Jira-keys van wat je niet via deze app verstuurde."""
            account_id = jira.myself()
            worklogs = tempo.worklogs(account_id, day)
            ids = {w.issue_id for w in worklogs if w.worklog_id not in own and w.issue_id}
            return account_id, worklogs, {issue_id: self._issue_key_for_id(jira, issue_id) for issue_id in ids}

        try:
            jira, tempo = self._jira(), self._tempo()
            account_id, worklogs, keys = dialogs.progress("Day overview", "Reading your worklogs from Tempo…", read)
        except (JiraError, TempoError) as error:
            log.warning("Reading worklogs from Tempo failed: %s", error)
            return [], f"Couldn't read your worklogs from Tempo ({error}). Nothing was subtracted.", None, []
        self.store.set_state("account_id", account_id)
        in_tempo = sum(w.seconds for w in worklogs)
        external = [w for w in worklogs if w.worklog_id not in own]
        if not external:
            return [], "", in_tempo, []

        periods, chips = self._booked_periods(day, external), []
        for worklog, (start, end) in zip(sorted(external, key=lambda w: w.start_time), periods):
            key = keys.get(worklog.issue_id, "?")
            description = worklog.description.split(" [")[0].strip(" -_")[:32]
            chips.append(f"{hhmm(start)}–{hhmm(end)}  {key}  {description}  {format_duration(worklog.seconds)}".replace("    ", "  "))
        if len(chips) > 9:
            chips = chips[:8] + [f"+ {len(chips) - 8} more"]
        return periods, "Meetings already in Tempo are subtracted from your tickets.", in_tempo, chips

    @staticmethod
    def _booked_periods(day: str, worklogs: list) -> list[tuple[float, float]]:
        """(begin, eind) van worklogs, op volgorde van begintijd."""
        periods = []
        for worklog in sorted(worklogs, key=lambda w: w.start_time):
            start = datetime.strptime(f"{day} {worklog.start_time}", "%Y-%m-%d %H:%M:%S").timestamp()
            periods.append((start, start + worklog.seconds))
        return periods

    def _issue_key_for_id(self, jira: JiraClient, issue_id: str) -> str:
        if not issue_id:
            return "?"
        if issue_id not in self._issue_keys:
            try:
                self._issue_keys[issue_id] = jira.get_issue(issue_id).key
            except JiraError:
                return f"issue {issue_id}"
        return self._issue_keys[issue_id]

    def _parse_lines(
        self, lines: list[OverviewLine], first_started: dict, tracked: dict, require_keys: bool, auto: dict | None = None
    ) -> list[_Line]:
        auto = auto or {}
        parsed, errors = [], []
        for line in lines:
            name = line.label or line.target_key or "new line"
            if line.removed:
                if line.issue_key:
                    parsed.append(
                        _Line(line.issue_key, line.label, line.target_key, 0, tracked.get(line.issue_key, 0), "", None)
                    )
                continue
            try:
                seconds = parse_duration(line.duration)
            except ValueError as error:
                errors.append(f"{name}: {error}")
                continue
            if not line.issue_key and not line.target_key and not seconds:
                continue
            target = line.target_key.strip().upper()
            if target and not ISSUE_KEY_RE.match(target):
                errors.append(f"{name}: '{target}' is not a valid Jira key")
                continue
            if not line.issue_key and not target:
                errors.append("New line: enter a Jira key")
                continue
            if require_keys and seconds and not target:
                errors.append(f"{name}: link a Jira key (e.g. an internal ticket for {name})")
                continue
            parsed.append(
                _Line(
                    issue_key=line.issue_key or target,
                    label=line.label or target,
                    target_key=target,
                    seconds=seconds,
                    tracked_seconds=tracked.get(line.issue_key, 0),
                    description=line.description,
                    first_started_at=first_started.get(line.issue_key),
                    default_description=line.description_placeholder,
                    auto_seconds=auto.get(line.issue_key, 0),
                )
            )
        if errors:
            raise ValueError("\n".join(errors))
        return parsed

    def _remember_fixed_keys(self, lines: list[_Line]) -> None:
        """Een Jira-key die je bij een vaste optie invult, wordt voortaan automatisch gebruikt."""
        changed = False
        for line in lines:
            if line.issue_key.startswith("@") and line.target_key:
                for option in self.config.fixed_options:
                    if option["label"] == line.issue_key[1:] and option.get("issue_key") != line.target_key:
                        option["issue_key"] = line.target_key
                        changed = True
        if changed:
            save_config(self.config)

    def _save_drafts(self, day: str, lines: list[_Line]) -> None:
        for line in lines:
            if line.seconds == 0:
                self.store.discard_unsent(day, line.issue_key)
            elif line.tracked_seconds and line.seconds == line.auto_seconds and not line.description:
                self.store.delete_draft(day, line.issue_key)  # niets aangepast: getrackte tijd blijft leidend
            else:
                self.store.save_draft(day, line.issue_key, line.label, line.seconds, line.tracked_seconds, line.description)

    def _send(self, day: str, lines: list[_Line]) -> str:
        """Verstuurt regel voor regel; mislukte regels blijven als concept staan. Geeft 'sent', 'back' of 'failed'."""
        to_send = [line for line in lines if line.seconds > 0]
        to_discard = [line for line in lines if line.seconds == 0]
        total = sum(line.seconds for line in to_send)
        summary = "\n".join(f"• {line.target_key}  {format_duration(line.seconds)}  {line.label}" for line in to_send)
        if not to_send and not to_discard:
            return "sent"
        if dialogs.choose(
            f"Send {len(to_send)} worklogs ({format_duration(total)}) to Tempo?",
            (summary or "Nothing will be sent.")
            + (f"\n\n{len(to_discard)} line(s) will be removed." if to_discard else ""),
            ["Send", "Back"],
        ) != 0:
            return "back"

        jobs = [
            (
                line,
                line.description or line.default_description or default_description(line.issue_key, line.label),
                datetime.fromtimestamp(line.first_started_at).strftime("%H:%M:%S") if line.first_started_at else "09:00:00",
            )
            for line in to_send
        ]

        def work(report) -> tuple[str, list[tuple[_Line, str, str | None, str | None]]]:
            """Op de achtergrond: per regel (regel, omschrijving, Tempo-id of None, fout of None)."""
            account_id = jira.myself()
            issues = jira.issues([line.target_key for line, _, _ in jobs])
            results = []
            for number, (line, description, start) in enumerate(jobs, 1):
                report(f"{number} of {len(jobs)} · {line.target_key}  {format_duration(line.seconds)}")
                issue = issues.get(line.target_key)
                if issue is None:
                    results.append((line, description, None, "not found in Jira"))
                    continue
                try:
                    worklog_id = tempo.create_worklog(issue.issue_id, account_id, day, start, line.seconds, description)
                    results.append((line, description, worklog_id, None))
                except TempoError as error:
                    results.append((line, description, None, str(error)))
            return account_id, results

        try:
            jira, tempo = self._jira(), self._tempo()
            account_id, results = dialogs.progress(
                "Sending to Tempo", f"0 of {len(jobs)}…" if jobs else "Removing lines…", work
            )
        except (JiraError, TempoError) as error:
            dialogs.message("Sending failed", str(error))
            self._save_drafts(day, lines)
            return "failed"
        self.store.set_state("account_id", account_id)

        failures = []
        for line, description, worklog_id, error in results:
            if worklog_id:
                self.store.record_worklog(day, line.issue_key, line.seconds, description, worklog_id)
            else:
                log.warning("Worklog for %s failed: %s", line.target_key, error)
                failures.append(f"{line.target_key}: {error}")
                self.store.save_draft(day, line.issue_key, line.label, line.seconds, line.tracked_seconds, line.description)
        for line in to_discard:
            self.store.discard_unsent(day, line.issue_key)

        if failures:
            dialogs.message("Not everything was sent", "These lines are still open:\n" + "\n".join(failures))
            return "failed"
        self._last_week_refresh = 0.0  # meteen de nieuwe stand uit Tempo lezen
        self._after_sent(day, f"{len(to_send)} worklogs, {format_duration(total)}.")
        return "sent"

    def _after_sent(self, day: str, text: str) -> None:
        """Verstuurd. Liep de tracking vandaag nog: klaar voor vandaag (Enter), pauze, of gewoon doorgaan."""
        if day != today() or self.tracker.status != TRACKING:
            dialogs.message("Sent to Tempo", text)
            return
        choice = dialogs.choose(
            "Sent to Tempo", text + "\nDone for today stops tracking until your next work day; Pause is for a break.",
            ["Done for today", "Pause", "Keep tracking"],
        )
        if choice == SENT_KEEP:
            return
        self.tracker.pause(time.time())
        if choice == SENT_DONE:
            self.store.set_state("done_day", today())
            self.store.set_state("start_prompted", today())  # vandaag geen 'Good morning' meer

    def _settings(self) -> None:
        config = self.config
        has_jira = bool(credentials.get_token(credentials.JIRA))
        has_tempo = bool(credentials.get_token(credentials.TEMPO))
        values = {
            "jira_site": config.jira_site,
            "email": config.email,
            "jira_token": "",
            "tempo_token": "",
            "tempo_base_url": config.tempo_base_url,
            "checkin_minutes": str(config.checkin_minutes),
            "idle_minutes": str(config.idle_minutes),
            "end_of_day": config.end_of_day,
            "round_minutes": str(config.round_minutes),
            "fixed_options": format_fixed_options(config.fixed_options),
            "git_folders": config.git_folders,
            "branch_switch": config.branch_switch,
            "shortcut": config.shortcut,
            "day_target": config.day_target,
            "week_reminder": config.week_reminder,
            "call_min_minutes": str(config.call_min_minutes),
            "work_days": config.work_days,
        }
        help_text = (
            "Jira token: id.atlassian.com → Security → API tokens → 'Create API token with scopes', "
            "app Jira, scopes read:jira-work and read:jira-user.\n"
            "Tempo token: Tempo → Settings → API Integration → New Token, "
            "Custom access: Worklogs View + Manage.\n"
            "Fixed options: 'Name=JIRA-KEY', separated by commas.\n"
            "Git: on a checkout of a branch with a ticket key (e.g. feature/IN-123-…) the app asks to switch (ask), "
            "switches by itself (auto) or does nothing (off). Commit messages become the worklog description.\n"
            "Work days: on other days the app doesn't ask anything by itself (e.g. mon-fri, or mon-thu).\n"
            "Calls: after a call (microphone in use) of at least this many minutes the app asks what it was; "
            "your fixed options are on top, e.g. Standup=IN-100, Refinement=IN-101."
        )
        while True:
            result = dialogs.form(
                "Settings",
                help_text,
                [
                    FormField("jira_site", "Jira site", values["jira_site"], "company.atlassian.net", section="Connection"),
                    FormField("email", "Jira email", values["email"], "name@company.com"),
                    FormField("jira_token", "Jira API token", values["jira_token"], "Saved · leave empty to keep" if has_jira else "", True),
                    FormField("tempo_token", "Tempo API token", values["tempo_token"], "Saved · leave empty to keep" if has_tempo else "", True),
                    FormField("tempo_base_url", "Tempo API URL", values["tempo_base_url"], "https://api.tempo.io/4"),
                    FormField("checkin_minutes", "Check in every (min)", values["checkin_minutes"], "45", section="Tracking"),
                    FormField("idle_minutes", "Idle after (min)", values["idle_minutes"], "10"),
                    FormField("work_days", "Work days", values["work_days"], "mon-fri"),
                    FormField("end_of_day", "Day overview at", values["end_of_day"], "18:00"),
                    FormField("round_minutes", "Round to (min)", values["round_minutes"], "15 · 0 = off"),
                    FormField("day_target", "Hours per day", values["day_target"], "8:00 · empty = don't show"),
                    FormField("week_reminder", "Week check", values["week_reminder"], "fri 15:00 · empty = off"),
                    FormField("call_min_minutes", "Ask about calls from (min)", values["call_min_minutes"], "5 · 0 = off"),
                    FormField("fixed_options", "Fixed options", values["fixed_options"], "Meetings=IN-100, Support, Other", section="Tickets"),
                    FormField("git_folders", "Git folders", values["git_folders"], "~/Projects · empty = off"),
                    FormField("branch_switch", "On branch switch", values["branch_switch"], "ask, auto or off"),
                    FormField("shortcut", "Shortcut", values["shortcut"], "ctrl+alt+t · empty = off"),
                ],
            )
            if result is None:
                return
            values = result
            try:
                self._apply_settings(result, has_jira, has_tempo)
            except ValueError as error:
                dialogs.message("Check your settings", str(error))
                continue
            break
        self._test_connection()

    def _apply_settings(self, values: dict, has_jira: bool, has_tempo: bool) -> None:
        def minutes(name: str, label: str, minimum: int) -> int:
            if not re.fullmatch(r"\d+", values[name].strip()) or int(values[name]) < minimum:
                raise ValueError(f"{label}: enter a whole number ≥ {minimum}")
            return int(values[name])

        site = normalize_site(values["jira_site"])
        email = values["email"].strip()
        if not site.endswith(".atlassian.net"):
            raise ValueError("Jira site must end in .atlassian.net")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address")
        base_url = values["tempo_base_url"].strip().rstrip("/")
        if not re.fullmatch(r"https://api(\.[a-z]+)?\.tempo\.io/4", base_url):
            raise ValueError("Tempo API URL must be https://api.tempo.io/4 (or a regional variant such as https://api.eu.tempo.io/4)")
        if not values["jira_token"].strip() and not has_jira:
            raise ValueError("Enter a Jira API token")
        if not values["tempo_token"].strip() and not has_tempo:
            raise ValueError("Enter a Tempo API token")
        parse_clock(values["end_of_day"])
        try:
            work_days = format_work_days(parse_work_days(values["work_days"]))
        except ValueError as error:
            raise ValueError(f"Work days: {error}") from None
        day_target = values["day_target"].strip()
        if day_target:
            try:
                parse_duration(day_target)
            except ValueError as error:
                raise ValueError(f"Hours per day: {error}") from None
        week_reminder = " ".join(values["week_reminder"].split()).lower()
        try:
            parse_reminder(week_reminder)
        except ValueError as error:
            raise ValueError(f"Week check: {error}") from None
        shortcut = values["shortcut"].strip().lower().replace(" ", "")
        if shortcut:
            parse_shortcut(shortcut)
        branch_switch = values["branch_switch"].strip().lower()
        if branch_switch not in BRANCH_MODES:
            raise ValueError("On branch switch: enter ask, auto or off")
        git_folders = ", ".join(f.strip() for f in values["git_folders"].split(",") if f.strip())
        for folder in git_folders.split(", ") if git_folders else []:
            if not os.path.isdir(os.path.expanduser(folder)):
                raise ValueError(f"Git folders: {folder} does not exist")

        self.config.jira_site = site
        self.config.email = email
        self.config.tempo_base_url = base_url
        self.config.checkin_minutes = minutes("checkin_minutes", "Check in every", 5)
        self.config.idle_minutes = minutes("idle_minutes", "Idle after", 1)
        self.config.round_minutes = minutes("round_minutes", "Round to", 0)
        self.config.call_min_minutes = minutes("call_min_minutes", "Ask about calls from", 0)
        self.config.end_of_day = values["end_of_day"].strip()
        self.config.work_days = work_days
        self.config.fixed_options = parse_fixed_options(values["fixed_options"])
        self.config.git_folders = git_folders
        self.config.branch_switch = branch_switch
        self.config.shortcut = shortcut
        self.config.day_target = day_target
        self.config.week_reminder = week_reminder
        self._last_repo_scan = 0.0
        self._repos = []
        if values["jira_token"].strip():
            credentials.set_token(credentials.JIRA, values["jira_token"])
        if values["tempo_token"].strip():
            credentials.set_token(credentials.TEMPO, values["tempo_token"])
        save_config(self.config)
        self.store.set_state("account_id", None)
        self._apply_config()
        self._register_shortcut()

    def _test_connection(self) -> None:
        try:
            jira = self._jira()
            account_id = jira.myself()
            self.store.set_state("account_id", account_id)
            self._tempo().check(account_id, _today_date().isoformat())
        except (JiraError, TempoError) as error:
            dialogs.message("Connection failed", str(error))
            return
        self._last_refresh = 0.0
        self._start_issue_refresh()
        dialogs.message("Connected", "Jira and Tempo work. Your issues are being fetched in the background.")
        if self.tracker.status == PAUSED and self.tracker.current is None:
            self._ask_start("What are you starting with?")

    # -- menu -----------------------------------------------------------------

    def on_switch(self, _sender) -> None:
        def switch() -> None:
            current = self.tracker.current
            choice = self._pick_activity_since(
                "Switch ticket",
                "From now (or from the start time) your time goes to this ticket.",
                current.key if current else None,
            )
            if choice:
                self.tracker.start(choice[0], time.time(), choice[1])

        self._dialog(switch)

    def on_add_time(self, _sender) -> None:
        def add_time() -> None:
            now = time.time()
            if self.tracker.status == TRACKING:
                available = self.tracker.open_seconds(now)
                text = (
                    f"For something small in between. This comes out of your time on {self.tracker.current.label} "
                    f"since {hhmm(self.tracker.open_since)}."
                )
            else:
                available = None
                text = "Added as a period ending now."
            extra = self._ask_extra(available, text)
            if extra:
                self.tracker.add_time(extra[0], extra[1], time.time())

        self._dialog(add_time)

    def on_toggle_pause(self, _sender) -> None:
        if self.tracker.status == TRACKING:
            self.tracker.pause(time.time())
            self._update_menu()
        elif self.tracker.current and self.tracker.status == PAUSED:
            self.tracker.start(self.tracker.current, time.time())
            self._update_menu()
        else:
            self._dialog(self._ask_start, "What are you starting with?")

    def on_overview_today(self, _sender) -> None:
        self._dialog(self._overview, today(), False)

    def on_refresh_issues(self, _sender) -> None:
        self._last_refresh = 0.0
        self._start_issue_refresh()

    def on_settings(self, _sender) -> None:
        self._dialog(self._settings)

    def on_quit(self, _sender) -> None:
        self.tracker.flush(time.time())
        rumps.quit_application()

