"""Unit tests for worker RPC hardening: correlation, generation tagging,
per-method timeouts, and the embedding-model manifest. Synthetic only."""

import json
import os
import select as select_module
import sys
import threading
import time
import uuid as uuid_module
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from daemon.knowledge_db_dual import ChromaWorkerClient, WorkerBackendError, WorkerRequestError
from daemon.chroma_worker import open_verified_collection


class _FakeStdin:
    def write(self, data): pass
    def flush(self): pass


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [json.dumps(line) + "\n" for line in lines]
    def readline(self):
        return self._lines.pop(0) if self._lines else ""


def _client(lines, gen=1):
    c = ChromaWorkerClient.__new__(ChromaWorkerClient)
    c._process = type("P", (), {"stdin": _FakeStdin(), "stdout": _FakeStdout(lines)})()
    c._generation = gen
    return c


# Captured before the autouse fixture below ever patches it, so tests that
# genuinely need OS-level select() semantics (a real os.pipe() blocking on
# readline()) can restore it -- see _real_select_for_this_test().
_real_select = select_module.select


@pytest.fixture(autouse=True)
def _fake_select(monkeypatch):
    # select() needs real fds; our fakes aren't, so report "ready" immediately.
    monkeypatch.setattr(select_module, "select", lambda r, w, x, timeout: (r, [], []))


def test_rpc_discards_stale_response_and_returns_matching_one(monkeypatch):
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _client([
        {"id": "stale-id", "result": "wrong", "gen": 1},
        {"id": "req-1", "result": "correct", "gen": 1},
    ])
    assert c._rpc("query", {}) == "correct"


def test_rpc_discards_stale_generation(monkeypatch):
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _client([
        {"id": "req-1", "result": "old-gen", "gen": 1},
        {"id": "req-1", "result": "current-gen", "gen": 2},
    ], gen=2)
    assert c._rpc("query", {}) == "current-gen"


