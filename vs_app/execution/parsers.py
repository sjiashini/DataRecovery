"""Output parsers for ddrescue and PhotoRec."""

import re
import time

DD_PCT_RE = re.compile(
    r'pct rescued:\s*([\d.]+)%.*?read errors:\s*(\d+).*?remaining time:\s*(.+?)\s*$')
DD_RESCUED_RE = re.compile(r'rescued:\s*([\d.]+ \S*B),\s*bad areas:\s*(\d+)')
DD_RATE_RE = re.compile(r'average rate:\s*([\d.]+ \S*B/s)')
DD_STATUS_RE = re.compile(
    r'^\s*(ipos|opos|non-tried|rescued|pct rescued|time since last)\b|^\s*$')
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
PR_PASS_RE = re.compile(r'Pass (\d+) - Reading sector\s+(\d+)/(\d+)(?:,\s*(.+))?')
PR_TIME_RE = re.compile(r'Elapsed time (\S+) - Estimated time to completion (\S+)')


def hms_to_text(value):
    match = re.match(r'(\d+)h(\d+)m(\d+)', value or '')
    if not match:
        return value
    hours, minutes, seconds = (int(part) for part in match.groups())
    return f'{hours}h {minutes:02d}m' if hours else f'{minutes}m {seconds:02d}s'


def new_progress(state):
    return {'state': state, 'pct': None, 'eta': None, 'detail': '',
            'phase': '', 'seconds': None, 'started': None}


def fmt_duration(seconds):
    if seconds is None:
        return 'n/a'
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class DdrescueParser:
    """Turn ddrescue's status lines into progress fields."""

    def feed_line(self, line):
        updates = {}
        line = ANSI_RE.sub('', line)
        match = DD_PCT_RE.search(line)
        if match:
            updates['pct'] = float(match.group(1))
            updates['errors'] = int(match.group(2))
            eta = match.group(3).strip()
            updates['eta'] = None if eta in ('n/a', '0s') else eta
        match = DD_RESCUED_RE.search(line)
        if match:
            updates['rescued'] = match.group(1)
            updates['bad_areas'] = int(match.group(2))
        match = DD_RATE_RE.search(line)
        if match:
            updates['rate'] = match.group(1)
        if 'pct rescued' in line:
            updates.setdefault('detail', line.strip()[:160])
        return updates

    @staticmethod
    def quiet_line(line):
        """ddrescue's status table: parsed into progress, never logged verbatim."""
        return bool(DD_STATUS_RE.search(line))


class PhotorecParser:
    """Feed raw ncurses bytes and extract pass/sector/ETA progress."""

    def __init__(self, image_size=0):
        self.screen = TermScreen()
        self.image_sectors = max(0, image_size) // 512
        self.pass_number = None
        self._last_summary = 0.0

    def feed_bytes(self, chunk):
        self.screen.feed(chunk)
        updates = {}
        for line in self.screen.lines():
            match = PR_PASS_RE.search(line)
            if match:
                step, current, total, extra = match.groups()
                current, total = int(current), int(total)
                if self.image_sectors:
                    total = min(total, self.image_sectors)
                if total:
                    updates['pct'] = round(min(current, total) * 100.0 / total, 2)
                updates['sector'] = current
                updates['total_sectors'] = total
                updates['phase'] = (f'Pass {step} — detecting block size' if step == '0'
                                    else f'Pass {step} — carving')
                updates['detail'] = (f"sector {current:,} / {total:,}"
                                     + (f" · {extra.strip()}" if extra else ''))
            match = PR_TIME_RE.search(line)
            if match:
                updates['elapsed_tool'] = hms_to_text(match.group(1))
                updates['eta'] = hms_to_text(match.group(2))
                sector = updates.get('sector')
                total = updates.get('total_sectors')
                elapsed_match = re.match(r'(\d+)h(\d+)m(\d+)', match.group(1))
                if (elapsed_match and self.image_sectors and total == self.image_sectors
                        and sector and 0 < sector < total):
                    elapsed = (int(elapsed_match.group(1)) * 3600
                               + int(elapsed_match.group(2)) * 60
                               + int(elapsed_match.group(3)))
                    remaining = int(elapsed * (total - sector) / sector)
                    updates['eta'] = hms_to_text(
                        f'{remaining // 3600}h{remaining % 3600 // 60}m{remaining % 60}')
        return updates

    def summary_due(self, seconds=30):
        now = time.time()
        if now - self._last_summary >= seconds:
            self._last_summary = now
            return True
        return False


class TermScreen:
    """Minimal VT100 screen, enough to rebuild PhotoRec's ncurses status rows."""

    _ESC = re.compile(rb'\x1b(?:\[([?=>]?)([0-9;]*)([@-~])|[()][0-9A-Za-z]|[=>M78DEc])')

    def __init__(self, rows=24, cols=80):
        self.rows, self.cols = rows, cols
        self.buf = [[' '] * cols for _ in range(rows)]
        self.r = self.c = 0
        self._pending = b''

    def feed(self, data):
        data = self._pending + data
        self._pending = b''
        index, length = 0, len(data)
        while index < length:
            byte = data[index]
            if byte == 0x1b:
                match = self._ESC.match(data, index)
                if not match:
                    if length - index < 32:
                        self._pending = data[index:]
                        return
                    index += 1
                    continue
                if match.group(3) is not None and not match.group(1):
                    self._csi(match.group(2).decode(), match.group(3).decode())
                index = match.end()
                continue
            if byte == 0x0d:
                self.c = 0
            elif byte == 0x0a:
                self.r = min(self.r + 1, self.rows - 1)
            elif byte == 0x08:
                self.c = max(self.c - 1, 0)
            elif byte >= 0x20:
                if 0 <= self.r < self.rows and 0 <= self.c < self.cols:
                    self.buf[self.r][self.c] = chr(byte) if byte < 0x80 else '?'
                self.c += 1
            index += 1

    def _csi(self, params, cmd):
        values = [int(item) if item else 0 for item in params.split(';')] if params else []
        first = values[0] if values else 0
        row = self.buf[self.r] if 0 <= self.r < self.rows else None
        if cmd in ('H', 'f'):
            self.r = (values[0] if len(values) > 0 and values[0] else 1) - 1
            self.c = (values[1] if len(values) > 1 and values[1] else 1) - 1
        elif cmd == 'd':
            self.r = (first or 1) - 1
        elif cmd == 'G':
            self.c = (first or 1) - 1
        elif cmd == 'A':
            self.r = max(self.r - (first or 1), 0)
        elif cmd == 'B':
            self.r = min(self.r + (first or 1), self.rows - 1)
        elif cmd == 'C':
            self.c += first or 1
        elif cmd == 'D':
            self.c = max(self.c - (first or 1), 0)
        elif cmd == 'J' and first == 2:
            self.buf = [[' '] * self.cols for _ in range(self.rows)]
        elif cmd == 'K' and row is not None:
            if first == 0:
                row[self.c:] = [' '] * (self.cols - self.c)
            elif first == 1:
                row[:self.c + 1] = [' '] * (self.c + 1)
            else:
                row[:] = [' '] * self.cols
        elif cmd == 'P' and row is not None:
            del row[self.c:self.c + (first or 1)]
            row.extend([' '] * (self.cols - len(row)))
        elif cmd == 'X' and row is not None:
            count = len(row[self.c:self.c + (first or 1)])
            row[self.c:self.c + count] = [' '] * count
        elif cmd == '@' and row is not None:
            row[self.c:self.c] = [' '] * (first or 1)
            del row[self.cols:]

    def lines(self):
        return [''.join(row).rstrip() for row in self.buf]
