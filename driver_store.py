import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncio
from dataclasses import dataclass, asdict
try:
    import fcntl  # POSIX only
except Exception:
    fcntl = None
import time
import logging

# Configure logging to write to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('announcement_debug.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Load settings from config file
with open('config.json', 'r', encoding='utf-8') as f:
    settings = json.load(f)

# Get encoding settings
ENCODING_SETTINGS = settings.get('ENCODING_SETTINGS', {
    'DEFAULT_ENCODING': 'utf-8',
    'UNICODE_NORMALIZATION': 'NFC',
    'ENSURE_ASCII': False
})

@dataclass
class Driver:
    """Driver data class for storing driver information."""
    tg_id: int
    full_name: str
    phone: str
    active_until: datetime
    is_active: bool = True
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    folder_id: Optional[int] = None
    folder_title: Optional[str] = None
    session_string: Optional[str] = None
    two_factor_code: Optional[str] = None
    password: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert driver to dictionary for JSON storage."""
        return {
            'tg_id': self.tg_id,
            'full_name': self.full_name,
            'phone': self.phone,
            'active_until': self.active_until.isoformat(),
            'is_active': self.is_active,
            'api_id': self.api_id,
            'api_hash': self.api_hash,
            'folder_id': self.folder_id,
            'folder_title': self.folder_title,
            'session_string': self.session_string,
            'two_factor_code': self.two_factor_code,
            'password': self.password
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Driver':
        """Create driver from dictionary loaded from JSON."""
        return cls(
            tg_id=data['tg_id'],
            full_name=data['full_name'],
            phone=data['phone'],
            active_until=datetime.fromisoformat(data['active_until']),
            is_active=data['is_active'],
            api_id=data.get('api_id'),
            api_hash=data.get('api_hash'),
            folder_id=data.get('folder_id'),
            folder_title=data.get('folder_title'),
            session_string=data.get('session_string'),
            two_factor_code=data.get('two_factor_code'),
            password=data.get('password')
        )

    @property
    def is_expired(self) -> bool:
        """Check if driver's subscription has expired."""
        return datetime.now() > self.active_until

class DriverStore:
    """JSON-based storage for driver data."""
    def __init__(self, file_path: str = "drivers.json"):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._debounce_task: Optional[asyncio.Task] = None
        self._debounce_delay = 0.5  # seconds
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Ensure the JSON file exists."""
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                json.dump([], f, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])

    async def _acquire_file_lock(self, file_handle, timeout: float = 5.0) -> bool:
        """Acquire a file lock with timeout. No-op on non-POSIX."""
        if fcntl is None:
            return True
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                fcntl.flock(file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except IOError:
                await asyncio.sleep(0.1)
        return False

    async def _release_file_lock(self, file_handle):
        """Release the file lock. No-op on non-POSIX."""
        if fcntl is None:
            return
        try:
            fcntl.flock(file_handle, fcntl.LOCK_UN)
        except IOError:
            pass

    async def _read_data(self) -> List[Dict[str, Any]]:
        """Read data with in-memory cache; fall back to disk with file locking."""
        async with self._lock:
            if self._cache is not None:
                return self._cache
            try:
                with open(self.file_path, 'r', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                    if not await self._acquire_file_lock(f):
                        raise IOError("Could not acquire file lock for reading")
                    try:
                        data = json.load(f)
                        self._cache = data
                        return data
                    finally:
                        await self._release_file_lock(f)
            except json.JSONDecodeError:
                self._cache = []
                return []
            except Exception as e:
                raise IOError(f"Error reading driver data: {str(e)}")

    async def _flush_to_disk(self) -> None:
        """Flush current cache to disk with file locking."""
        async with self._lock:
            data = self._cache if self._cache is not None else []
            try:
                with open(self.file_path, 'w', encoding=ENCODING_SETTINGS['DEFAULT_ENCODING']) as f:
                    if not await self._acquire_file_lock(f):
                        raise IOError("Could not acquire file lock for writing")
                    try:
                        json.dump(data, f, indent=2, ensure_ascii=ENCODING_SETTINGS['ENSURE_ASCII'])
                    finally:
                        await self._release_file_lock(f)
            except Exception as e:
                raise IOError(f"Error writing driver data: {str(e)}")

    async def _schedule_flush(self) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await asyncio.sleep(0)
            except Exception:
                pass
        async def _delayed():
            try:
                await asyncio.sleep(self._debounce_delay)
                await self._flush_to_disk()
            except asyncio.CancelledError:
                pass
        self._debounce_task = asyncio.create_task(_delayed())

    async def get_driver(self, tg_id: int) -> Optional[Driver]:
        """Get driver by Telegram ID."""
        data = await self._read_data()
        for driver_data in data:
            if driver_data['tg_id'] == tg_id:
                return Driver.from_dict(driver_data)
        return None

    async def get_all_drivers(self) -> List[Driver]:
        """Get all drivers."""
        data = await self._read_data()
        return [Driver.from_dict(d) for d in data]

    async def get_active_drivers(self) -> List[Driver]:
        """Get all active drivers."""
        data = await self._read_data()
        return [Driver.from_dict(d) for d in data if d['is_active']]

    async def get_inactive_drivers(self) -> List[Driver]:
        """Get all inactive drivers."""
        data = await self._read_data()
        return [Driver.from_dict(d) for d in data if not d['is_active']]

    async def create_driver(self, driver: Driver) -> Driver:
        """Create a new driver."""
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if driver.full_name:
            driver.full_name = unicodedata.normalize('NFC', driver.full_name)
            log.info(f"Creating driver with normalized name: {driver.full_name}")
        
        data = await self._read_data()
        data.append(driver.to_dict())
        self._cache = data
        await self._schedule_flush()
        log.info(f"Created driver: {driver.full_name} (ID: {driver.tg_id})")
        return driver

    async def update_driver(self, driver: Driver) -> Driver:
        """Update an existing driver."""
        # Ensure proper text encoding for Unicode characters (including Cyrillic)
        import unicodedata
        if driver.full_name:
            driver.full_name = unicodedata.normalize('NFC', driver.full_name)
            log.info(f"Updating driver with normalized name: {driver.full_name}")
        if driver.folder_title:
            driver.folder_title = unicodedata.normalize('NFC', driver.folder_title)
            log.info(f"Updating driver with normalized folder title: {driver.folder_title}")
        
        data = await self._read_data()
        for i, d in enumerate(data):
            if d['tg_id'] == driver.tg_id:
                data[i] = driver.to_dict()
                self._cache = data
                await self._schedule_flush()
                log.info(f"Updated driver: {driver.full_name} (ID: {driver.tg_id})")
                return driver
        raise ValueError(f"Driver with tg_id {driver.tg_id} not found")

    async def delete_driver(self, tg_id: int) -> bool:
        """Delete a driver by Telegram ID."""
        data = await self._read_data()
        initial_length = len(data)
        data = [d for d in data if d['tg_id'] != tg_id]
        if len(data) < initial_length:
            self._cache = data
            await self._schedule_flush()
            log.info(f"Deleted driver with ID: {tg_id}")
            return True
        log.warning(f"Driver with ID {tg_id} not found for deletion")
        return False

# Create logger
log = logging.getLogger(__name__)

# Create a global instance
driver_store = DriverStore() 