def test_timeout_for_selects_per_method_defaults():
    c = ChromaWorkerClient.__new__(ChromaWorkerClient)
    assert c._timeout_for("query") == ChromaWorkerClient._RPC_TIMEOUT_QUERY == 20
    assert c._timeout_for("index") == ChromaWorkerClient._RPC_TIMEOUT_INDEX == 120
    assert c._timeout_for("ping") == ChromaWorkerClient._RPC_TIMEOUT_PING == 10
    assert c._timeout_for("unknown") == ChromaWorkerClient._RPC_TIMEOUT_QUERY


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("RPC_TIMEOUT_QUERY", "7")
    monkeypatch.setenv("RPC_TIMEOUT_INDEX", "42")
    monkeypatch.setenv("RPC_TIMEOUT_PING", "3")
    import importlib
    import daemon.knowledge_db_dual as kdd
    importlib.reload(kdd)
    try:
        assert kdd.ChromaWorkerClient._RPC_TIMEOUT_QUERY == 7
        assert kdd.ChromaWorkerClient._RPC_TIMEOUT_INDEX == 42
        assert kdd.ChromaWorkerClient._RPC_TIMEOUT_PING == 3
    finally:
        for k in ("RPC_TIMEOUT_QUERY", "RPC_TIMEOUT_INDEX", "RPC_TIMEOUT_PING"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(kdd)


class _FakeCollection:
    def __init__(self, metadata=None):
        self.metadata = metadata


class _FakeClient:
    def __init__(self, existing=None):
        self._existing = existing
        self.created_with = None

    def get_collection(self, name, embedding_function=None):
        if self._existing is None:
            raise Exception("does not exist")
        return self._existing

    def create_collection(self, name, embedding_function=None, metadata=None):
        self.created_with = metadata
        self._existing = _FakeCollection(metadata=metadata)
        return self._existing


def test_manifest_mismatch_refuses_collection():
    fc = _FakeClient(existing=_FakeCollection({"ag_embedding_model": "model-a"}))
    assert open_verified_collection(fc, "c", ef=None, embedding_model="model-b") is None


def test_manifest_match_returns_collection():
    existing = _FakeCollection({"ag_embedding_model": "model-a"})
    fc = _FakeClient(existing=existing)
    assert open_verified_collection(fc, "c", ef=None, embedding_model="model-a") is existing


def test_manifest_missing_proceeds_unverified():
    existing = _FakeCollection({})
    fc = _FakeClient(existing=existing)
    assert open_verified_collection(fc, "c", ef=None, embedding_model="model-a") is existing


def test_manifest_written_on_creation():
    fc = _FakeClient(existing=None)
    col = open_verified_collection(fc, "c", ef=None, embedding_model="model-a")
    assert col is not None
    assert fc.created_with["ag_embedding_model"] == "model-a"
    assert fc.created_with["ag_manifest_version"] == "1"
    assert "ag_embedding_device" not in fc.created_with


# ---------------------------------------------------------------------------
# Circuit-breaker state machine (docs/plans/worker-breaker-redesign.md)
# ---------------------------------------------------------------------------

def _fake_process(lines):
    return type("P", (), {"stdin": _FakeStdin(), "stdout": _FakeStdout(lines)})()


def _breaker_client(state=None, epoch=0, generation=1, failures=0):
    """A ChromaWorkerClient with breaker fields set directly, bypassing
    __init__ (and therefore never starting a real supervisor thread or
    subprocess) -- the same __new__ pattern as _client() above, extended
    with the new locked-state-machine fields."""
    c = ChromaWorkerClient.__new__(ChromaWorkerClient)
    c.persist_directory = "/tmp/fake-chroma"
    c.embedding_model_name = "fake-model"
    c._lock = threading.Lock()
    c._state_lock = threading.Lock()
    c._process = None
    c._generation = generation
    c._epoch = epoch
    c._consecutive_failures = failures
    c._closed_since = None
    c._last_error = None
    c._last_index_error = None
    c._served_collections = []
    c._wake = threading.Event()
    c._state = state if state is not None else ChromaWorkerClient._RECOVERING
    c._state_since = time.monotonic()
    c._supervisor = None
    return c


def test_supervisor_cas_discards_stale_probe_success_lost_wakeup(monkeypatch):
    """A probe that succeeds must not publish CLOSED if a failure bumped
    _epoch mid-probe -- this is defect 1 (the lost wakeup) from a94ad08."""
    c = _breaker_client(state=ChromaWorkerClient._OPEN, epoch=5, failures=0)
    calls = {"n": 0}

    def fake_probe():
        calls["n"] += 1
        if calls["n"] == 1:
            # simulate a concurrent failure landing while this probe is in flight
            with c._state_lock:
                c._epoch += 1
            return True
        return True

    monkeypatch.setattr(c, "_probe", fake_probe)
    c._supervisor = threading.Thread(target=c._supervise, daemon=True)
    c._supervisor.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and c._state != ChromaWorkerClient._CLOSED:
        time.sleep(0.005)

    assert c._state == ChromaWorkerClient._CLOSED
    # If the CAS were missing, the first (mismatched) success would have been
    # accepted immediately and there would only be one probe call.
    assert calls["n"] == 2
    assert c._epoch == 6
    assert c._consecutive_failures == 0
    c.shutdown()


def test_request_error_never_opens_breaker(monkeypatch):
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = _fake_process(
        [{"id": "req-1", "gen": 1, "error": "bad query_text", "error_kind": "request"}]
    )
    result = c._call_query("query", {})
    assert result is None
    assert c._state == ChromaWorkerClient._CLOSED
    assert c._consecutive_failures == 0
    assert c._epoch == 0
    assert c._last_error is None


def test_backend_error_response_opens_breaker(monkeypatch):
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = _fake_process(
        [{"id": "req-1", "gen": 1, "error": "boom", "error_kind": "backend"}]
    )
    result = c._call_query("query", {})
    assert result is None
    assert c._state == ChromaWorkerClient._OPEN
    assert c._epoch == 1


def test_timeout_opens_breaker(monkeypatch):
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = _fake_process([])
    monkeypatch.setattr(c, "_timeout_for", lambda method: 0)
    result = c._call_query("query", {})
    assert result is None
    assert c._state == ChromaWorkerClient._OPEN
    assert c._epoch == 1


def test_backoff_schedule_walks_and_holds():
    c = ChromaWorkerClient.__new__(ChromaWorkerClient)
    assert c._backoff_base_delay(0) == 0        # failure #0 -> immediate
    assert c._backoff_base_delay(1) == 120
    assert c._backoff_base_delay(2) == 300
    assert c._backoff_base_delay(3) == 900
    assert c._backoff_base_delay(4) == 1800
    assert c._backoff_base_delay(5) == 1800     # stays at the ceiling
    assert c._backoff_base_delay(100) == 1800


def test_probe_missing_required_collection_fails(monkeypatch):
    c = _breaker_client(state=ChromaWorkerClient._RECOVERING)
    monkeypatch.setattr(c, "_terminate", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)

    def fake_rpc(method, params):
        if method == "ping":
            return "pong"
        return {"results": [], "total_matches": 0, "served_collections": ["beta"]}

    monkeypatch.setattr(c, "_rpc", fake_rpc)
    assert c._probe() is False


def test_probe_zero_hits_but_required_collection_served_passes(monkeypatch):
    c = _breaker_client(state=ChromaWorkerClient._RECOVERING)
    monkeypatch.setattr(c, "_terminate", lambda: None)
    monkeypatch.setattr(c, "_start_worker", lambda: None)

    def fake_rpc(method, params):
        if method == "ping":
            return "pong"
        return {"results": [], "total_matches": 0, "served_collections": ["alpha", "beta"]}

    monkeypatch.setattr(c, "_rpc", fake_rpc)
    assert c._probe() is True


def test_missing_error_kind_treated_as_backend(monkeypatch):
    """Fail-safe: an error response with no error_kind field must open the
    breaker, matching pre-redesign behaviour."""
    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = _fake_process([{"id": "req-1", "gen": 1, "error": "mystery failure"}])
    result = c._call_query("query", {})
    assert result is None
    assert c._state == ChromaWorkerClient._OPEN
    assert c._epoch == 1


def test_call_never_starts_worker_on_query_path(monkeypatch):
    def boom():
        raise AssertionError("_start_worker must not be reachable from call()")

    monkeypatch.setattr(uuid_module, "uuid4", lambda: "req-1")
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    monkeypatch.setattr(c, "_start_worker", boom)
    c._process = _fake_process([{
        "id": "req-1", "gen": 1,
        "result": {"results": [], "total_matches": 0, "served_collections": ["alpha"]},
    }])
    result = c.call("query", {"query_text": "x"})
    assert result is not None


def test_call_never_starts_worker_on_index_path(monkeypatch):
    def boom():
        raise AssertionError("_start_worker must not be reachable from call()")

    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    monkeypatch.setattr(c, "_start_worker", boom)
    c._process = None  # worker not alive
    assert c.call("index", {"conversation": {}}) is None
    assert c._last_index_error is not None


def test_admission_fails_fast_when_lock_held(monkeypatch):
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    monkeypatch.setattr(ChromaWorkerClient, "_QUERY_LOCK_TIMEOUT", 0.05)
    c._lock.acquire()  # simulate another thread mid-RPC
    try:
        start = time.monotonic()
        result = c._call_query("query", {})
        elapsed = time.monotonic() - start
    finally:
        c._lock.release()
    assert result is None
    assert elapsed < 0.5


def test_shutdown_reaches_stopped_and_joins_supervisor():
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._supervisor = threading.Thread(target=c._supervise, daemon=True)
    c._supervisor.start()
    time.sleep(0.05)  # let it park in the indefinite CLOSED wait
    c.shutdown()
    assert c._state == ChromaWorkerClient._STOPPED
    c._supervisor.join(timeout=2)
    assert not c._supervisor.is_alive()


# ---------------------------------------------------------------------------
# F1/F2/F3: dead worker discovered via indexing must not strand the breaker
# ---------------------------------------------------------------------------

def test_index_call_on_dead_worker_opens_breaker():
    """F1: a dead worker found via the index path is a backend-health fact,
    not an index-batch outcome -- it must open the breaker, or a worker that
    dies during indexing strands serving CLOSED with nothing watching it."""
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = None  # dead
    result = c.call("index", {"conversation": {}})
    assert result is None
    assert c._state == ChromaWorkerClient._OPEN
    assert c._epoch == 1
    assert c._wake.is_set()


def test_index_timeout_on_live_worker_does_not_open_breaker(monkeypatch):
    """A slow-but-live index batch must stay non-breaking -- this is the
    2026-08-17 incident's original fix and must not regress."""
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    fake_proc = _fake_process([])
    fake_proc.poll = lambda: None  # still alive throughout
    c._process = fake_proc
    monkeypatch.setattr(c, "_timeout_for", lambda method: 0)
    result = c._call_index({"conversation": {}})
    assert result is None
    assert c._state == ChromaWorkerClient._CLOSED
    assert c._epoch == 0
    assert c._last_index_error is not None


def test_closed_park_is_bounded_not_indefinite(monkeypatch):
    """F2: the CLOSED park must never be an untimed wait() -- a dead worker
    process must self-heal even if nothing ever explicitly classified the
    failure that killed it."""
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    captured = {}

    def spy_wait(timeout=None):
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(c._wake, "wait", spy_wait)
    c._supervise_once()
    assert captured.get("timeout") is not None
    assert captured["timeout"] == ChromaWorkerClient._LIVENESS_POLL_S


def test_dead_worker_discovered_only_by_liveness_poll_recovers(monkeypatch):
    """F2 end-to-end: a worker that dies with nothing ever calling
    _record_backend_failure must still be found and recovered by the bounded
    CLOSED park, not left CLOSED forever."""
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    c._process = None  # dead, but nobody has told the breaker
    monkeypatch.setattr(ChromaWorkerClient, "_LIVENESS_POLL_S", 0.01)
    c._supervise_once()
    assert c._state == ChromaWorkerClient._OPEN
    assert c._epoch == 1
    assert c._wake.is_set()


def test_supervisor_survives_unexpected_exception_and_keeps_running(monkeypatch):
    """F3: an unhandled exception anywhere in the loop body must not end the
    only supervisor -- it must be logged and the loop must continue."""
    import daemon.knowledge_db_dual as kdd

    monkeypatch.setattr(kdd.time, "sleep", lambda s: None)  # skip the real 1s backoff
    monkeypatch.setattr(ChromaWorkerClient, "_LIVENESS_POLL_S", 0.01)
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)

    calls = {"n": 0}

    def flaky_is_alive():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated unexpected failure")
        return True

    monkeypatch.setattr(c, "_is_alive", flaky_is_alive)
    c._supervisor = threading.Thread(target=c._supervise, daemon=True)
    c._supervisor.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and calls["n"] < 2:
        time.sleep(0.005)

    assert calls["n"] >= 2
    assert c._supervisor.is_alive()  # the exception did not kill the loop
    c.shutdown()


