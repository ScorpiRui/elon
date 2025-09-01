### Project Overview

- **Purpose**: Telegram bot for taxi drivers to broadcast messages to groups from a chosen Telegram folder at a fixed interval. Admins manage driver accounts and access.
- **Key Functions**:
  - Admins add/manage drivers and their access periods.
  - Drivers log in with personal Telegram API credentials and select a folder containing groups.
  - Drivers compose a message and choose an interval; the bot schedules and sends the message to all groups in the selected folder in batches, repeating on the chosen interval.
  - Drivers can stop broadcasting, start a new message, or delete their session.

### Tech Stack (as implemented)

- **Language**: Python 3
- **Telegram Frameworks**: `aiogram` (bot) and `telethon` (user client)
- **Storage**: JSON files on disk (`drivers.json`, `announcements.json`)
- **Scheduling**: Custom async loop (checks every 30 seconds)
- **Locking**: In-memory async locks; file-level locking via `fcntl` in driver storage

### Directory and Files

- `main.py`: Aiogram bot, handlers, FSM flows, menus, core user/admin flows
- `utils.py`: Announcement scheduler, Telethon client cache, folder peer fetching, retry logic, admin notifications
- `driver_store.py`: Driver model and JSON-backed persistence with file locks
- `announcement_store.py`: Announcement model and JSON-backed persistence
- `keyboards.py`: Reply and inline keyboards for bot UI
- `config.json`: Runtime configuration (token, admin IDs) — expected at project root
- `sessions/`: Directory for Telethon session files (created in the parent directory of the project root)

### Configuration

- `config.json` (required at runtime):
  - `BOT_TOKEN`: Telegram bot token
  - `ADMINS`: Array of admin Telegram user IDs (integers)

Example (illustrative):
```json
{
  "BOT_TOKEN": "123456:ABC...",
  "ADMINS": [111111111, 222222222]
}
```

### Data Models

- Driver (`driver_store.Driver`)
  - `tg_id: int` — Driver’s Telegram user ID
  - `full_name: str`
  - `phone: str`
  - `active_until: datetime` — Access expiry
  - `is_active: bool` — Active flag
  - `api_id: Optional[int]` — Driver’s API ID (my.telegram.org)
  - `api_hash: Optional[str]` — API hash
  - `folder_id: Optional[int]` — Selected Telegram folder ID
  - `folder_title: Optional[str]` — Selected folder title
  - `session_string: Optional[str]` — Telethon StringSession
  - `two_factor_code: Optional[str]`
  - `password: Optional[str]` — 2FA password, if set
  - Derived: `is_expired` property (now > active_until)

- Announcement (`announcement_store.AnnouncementData`)
  - `id: int` — Unique announcement ID
  - `driver_id: int` — Owner driver’s Telegram ID
  - `text: str` — Message body
  - `interval_min: int` — Interval in minutes between runs
  - `folder_title: str` — Folder name to pull peers from
  - `is_running: bool` — Active flag
  - `created_at: str (ISO)` — Auto-populated

### Persistence (JSON Files)

- `drivers.json`: Array of driver objects (see Driver fields). Dates stored in ISO format.
- `announcements.json`: Array of announcement objects (see Announcement fields).

### Runtime Components

- Aiogram Bot (`Bot`, `Dispatcher`, `Router`) in `main.py`
- Telethon Client cache and per-user locks in `utils.py`
- Global in-memory cache (`DBCache`) for driver and announcement state in `utils.py`
- Background scheduler loop (every 30s) to execute due announcements in `utils.py`

### Key Flows and Features

- Admin-only
  - View admin menu on `/start` if user is in `ADMINS`.
  - Add driver (guided FSM): name → phone → Telegram ID → active days; creates a `Driver` with active period.
  - List drivers with counts and pagination.
  - Search driver by phone; view details with inline actions.
  - Inline actions:
    - Extend driver by +30 days (from now or from current expiry)
    - Activate/Deactivate driver
    - Change driver’s Telegram ID
  - `/delete <telegram_id>`: Stop any announcements, remove session file, remove driver.

- Driver-only (must be active and not expired)
  - `/start`: If session exists, show main menu; else show login menu.
  - Login flow (FSM):
    1) Provide `api_id`
    2) Provide `api_hash`
    3) Provide phone → bot sends code via Telethon
    4) Provide code → Telethon sign-in; if 2FA required, provide password
    5) On success, `session_string` is saved
  - Select folder:
    - Provide folder name; Telethon lists dialog filters and finds exact matching folder (case-insensitive), saves `folder_id` and `folder_title`
  - Create and schedule message:
    - Provide message text; choose interval button: 2/5/10/15 minutes
    - Announcement is created and marked running
  - Stop broadcasting:
    - “Stop” button stops the running announcement and removes it from JSON
  - Start new message while running:
    - “Yangi xabar” stops existing, deletes announcement, and starts new text input
  - Delete session:
    - Removes Telethon session file for the user

- Chat Hygiene
  - For non-admins: deletes non-command messages and maintains only latest bot message with reply markup, cleaning intermediate messages when possible.

### Scheduling & Sending Logic

