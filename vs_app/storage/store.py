"""SQLite persistence for jobs and their replayable event streams."""

import json
import os
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id       TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,
    status   TEXT NOT NULL,
    stage    TEXT,
    created  REAL NOT NULL,
    started  REAL,
    finished REAL,
    error    TEXT,
    snapshot TEXT NOT NULL,
    updated  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_kind_created ON jobs(kind, created DESC);
CREATE TABLE IF NOT EXISTS events (
    job_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
"""


class JobStore:
    def __init__(self, path):
        self.path = path
        self._db = None

    async def open(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute('PRAGMA journal_mode=WAL')
        await self._db.execute('PRAGMA synchronous=NORMAL')
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        return self

    async def close(self):
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def save_job(self, record):
        snapshot = json.dumps(record['snapshot'], ensure_ascii=False)
        await self._db.execute(
            'INSERT INTO jobs (id, kind, status, stage, created, started, finished, error, '
            'snapshot, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(id) DO UPDATE SET status=excluded.status, stage=excluded.stage, '
            'started=excluded.started, finished=excluded.finished, error=excluded.error, '
            'snapshot=excluded.snapshot, updated=excluded.updated',
            (record['id'], record['kind'], record['status'], record.get('stage'),
             record['created'], record.get('started'), record.get('finished'),
             record.get('error'), snapshot, time.time()))
        await self._db.commit()

    async def append_event(self, job_id, event):
        await self._db.execute(
            'INSERT OR REPLACE INTO events (job_id, seq, ts, type, payload) VALUES (?, ?, ?, ?, ?)',
            (job_id, event['id'], event['ts'], event['type'],
             json.dumps(event['data'], ensure_ascii=False)))
        await self._db.commit()

    async def events_since(self, job_id, after=0, limit=2000):
        cursor = await self._db.execute(
            'SELECT seq, ts, type, payload FROM events WHERE job_id=? AND seq>? '
            'ORDER BY seq LIMIT ?', (job_id, after, limit))
        rows = await cursor.fetchall()
        await cursor.close()
        return [{'id': row['seq'], 'type': row['type'], 'ts': row['ts'],
                 'data': json.loads(row['payload'])} for row in rows]

    async def get_job(self, job_id):
        cursor = await self._db.execute('SELECT snapshot FROM jobs WHERE id=?', (job_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return json.loads(row['snapshot']) if row else None

    async def latest_for_kind(self, kind):
        cursor = await self._db.execute(
            "SELECT snapshot FROM jobs WHERE kind=? AND status NOT IN ('queued','running') "
            'ORDER BY created DESC LIMIT 1', (kind,))
        row = await cursor.fetchone()
        await cursor.close()
        return json.loads(row['snapshot']) if row else None

    async def recent(self, limit=25):
        cursor = await self._db.execute(
            'SELECT id, kind, status, stage, created, started, finished, error FROM jobs '
            'ORDER BY created DESC LIMIT ?', (limit,))
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def mark_interrupted(self):
        """Jobs left queued/running by a crash can never finish; close them explicitly."""
        message = 'Backend restarted while this job was running.'
        cursor = await self._db.execute(
            "SELECT id, snapshot FROM jobs WHERE status IN ('queued','running')")
        rows = await cursor.fetchall()
        await cursor.close()
        now = time.time()
        for row in rows:
            snapshot = json.loads(row['snapshot'])
            snapshot.update(active=False, done=True, status='failed', stage='failed',
                            outcome='failed', error=message, finished=now)
            if snapshot.get('progress'):
                for progress in snapshot['progress'].values():
                    if progress.get('state') in ('running', 'pending'):
                        progress['state'] = 'failed'
            await self._db.execute(
                "UPDATE jobs SET status='failed', stage='failed', finished=?, error=?, "
                'snapshot=?, updated=? WHERE id=?',
                (now, message, json.dumps(snapshot, ensure_ascii=False), now, row['id']))
        if rows:
            await self._db.commit()
        return len(rows)
