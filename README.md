# Tempo Tracker

A macOS menu bar app that keeps track of what you work on, so you don't have to fill in your Tempo timesheet from memory.

It asks every 45 minutes whether you're still on the same ticket, notices branch switches, calls and breaks, and keeps everything locally. At the end of the day you check a day overview and send it to Tempo as worklogs. **Nothing goes to Tempo until you press *Send* and confirm.**

## Contents

- [Install](#install)
- [How it works in the UI](#how-it-works-in-the-ui)
- [How it works technically](#how-it-works-technically)
- [Privacy](#privacy)
- [Files](#files)
- [Update and uninstall](#update-and-uninstall)
- [Development](#development)

## Install

### What you need

- **macOS** (developed and tested on macOS 26).
- **Python 3.10–3.14.** If you don't have it: `brew install python@3.13`.
- **Access to this repo** through SSH (`ssh -T git@github.com` should greet you).
- **A Jira API token** (read-only) and **a Tempo API token**. See [Create the tokens](#create-the-tokens).

### Steps

```bash
git clone git@github.com:omniboost/tempo-tracker.git ~/Projects/tempo-tracker
cd ~/Projects/tempo-tracker
./install.sh
```

`install.sh` does four things:
1. finds a suitable Python;
2. creates a virtualenv in `.venv`;
3. installs `requirements.txt` from the public PyPI;
4. installs a LaunchAgent (`io.omniboost.tempo-tracker`), so the app starts now and every time you log in.

A ⏱ appears in your menu bar. On first start the app asks for your settings: Jira site, email and the two tokens.

### Create the tokens

**Jira** (read-only):
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens.
2. Choose **Create API token with scopes**, app **Jira**, scopes `read:jira-work` and `read:jira-user`.
3. Set an expiry date (e.g. 90 days).

**Tempo**:
1. In Tempo, go to **Settings → API Integration → New Token**.
2. Choose **Custom access** and select only **Worklogs: View + Manage**.

Paste both into **Settings…** in the app. They're stored in your macOS Keychain, never in a file.

### Set up your standard meetings

In **Settings → Fixed options**, list your recurring meetings and other fixed activities. Each one gets its own Jira ticket:

```
Standup=IN-100, Backlog Refinement=IN-101, Support Work=IN-102, Other
```

You don't need to know the keys up front. Leave them out (`Standup, Retro`), and the first time you pick one after a call, the app searches Jira by its name and asks which ticket it is. It remembers your answer.

## How it works in the UI

### A normal day

| When | What you see |
|---|---|
| First activity on a work day | **Good morning! What are you starting with?** Pick a ticket. If you started earlier, choose *5m ago* … *30m* or type a time like `9:00`. |
| Every 45 min | **Still working on IN-1234?** Enter = yes. |
| You check out a branch with a ticket key | **Switch to IN-5678?** The new ticket counts from the moment of the checkout. |
| After a call | **You were in a call 9:30–9:47. What was it?** Pick a meeting or ticket, or keep it on your current ticket. |
| You were away (>10 min idle, Mac asleep, screen off or locked) | **Welcome back. What did you do?** Default is *Break*, which isn't logged. |
| 18:00 | **Day overview.** Check it and send it to Tempo. |
| After sending | **Done for today** (Enter), **Pause** or **Keep tracking**. |
| Friday 15:00 | **Your week**: per day what's in Tempo and which days still need attention. |

### The check-in

- **Yes** (Enter): the time since the last check-in goes to the current ticket.
- **Plus something else…**: spent 15 minutes on something else? Pick the ticket and duration. That time comes out of the period and the rest stays on the current ticket. You can use it several times per check-in.
- **Other ticket…**: you switched. Choose since when: by default the whole period since the last check-in moves to the new ticket, or only the last 5–30 minutes.
- **Take a break** pauses tracking. **Ask again in 15 min** postpones the question.
- **Unanswered after 10 minutes**: if you were away, the time from the question onwards counts as away. If you were active but didn't see it, it counts as *Yes*.

### Calls and meetings

- **During a call** (any app using a microphone: Teams, Zoom, Meet) there are no check-ins. Not typing during a call doesn't count as idle.
- **After a call of 5 minutes or more**, you get a list:
  - your fixed options first;
  - then your current ticket;
  - then recent and assigned tickets.

  Type to search. **Log** puts the call on your choice and takes that time off your current ticket. **Keep on …** (or Esc) leaves it on your current ticket.
- **Suggested meeting**: if you picked the same meeting the last 2 times for a call around this time, it's preselected and the question says why. "Around this time" means within 15 minutes, on the same weekday in the last 4 weeks or on any day in the last week. An unanswered question goes to the suggestion, or stays on your ticket if there is none.
- **The next check-in** shows the call as its own line.
- **Meetings logged in Tempo itself** (e.g. from the calendar suggestions in your timesheet) are subtracted in the day overview, so nothing is counted twice.

### Switching tickets

- **Switch ticket…** in the menu, or **⌃⌥T** from anywhere. Search by key or title (text also searches Jira after a short pause), or type a key like `IN-1234`.
- **Recent tickets** are listed right under *Switch ticket…* in the menu: one click switches.
- **Add time…** in the menu: book a piece of time on another ticket in between, without waiting for the check-in.
- **Git**: check out a branch with a ticket key (e.g. `feature/IN-42-split-bills`) in a repo under your git folders. The app asks whether to switch, or switches by itself if *On branch switch* is `auto`. The time up to the checkout stays on the previous ticket. Branches without a key, such as `main`, change nothing.

### Away, nights and weekends

- **Away** means more than 10 minutes without keyboard or mouse, the Mac asleep, or the screen off or locked. When you're back, the app asks what you did.
- **Late answers**: if you answer *Welcome back* much later, the time in between counts as away too.
- **Away since a previous day** (e.g. laptop closed overnight): the app asks nothing about yesterday. Tracking stopped when you left, and today starts with *Good morning*.
- **Work days** (default `mon-fri`): on other days the app doesn't start asking by itself. If you start something yourself, everything works as usual.
- **Laptop closed at the office and continuing at home?** Don't choose *Done for today*. You'll get *Welcome back* at home; choose *Break* for the travel time.

### The day overview

- **One line per ticket**, with the tracked time rounded to 15 minutes. You can change:
  - the duration; a ✎ appears, and clicking it restores the tracked time;
  - the description;
  - the Jira key, for fixed options.
- **Removing and adding**: remove a line with ✕, or add a line for something that wasn't tracked.
- **Descriptions** come from your commit messages of that day when they mention the ticket (`feat(IN-42): add tips` → `Add tips`). Otherwise the issue title is used. Your own text always wins.
- **Totals at the bottom**: what's in Tempo plus what you're about to send, against your hours per day. For example: `In Tempo 1:15 + to send 5:45 = 7:00 of 8:00 · 1:00 missing`.
- **Save** keeps your edits for later. **Send to Tempo** shows a summary first and only sends after you confirm. Sending runs in the background with a progress card.
- **After sending today**:
  - **Done for today** stops tracking until your next work day;
  - **Pause** is for a break, with *Resume* in the menu;
  - **Keep tracking** carries on.

### The menu

| Item | What it does |
|---|---|
| Header | Current ticket, since when, and today's total against your hours per day. |
| Pause / Resume | Pause tracking, or continue with the last ticket. |
| Switch ticket… | Pick another ticket from now (or from an earlier time). |
| Recent tickets | One click switches. |
| Add time… | Book a piece of time on another ticket in between. |
| This week | Per day what's in Tempo and what's still to send. Click a day to open its overview. Older unsent days are listed below. |
| Day overview… | Today's overview. |
| Refresh Jira issues | Reload your assigned and recent issues. |
| Settings… | All settings (see below). |

### Settings

| Setting | Default | Meaning |
|---|---|---|
| Jira site, email, tokens | – | Connection. Tokens go to the Keychain. |
| Tempo API URL | `https://api.tempo.io/4` | Or a regional variant such as `https://api.eu.tempo.io/4`. |
| Check in every (min) | 45 | Interval of the check-in. |
| Idle after (min) | 10 | No input for this long counts as away. |
| Work days | `mon-fri` | e.g. `mon-thu` or `mon, tue, thu`. |
| Day overview at | 18:00 | When the day overview opens by itself. |
| Round to (min) | 15 | Rounding in the day overview; 0 = off. |
| Hours per day | 8:00 | For the totals and the week; empty = don't show. |
| Week check | `fri 15:00` | Weekly summary; empty = off. |
| Ask about calls from (min) | 5 | Shorter calls stay on your ticket; 0 = never ask. |
| Fixed options | `Meetings, Support, Other` | Meetings and other fixed activities, `Name=JIRA-KEY`. |
| Git folders | `~/Projects` | Where to look for repos; empty = off. |
| On branch switch | `ask` | `ask`, `auto` or `off`. |
| Shortcut | `ctrl+alt+t` | Opens *Switch ticket…*; empty = off. |

## How it works technically

### Overview

A Python app on top of [rumps](https://github.com/jaredks/rumps) (menu bar) and PyObjC (native windows). A LaunchAgent starts it at login and restarts it if it crashes.

| Module | Role |
|---|---|
| `app.py` | The menu bar app: the 15-second tick, all questions, the day overview, sending, settings. |
| `tracker.py` | Time tracking as a state machine, without UI or clock, so it's fully testable. |
| `store.py` | SQLite: time segments, sent worklogs, drafts, cached Jira issues, state. |
| `summary.py` | Builds the day overview: per ticket, rounded, minus what's already in Tempo. |
| `week.py` | Week overview, days that need attention, work days, the week check. |
| `meetings.py` | The rule that suggests which meeting a call was. |
| `calls.py` | Is a microphone in use? (CoreAudio) |
| `idle.py` | Seconds since the last keyboard/mouse input (`HIDIdleTime`), and whether the screen is off or locked (CoreGraphics). |
| `git.py` | Finds repos, watches `.git/HEAD` for branch switches, reads commit messages. |
| `jira.py`, `tempo.py` | REST clients for Jira Cloud and Tempo API v4. |
| `hotkey.py` | Global shortcut (Carbon `RegisterEventHotKey`). |
| `dialogs.py`, `ui.py` | The native "card" windows: lists, forms, the day overview, the progress card. |
| `config.py`, `credentials.py` | `config.json` and the Keychain. |

### The tick

Every 15 seconds `on_tick` does the following, in order:
1. Once a day, remove old days that are fully sent (see [Files](#files)).
2. If the screen is off or locked, tell the tracker you're not there and stop. No questions are asked while you can't see them.
3. Check the microphone. During a call, idle time counts as 0. Right after a call, idle time counts from the end of the call.
4. Let the tracker tick with the current idle time. It returns at most one event: *came back*, *check-in due*, *good morning* or *end of day*.
5. Check the git repos for a branch switch.
6. Show at most one question, in a fixed priority: *welcome back*, then the call question, then the check-in, the branch switch, the morning question, the day overview and the week check.

### The tracker model

- **The open period.** There is always at most one open period: the time since the last check-in, for the current ticket. Answering a check-in, switching, pausing or going away closes it and writes it to the database as a **segment** (ticket, start, end).
- **Calls are carved out.** A call is written as its own segment right away and recorded in `carved`. When the open period closes later, the ticket only gets the time around the call.
- **Extras come off the end.** *Plus something else* and *Add time* take their time from the end of the period, skipping calls.
- **Detecting sleep.** If two ticks are more than 3 minutes apart, the Mac was asleep, and the tracker goes away from the last tick.
- **A new day.** If you're away since a previous day, the tracker stops at the moment you left and doesn't ask about it.
- **Persistence.** The tracker state is saved after every change, so a restart (or crash) continues where it left off.

### The day overview

1. Take the unsent segments of the day and add them up per ticket.
2. Subtract the overlap with worklogs that are already in Tempo but weren't sent by this app, such as meetings logged from the Tempo calendar.
3. Round each line to 15 minutes. A line that had time is never rounded down to 0.
4. Apply saved drafts (your edits). Time tracked after saving is added on top.

### Sending

1. You press *Send* and confirm the summary.
2. In a background thread, the app:
   - looks up all Jira issue ids in one search (`key in (…)`);
   - creates one Tempo worklog per line (`POST /4/worklogs`), starting at the first tracked moment of that ticket.

   A progress card shows *"3 of 7 · IN-3224 1:30"* and the app keeps responding.
3. Back on the main thread, sent segments are marked with their worklog. Failed lines stay as drafts, so you can retry.

### The meeting suggestion

The app reads short segments (at most 2 hours) on fixed options from the last 4 weeks. It keeps those that started within 15 minutes of the call's time of day and fall:
- on the same weekday, within the last 4 weeks, or
- on any day within the last 7 days.

It takes the newest one per day, and today doesn't count. If the two newest are the same option, that's the suggestion. There's no separate learning store: the suggestion is computed from your own tracked time.

### Network calls

| Service | Call | When | Writes? |
|---|---|---|---|
| Jira | `GET /myself`, issue search, `GET /issue/{key}` | Every 30 min (your assigned and recent issues), when looking up a key, before sending | No |
| Tempo | `GET /4/worklogs/user/{accountId}` | Every 15 min for this week; when opening the day overview or the week check | No |
| Tempo | `POST /4/worklogs` | Only after *Send to Tempo* and your confirmation | **Yes** |

Jira scoped tokens only work through `api.atlassian.com`. The client finds out once which endpoint works and keeps using it.

## Privacy

- **Microphone:** the app only asks macOS whether a microphone is in use. It never reads, records or stores audio, and doesn't know which app uses it.
- **Keyboard and mouse:** the app only reads the system counter "seconds since last input". No keylogging, and no Accessibility or Input Monitoring permission is needed.
- **Screen:** the app only checks whether the display is asleep or the session is locked.
- **Git:** only branch names and your own commit messages of the day, read locally.
- **Local storage:** everything stays on your Mac until you send a day overview.
- **Tokens:** they're in the Keychain, and never in the log or in files.

## Files

| What | Where |
|---|---|
| Settings | `~/Library/Application Support/TempoTracker/config.json` |
| Tracked time | `~/Library/Application Support/TempoTracker/tracker.db` |
| Log | `~/Library/Logs/TempoTracker.log` |
| Tokens | macOS Keychain, service `tempo-tracker` |
| LaunchAgent | `~/Library/LaunchAgents/io.omniboost.tempo-tracker.plist` |

Days that are completely sent to Tempo are removed from `tracker.db` after 35 days. Days with anything not sent are kept. The database stays well under 1 MB.

## Update and uninstall

Update:

```bash
cd ~/Projects/tempo-tracker
git pull
./install.sh
```

Restart without reinstalling:

```bash
launchctl kickstart -k gui/$(id -u)/io.omniboost.tempo-tracker
```

Uninstall:

```bash
./uninstall.sh
```

This removes the LaunchAgent and the tokens. Your tracked time stays in `~/Library/Application Support/TempoTracker`; delete that folder yourself if you no longer need it.

## Development

Run the tests:

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

- **No real windows.** The tests never open real windows: `tests/__init__.py` makes that fail loudly, runs progress cards inline, and pins the screen to "on" and the microphone to "off" so results don't depend on your Mac.
- **No clock.** The tracker takes the time as an argument, so tests drive it with fixed timestamps.

## License

MIT, see [LICENSE](LICENSE).