- Background scheduler loop (every 30s):
  - Loads all active announcements from `announcements.json`.
  - For each, maintains in-memory `AnnouncementState` with `last_run` to respect per-announcement interval.
  - When due, processes the announcement:
    - Retrieves driver and Telethon client (cached and auto-reconnect logic).
    - Finds peers from the selected folder via `GetDialogFiltersRequest()` and `include_peers`.
    - Sends messages in batches of 10 peers with retry logic:
      - Retries up to 3 times; handles `FloodWaitError` by sleeping the required seconds.
    - Aggregates success/failure counts and logs errors.

### Client & Session Management

- Per-user async locks around client creation/use
- Client cache with reconnect attempts
- Session persistence via Telethon `StringSession` (`driver.session_string`), as well as filesystem sessions in `sessions/`
- Periodic cache cleanup loop (in-memory) to disconnect and purge idle or disconnected clients

### Bot UI (Keyboards)

- Driver Main Menu (`keyboards.main_menu`)
  - When running: `Stop`, `Yangi xabar`
  - When not running: `Xabar Yaratish`, `Guruhlar papkasi`
- Login Menu: `Kirish`
- Admin Menu: `Haydovchi qo'shish`, `🚗 Haydovchilar`, `Qidirish`
- Driver Details (inline): `+30 kunga uzaytirish`, `O'chirish`/`Faollashtirish`, `ID ozgartirish`
- Interval chooser (inline): 2, 5, 10, 15 minutes
- Pagination (inline): prev/next page buttons

### Commands & Buttons (User-Facing)

- Commands
  - `/start`
  - `/delete <telegram_id>` (admin-only)
- Buttons (text messages)
  - Admin: `Haydovchi qo'shish`, `🚗 Haydovchilar`, `Qidirish`
  - Driver: `Kirish`, `Guruhlar papkasi`, `Xabar Yaratish`, `Stop`, `Yangi xabar`, `Delete session`

### Access Control

- Admin checks: user ID in `settings['ADMINS']`
- Driver activity checks: driver exists, `is_active` is True, and not `is_expired`

### Error Handling (selected)

- Login: distinguishes invalid code and 2FA requirements
- Folder selection: reports missing folder and lists available folder titles
- Message sending: retries with flood-wait handling and logs per-peer errors
- Cleanup: safe disconnection and cache purging

### Notes on Storage & Locking

- `driver_store.py` uses `fcntl`-based file locking around JSON read/write (POSIX-only). The app also has an in-memory asyncio lock per store operation. `announcement_store.py` uses only an in-memory lock.

### Deployment & Run

- Required environment:
  - Python 3 with `aiogram`, `telethon`
  - `config.json` present at project root
- Start the bot:
  - Run `python main.py`
  - The dispatcher starts polling and the scheduler loop is launched in the background

### Batching & Rate Limits

- Batch size: 10 peers per batch
- Retries: up to 3 per peer with exponential/fixed backoffs
- `FloodWaitError`: respected by sleeping for the specified number of seconds

### Files and Responsibilities (Index)

- `main.py`
  - Bot lifecycle: `/start`, command/button handlers, FSM definitions and flows
  - Driver management views and actions (admin)
  - Driver login, folder selection, announcement creation, stop/new message, delete session
  - Chat cleanup utilities
  - Bot startup (`ensure_scheduler_running`, `start_polling`) and shutdown cleanup

- `utils.py`
  - Scheduler loop (`scheduler_loop`), announcement execution (`execute_due_announcements`)
  - Per-user Telethon client caching and reconnection (`get_cached_client`)
  - Folder peer enumeration (`get_folder_peers`)
  - Batched sending with retry (`process_peer_batch`, `send_message_with_retry`)
  - In-memory caches (`DBCache`), announcement in-memory state (`AnnouncementState`)
  - Admin notifications (via bot) helpers
  - Client cleanup routines

- `driver_store.py`
  - Driver dataclass, JSON serialization/deserialization
  - Create/read/update/delete driver records with file locking
  - Global `driver_store` instance

- `announcement_store.py`
  - Announcement dataclass, JSON serialization/deserialization
  - Create/read/update/delete announcement records
  - Query running announcements
  - Global `announcement_store` instance

- `keyboards.py`
  - Reply keyboards: login and main menus
  - Inline keyboards: driver details, pagination, interval selection

### Assumptions and Constraints (as implemented)

- Drivers provide their own Telegram API credentials and authenticate their sessions; the bot uses those sessions to send messages to groups the driver can access.
- Announcements are repeated indefinitely at the selected interval until stopped.
- Folder name match is case-insensitive and must match exactly one dialog filter.
- JSON files act as the single source of truth for drivers and announcements.

### Internationalization

- User-facing text is primarily in Uzbek.

### Security Considerations (implementation behavior)

- Driver 2FA passwords and sessions are stored in JSON and local session files respectively.
- Access is controlled by admin list and driver active/expiry flags.

### Limitations (from current code)

- `fcntl` locking is not available on Windows; `driver_store.py` locking relies on POSIX.
- JSON storage has no concurrency beyond simple locks and no schema migration support.
- Scheduler precision is ±30 seconds by design.
