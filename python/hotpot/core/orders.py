"""core/orders.py — doc section 9.7's order database, `state/orders.sqlite3`.

The schema is doc section 9.7's, with two columns added and one meaning
pinned down:

- `paid_at REAL` — doc section 18.2 has the receipt page "mark the order
  `paid` in the database", but section 9.7's `status` enum is
  `new | cooking | served | void`, which has no paid state in it. Paid
  and cooking are genuinely independent (a kitchen starts before money
  lands, and a contest demo takes money for something nobody cooks), so
  this is its own nullable timestamp rather than a fifth status value
  that would have to be ordered against the other four.
- `qr_url TEXT` — what the projected QR actually encodes, stored so a
  scanned code can be reconciled with what the table showed even if
  `core.host_for_browser` is reconfigured later.

SQLite because it is in the standard library, is a single file, survives
a power cut, and gives the staff view real reporting for almost no code.

Every write is its own connection. `sqlite3` objects are bound to the
thread that made them by default, and this store is written from core's
state thread (checkout) and read from the web server's thread (the
receipt page) — a shared connection would need `check_same_thread=False`
plus a lock of its own, which is more machinery than reopening a local
file costs.
"""

from __future__ import annotations

import contextlib
import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
ORDERS_PATH = _ROOT / "state" / "orders.sqlite3"

log = logging.getLogger("hotpot.orders")