def test_shutdown_is_not_mistaken_for_a_fault(monkeypatch):
    """STOPPED must propagate as a clean return, never get swallowed by the
    F3 blanket except and logged as an 'unexpected error'."""
    import daemon.knowledge_db_dual as kdd

    logged = {"n": 0}
    monkeypatch.setattr(kdd.logger, "exception", lambda *a, **kw: logged.__setitem__("n", logged["n"] + 1))
    c = _breaker_client(state=ChromaWorkerClient._STOPPED, epoch=0)
    c._supervise()  # returns immediately -- state is already STOPPED
    assert logged["n"] == 0


# ---------------------------------------------------------------------------
# G1/G2: _start_worker's readline() must not be able to block forever
# ---------------------------------------------------------------------------

class _FakeStartupProc:
    """A subprocess.Popen stand-in that is alive (poll() -> None) and never
    writes a line -- a real os.pipe() so `select` genuinely blocks, unlike a
    fake object returning "" (which only exercises the EOF path)."""

    pid = 999999

    def __init__(self, stdout):
        self.stdout = stdout
        self.stdin = _FakeStdin()
        self.terminated = False

    def poll(self):
        return None  # alive throughout -- a SIGSTOPped/wedged worker never exits

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        pass

    def kill(self):
        self.terminated = True


