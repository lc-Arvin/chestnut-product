"""MySQL implementation of the invitation store; never imports local SQLite data.

The small SQL adapter preserves the shared business rules. Each write unit uses
a database-scoped advisory lock and an InnoDB transaction, including quota checks.
Network I/O is called through asyncio.to_thread by the HTTP layer.
"""
import hashlib
import os
import secrets
import threading
import time

from admin_store import AdminStore


SETUP_HINTS = {
    "missing_user": "Set CHESTNUT_MYSQL_USER (or CHESTNUT_CLOUD_DB_ADMIN) in the deployed version's runtime environment",
    "missing_password": "Set CHESTNUT_MYSQL_PASSWORD (or CHESTNUT_CLOUD_DB_PASSWORD) in the deployed version's runtime environment",
    "missing_host": "Set CHESTNUT_MYSQL_HOST in the deployed version's runtime environment",
    "missing_database": "Set CHESTNUT_MYSQL_DATABASE to the existing business database name",
    "invalid_port": "CHESTNUT_MYSQL_PORT must be an integer from 1 to 65535",
    "invalid_timeout": "CHESTNUT_MYSQL_TIMEOUT_SECONDS must be an integer from 1 to 60",
    "write_lock_timeout": "Database write lock timed out; check other starting versions or active transactions",
    "unsupported_schema": "Unsupported MySQL schema version; check the selected database and application version",
}


class MySQLSetupError(RuntimeError):
    """Only allow predefined diagnostics, never connection values or driver text."""
    def __init__(self, reason):
        super().__init__(SETUP_HINTS[reason])
        self.reason = reason


def mysql_failure_details(error):
    if isinstance(error, MySQLSetupError):
        return error.reason, SETUP_HINTS[error.reason]
    code = error.args[0] if error.args and type(error.args[0]) is int else None
    hints = {
        1044: "Database access denied; check account permissions for the selected database",
        1045: "Authentication failed; check runtime username, password and permitted connection sources",
        1049: "Database does not exist; create CHESTNUT_MYSQL_DATABASE before starting the service",
        1142: "SQL permission denied; check the application's table creation and data access permissions",
        2003: "Cannot connect to MySQL; check host, port, VPC routing and firewall rules",
        2006: "MySQL connection closed; check database availability and network connectivity",
        2013: "MySQL connection lost; check database availability and network connectivity",
    }
    return (f"mysql_{code}" if code is not None else "internal_error",
            hints.get(code, "Check database configuration, driver dependencies and schema initialization"))