# Doc section 18.1's "a short code assigned (`A17`)". One letter and two
# digits: short enough to read off a projected table from a metre away and
# to say out loud (doc section 15's `order_code` sound event), and 26*90
# wide, which is far more than a service ever needs.
_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # no I, no O — they read as 1 and 0
_CODE_MIN = 10
_CODE_MAX = 99

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  status TEXT NOT NULL,
  broth TEXT, spice INTEGER,
  locale TEXT, currency TEXT,
  total REAL NOT NULL,
  paid_at REAL,
  qr_url TEXT
);
CREATE TABLE IF NOT EXISTS order_lines(
  order_id INTEGER REFERENCES orders(id),
  bin INTEGER, item_id TEXT, grams REAL,
  price_per_100g REAL, line_total REAL
);
CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(code);
"""


@dataclass
class OrderLine:
    bin: int
    item_id: str
    name: str
    grams: float
    price_per_100g: float
    line_total: float


@dataclass
class Order:
    id: int
    code: str
    created_at: float
    status: str
    broth: str
    spice: int
    locale: str
    currency: str
    total: float
    paid_at: Optional[float] = None
    qr_url: str = ""
    lines: List[OrderLine] = field(default_factory=list)

    @property
    def paid(self) -> bool:
        return self.paid_at is not None


class OrderStore:
    """Doc section 9.7's store. `name` is resolved at WRITE time.

    `order_lines` in the doc holds `item_id` only, and that is right for
    reconciliation — an id is stable where a display name is not. But a
    receipt a diner reads has to say "Fish Balls", and the catalogue is
    live data that a staff member can re-point at any time (the Bins tab's
    manual override). Reading an old order back through today's catalogue
    would silently relabel last week's receipts. So the display name is
    denormalised into the row at checkout: what the diner was shown is
    what the receipt shows, forever.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else ORDERS_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            # `name` is an addition to doc section 9.7's own schema (see
            # the class docstring). Added by migration rather than by
            # editing _SCHEMA's CREATE, so a database written before this
            # existed still opens.
            cols = {r[1] for r in db.execute("PRAGMA table_info(order_lines)")}
            if "name" not in cols:
                db.execute("ALTER TABLE order_lines ADD COLUMN name TEXT")

    @contextlib.contextmanager
    def _connect(self):
        """A connection that is COMMITTED and then CLOSED.

        `with sqlite3.connect(...)` does not close anything — the
        connection's own context manager commits or rolls back the
        transaction and leaves the handle open. On Windows that keeps a
        lock on the file, which is invisible in normal use and shows up
        as a test run that cannot delete its own temporary directory.
        Wrapping it is the fix; `contextlib.closing` alone would be the
        other half and would drop the commit.
        """
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # -- writing ----------------------------------------------------------

    def create(self, *, lines: List[OrderLine], total: float, broth: str,
               spice: int, locale: str, currency: str,
               qr_url: str = "") -> Order:
        """Write one order and return it, code assigned.

        The code is allocated inside the same transaction that inserts
        the row, and retried on collision. `code` is UNIQUE, so two
        orders finishing in the same second cannot both take `A17` — the
        loser hits an IntegrityError and draws again rather than
        overwriting a receipt somebody is about to scan.
        """
        now = time.time()
        with self._connect() as db:
            for _ in range(200):
                code = "%s%d" % (random.choice(_CODE_LETTERS),
                                 random.randint(_CODE_MIN, _CODE_MAX))
                try:
                    cur = db.execute(
                        "INSERT INTO orders(code, created_at, status, broth, "
                        "spice, locale, currency, total, paid_at, qr_url) "
                        "VALUES(?,?,?,?,?,?,?,?,NULL,?)",
                        (code, now, "new", broth, int(spice), locale,
                         currency, float(total), qr_url))
                except sqlite3.IntegrityError:
                    continue          # code already taken; draw another
                order_id = int(cur.lastrowid)
                break
            else:
                raise RuntimeError(
                    "orders: could not find a free order code in 200 tries — "
                    "the code space is full, which means the table has been "
                    "running far longer than a service")
            db.executemany(
                "INSERT INTO order_lines(order_id, bin, item_id, name, grams, "
                "price_per_100g, line_total) VALUES(?,?,?,?,?,?,?)",
                [(order_id, l.bin, l.item_id, l.name, l.grams,
                  l.price_per_100g, l.line_total) for l in lines])
        log.info("orders: wrote %s (%d lines, total %.2f, broth %s, spice %d)",
                 code, len(lines), total, broth, spice)
        return Order(id=order_id, code=code, created_at=now, status="new",
                     broth=broth, spice=int(spice), locale=locale,
                     currency=currency, total=float(total), qr_url=qr_url,
                     lines=list(lines))

    def mark_paid(self, code: str) -> Optional[Order]:
        """Doc section 18.2's "marks the order `paid` in the database".

        Returns the order, or None if there is no such code.

        Idempotent: a second tap on Pay does not move `paid_at`. The
        receipt page is a web page on a stranger's phone; it will be
        reloaded, double-tapped and opened twice, and the first payment
        is the one that happened.
        """
        with self._connect() as db:
            row = db.execute("SELECT id, paid_at FROM orders WHERE code = ?",
                             (code,)).fetchone()
            if row is None:
                return None
            if row["paid_at"] is None:
                db.execute("UPDATE orders SET paid_at = ? WHERE id = ?",
                           (time.time(), row["id"]))
        log.info("orders: %s marked paid", code)
        return self.get(code)

    def set_qr_url(self, code: str, url: str) -> bool:
        """Stamp on what the projected QR actually encodes.

        Separate from `create` because the URL contains the code, and the
        code is only known once the INSERT has succeeded — allocating it
        beforehand would mean allocating outside the transaction whose
        UNIQUE constraint is the thing that makes it unique at all.
        """
        with self._connect() as db:
            cur = db.execute("UPDATE orders SET qr_url = ? WHERE code = ?",
                             (url, code))
            return cur.rowcount > 0

    def set_status(self, code: str, status: str) -> bool:
        with self._connect() as db:
            cur = db.execute("UPDATE orders SET status = ? WHERE code = ?",
                             (status, code))
            return cur.rowcount > 0

    # -- reading ----------------------------------------------------------

    def get(self, code: str) -> Optional[Order]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM orders WHERE code = ?",
                             (code,)).fetchone()
            if row is None:
                return None
            lines = db.execute(
                "SELECT * FROM order_lines WHERE order_id = ? ORDER BY bin",
                (row["id"],)).fetchall()
        return self._row_to_order(row, lines)

    def recent(self, limit: int = 25) -> List[Order]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
                (int(limit),)).fetchall()
            out = []
            for row in rows:
                lines = db.execute(
                    "SELECT * FROM order_lines WHERE order_id = ? ORDER BY bin",
                    (row["id"],)).fetchall()
                out.append(self._row_to_order(row, lines))
        return out

    @staticmethod
    def _row_to_order(row: sqlite3.Row, lines: List[sqlite3.Row]) -> Order:
        return Order(
            id=int(row["id"]), code=row["code"],
            created_at=float(row["created_at"]), status=row["status"],
            broth=row["broth"] or "", spice=int(row["spice"] or 0),
            locale=row["locale"] or "", currency=row["currency"] or "",
            total=float(row["total"]), paid_at=row["paid_at"],
            qr_url=(row["qr_url"] if "qr_url" in row.keys() else "") or "",
            lines=[OrderLine(
                bin=int(l["bin"]), item_id=l["item_id"] or "",
                name=(l["name"] if "name" in l.keys() else "") or l["item_id"] or "",
                grams=float(l["grams"] or 0.0),
                price_per_100g=float(l["price_per_100g"] or 0.0),
                line_total=float(l["line_total"] or 0.0)) for l in lines])

    def as_dicts(self, limit: int = 25) -> List[Dict[str, Any]]:
        out = []
        for o in self.recent(limit):
            out.append({
                "code": o.code, "created_at": o.created_at,
                "status": o.status, "broth": o.broth, "spice": o.spice,
                "total": o.total, "paid": o.paid,
                "lines": [{"name": l.name, "grams": l.grams,
                           "line_total": l.line_total} for l in o.lines],
            })
        return out


def qr_matrix(url: str) -> List[List[bool]]:
    """The projected QR (doc section 18.1) as a square bool matrix.

    Core rasterises nothing. oF draws the modules as filled rects,
    the same way it draws everything else — sending a matrix rather than
    an image keeps I2 (core owns the data, oF owns the pixels) and means
    the QR scales to whatever the projector's module size needs to be
    without a resample.

    Returns `[]` if `qrcode` is not installed, which the caller must
    treat as "draw the code as text instead" rather than as a failure:
    the order is already written and the short code alone is enough for a
    diner to pay at the counter.
    """
    try:
        import qrcode
    except ImportError:
        log.warning("orders: qrcode not installed — no QR will be projected. "
                    "pip install -r python/requirements.txt")
        return []
    qr = qrcode.QRCode(border=0, box_size=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    return [[bool(v) for v in row] for row in qr.get_matrix()]