def test_start_worker_bounded_when_worker_never_signals_ready(monkeypatch):
    """G1: readline() must not be able to block past _READY_TIMEOUT even when
    the process is alive and never writes a line (SIGSTOP, or wedged mmapping
    a multi-GB index). A hard per-test timeout isn't available here, but the
    elapsed-time assertion fails loudly rather than hanging silently if this
    regresses -- and pytest's own default has no timeout, so a genuine
    regression would hang this test file, which is itself the signal."""
    import daemon.knowledge_db_dual as kdd

    # The file-wide autouse fixture fakes select() as always-ready (needed by
    # every other test's non-fd fakes); this test needs the real thing, since
    # its whole point is a genuinely-blocking OS pipe.
    monkeypatch.setattr(select_module, "select", _real_select)
    monkeypatch.setattr(ChromaWorkerClient, "_READY_TIMEOUT", 0.2)
    read_fd, write_fd = os.pipe()  # nothing is ever written to write_fd
    stdout = os.fdopen(read_fd, "r")
    fake_proc = _FakeStartupProc(stdout)
    monkeypatch.setattr(kdd.subprocess, "Popen", lambda *a, **kw: fake_proc)

    c = _breaker_client(state=ChromaWorkerClient._RECOVERING)
    try:
        start = time.monotonic()
        # kdd.WorkerBackendError, not the top-of-file import: an earlier test
        # in this file (test_timeout_env_override) reloads daemon.knowledge_db_dual
        # in place, which rebinds the module's WorkerBackendError to a new
        # class object -- the stale imported name would no longer match what
        # _start_worker actually raises.
        with pytest.raises(kdd.WorkerBackendError):
            c._start_worker()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0
        assert fake_proc.terminated  # timed-out worker must be cleaned up, not leaked
    finally:
        os.close(write_fd)
        stdout.close()