def mysql_options():
    def value(name, default=""):
        return os.environ.get("CHESTNUT_MYSQL_" + name, default)
    user = value("USER") or os.environ.get("CHESTNUT_CLOUD_DB_ADMIN", "")
    password = value("PASSWORD") or os.environ.get("CHESTNUT_CLOUD_DB_PASSWORD", "")
    host, database = value("HOST"), value("DATABASE", "chestnut")
    for name, setting in (("user", user), ("password", password), ("host", host), ("database", database)):
        if not setting.strip():
            raise MySQLSetupError("missing_" + name)

    def number(name, default, maximum, reason):
        try:
            result = int(value(name, default))
        except ValueError:
            raise MySQLSetupError(reason) from None
        if not 1 <= result <= maximum:
            raise MySQLSetupError(reason)
        return result

    port = number("PORT", "3306", 65535, "invalid_port")
    timeout = number("TIMEOUT_SECONDS", "10", 60, "invalid_timeout")
    options = dict(host=host, port=port, user=user, password=password, database=database,
                   charset="utf8mb4", autocommit=True, connect_timeout=timeout,
                   read_timeout=timeout, write_timeout=timeout)
    ca = value("SSL_CA")
    if ca:
        options.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
    return options


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        self.rowcount = cursor.rowcount
        names = [item[0] for item in cursor.description] if cursor.description else []
        self.rows = [Row(zip(names, row)) for row in cursor.fetchall()] if names else []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class MySQLDatabase:
    def __init__(self, options):
        import pymysql
        self.connection = pymysql.connect(**options)
        self.in_transaction = False
        self.lock_name = "chestnut-write-" + hashlib.sha256(options["database"].encode()).hexdigest()[:40]

    def execute(self, sql, params=()):
        if sql == "BEGIN IMMEDIATE":
            # __enter__ already owns the cross-connection lock and transaction.
            if not self.in_transaction:
                raise RuntimeError("Write transaction required")
            return None
        if not self.in_transaction:
            self.connection.ping(reconnect=True)
        with self.connection.cursor() as cursor:
            cursor.execute(sql.replace("?", "%s"), tuple(params))
            return Result(cursor)

    def __enter__(self):
        if self.in_transaction:
            raise RuntimeError("Nested database transactions are not supported")
        acquired = self.execute("SELECT GET_LOCK(?,10)", (self.lock_name,)).fetchone()[0]
        if acquired != 1:
            raise MySQLSetupError("write_lock_timeout")
        self.in_transaction = True
        try:
            self.connection.begin()
        except BaseException:
            self.connection.close()
            self.in_transaction = False
            raise
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if kind is None:
                self.connection.commit()
            else:
                self.connection.rollback()
            self.execute("SELECT RELEASE_LOCK(?)", (self.lock_name,))
        except BaseException:
            self.connection.close()
            raise
        finally:
            self.in_transaction = False

    def close(self):
        self.connection.close()


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS settings (`key` VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS codes (
        id VARCHAR(24) PRIMARY KEY, code VARCHAR(200) UNIQUE NOT NULL, label VARCHAR(60) NOT NULL,
        note VARCHAR(500) NOT NULL DEFAULT '', source VARCHAR(32) NOT NULL DEFAULT 'generated',
        enabled INT NOT NULL DEFAULT 1, version INT NOT NULL DEFAULT 1,
        created_at DOUBLE NOT NULL, expires_at DOUBLE, max_uses INT NOT NULL DEFAULT 0,
        use_count INT NOT NULL DEFAULT 0, last_used_at DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS events (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, created_at DOUBLE NOT NULL, kind VARCHAR(40) NOT NULL,
        code_id VARCHAR(24) NOT NULL DEFAULT '', client VARCHAR(16) NOT NULL DEFAULT '',
        network VARCHAR(16) NOT NULL DEFAULT '', channel VARCHAR(20) NOT NULL DEFAULT '',
        reason VARCHAR(200) NOT NULL DEFAULT '', meeting VARCHAR(16) NOT NULL DEFAULT '',
        occurrences INT NOT NULL DEFAULT 1, last_at DOUBLE,
        INDEX events_time(created_at), INDEX events_code_time(code_id,created_at),
        INDEX events_network_time(network,created_at),
        INDEX events_compaction(kind,code_id,client,meeting,channel,created_at))""",
    """CREATE TABLE IF NOT EXISTS alerts (
        id BIGINT PRIMARY KEY AUTO_INCREMENT, fingerprint VARCHAR(200) UNIQUE NOT NULL,
        kind VARCHAR(40) NOT NULL, code_id VARCHAR(24) NOT NULL, subject VARCHAR(64) NOT NULL,
        first_at DOUBLE NOT NULL, last_at DOUBLE NOT NULL, occurrences INT NOT NULL,
        detail TEXT NOT NULL, acknowledged_at DOUBLE, INDEX alerts_time(last_at))""",
    """CREATE TABLE IF NOT EXISTS admin_sessions (
        digest VARCHAR(64) PRIMARY KEY, csrf VARCHAR(64) NOT NULL, expires_at DOUBLE NOT NULL,
        INDEX sessions_expiry(expires_at))""",
    """CREATE TABLE IF NOT EXISTS daily_visits (
        day VARCHAR(10) NOT NULL, client VARCHAR(16) NOT NULL, channel VARCHAR(20) NOT NULL,
        visits INT NOT NULL, last_at DOUBLE NOT NULL, PRIMARY KEY(day,client,channel))""",
    """CREATE TABLE IF NOT EXISTS transcripts (
        owner VARCHAR(128) NOT NULL, filename VARCHAR(240) NOT NULL, content MEDIUMTEXT NOT NULL,
        created_at DOUBLE NOT NULL, PRIMARY KEY(owner,filename))""",
)


class MySQLAdminStore(AdminStore):
    dialect = "mysql"

    def __init__(self, options=None):
        self.lock = threading.RLock()
        self.db = MySQLDatabase(options or mysql_options())
        try:
            for statement in SCHEMA:
                self.db.execute(statement + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
            with self.lock, self.db:
                version = self.setting("schema_version")
                if version not in {"", "1"}:
                    raise MySQLSetupError("unsupported_schema")
                self.db.execute("INSERT IGNORE INTO settings VALUES ('schema_version','1')")
                self.db.execute("INSERT IGNORE INTO settings VALUES ('signing_secret',?)", (secrets.token_urlsafe(48),))
            self.signing_secret = self.setting("signing_secret")
            self.maintenance()
        except BaseException:
            self.db.close()
            raise

    def health(self):
        with self.lock:
            return self.db.execute("SELECT 1").fetchone()[0] == 1

    def save_transcript(self, filename, content, owner):
        # Keep the human readable name while avoiding overwrites on retries.
        filename = filename.removesuffix(".md")[:210] + "-" + secrets.token_hex(6) + ".md"
        with self.lock, self.db:
            self.db.execute("INSERT INTO transcripts VALUES (?,?,?,?)", (owner, filename, content, time.time()))
        return {"filename": filename, "storage": "mysql", "url": f"/meetings/{owner}/{filename}"}

    def read_transcript(self, owner, filename):
        with self.lock:
            row = self.db.execute("SELECT content FROM transcripts WHERE owner=? AND filename=?", (owner, filename)).fetchone()
            return row[0] if row else None