def test_probe_end_to_end_with_wedged_start_worker_bumps_failures(monkeypatch):
    """End to end via the real _probe()/_supervise_once() wiring: a worker
    that never signals ready must fail the probe, bump consecutive_failures,
    and land back in OPEN so backoff proceeds -- not wedge RECOVERING forever
    with the supervisor parked inside _start_worker (the fault-scenario-b bug)."""
    import daemon.knowledge_db_dual as kdd

    monkeypatch.setattr(select_module, "select", _real_select)
    monkeypatch.setattr(ChromaWorkerClient, "_READY_TIMEOUT", 0.1)
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "r")
    fake_proc = _FakeStartupProc(stdout)
    monkeypatch.setattr(kdd.subprocess, "Popen", lambda *a, **kw: fake_proc)

    c = _breaker_client(state=ChromaWorkerClient._OPEN, epoch=0, failures=0)
    try:
        start = time.monotonic()
        c._supervise_once()  # OPEN, failures=0 -> immediate probe -> wedged start_worker
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        assert c._state == ChromaWorkerClient._OPEN
        assert c._consecutive_failures == 1
    finally:
        os.close(write_fd)
        stdout.close()


def test_state_age_s_present_and_increases_across_transition():
    c = _breaker_client(state=ChromaWorkerClient._CLOSED, epoch=0)
    time.sleep(0.05)
    age_before = c.breaker_status()["state_age_s"]
    assert age_before >= 0.05

    with c._state_lock:
        c._set_state(ChromaWorkerClient._OPEN)
    age_after = c.breaker_status()["state_age_s"]
    assert age_after < age_before  # a transition resets the clock
