//! FlowBench native load-generation engine.
//!
//! A standalone process driven by the Python agent over stdin/stdout with a
//! newline-delimited JSON protocol (see ../ENGINE.md). The Python side keeps
//! every authorization, allow-list and limit check; this engine only ever
//! receives a job that has already passed `validate_job`, and refuses to run
//! without a concrete target.
//!
//! The engine stops when it reads a `{"type":"stop"}` command OR when stdin
//! reaches EOF. EOF means the controlling parent is gone, so the engine must
//! not keep generating load: the bridge keeps stdin open for the job's life.

use std::collections::{HashMap, VecDeque};
use std::error::Error as _;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{Mutex, Semaphore};
use tokio::time::Instant;

/// Bumped when the stdin/stdout contract changes in a breaking way.
const PROTOCOL: u32 = 1;

/// The User-Agent the engine sends (and counts in `bytes_tx`).
const USER_AGENT: &str = "FlowBench-Engine";

/// Once the test window ends (deadline or stop), in-flight requests get at
/// most this long to finish; the rest are cancelled. Without a cap a stop
/// waited out a slow target's whole timeout (up to 60 s), while the Python
/// engines give up after about 1.5-2.5 s.
const DRAIN_GRACE: Duration = Duration::from_millis(2000);

// ---------------------------------------------------------------------------
// Wire protocol
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
enum Command {
    Start {
        #[serde(default)]
        #[allow(dead_code)]
        job_id: String,
        config: JobConfig,
    },
    Stop,
}

fn default_timeout() -> u64 {
    5000
}

#[derive(Debug, Clone, Deserialize)]
struct JobConfig {
    target: String,
    protocol: String,
    #[serde(default)]
    url: String,
    #[serde(default)]
    port: u16,
    rate: f64,
    duration: f64,
    threads: u32,
    packet_size: u32,
    #[serde(default = "default_timeout")]
    timeout: u64,
    #[serde(default)]
    headers: Option<HashMap<String, String>>,
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
enum Event {
    Hello {
        engine: &'static str,
        version: &'static str,
        protocol: u32,
    },
    Snapshot {
        running: bool,
        total: u64,
        success: u64,
        fail: u64,
        tx: u64,
        qps: f64,
        avg: f64,
        active: u64,
        progress: f64,
        last_error: String,
    },
    Report {
        target: String,
        protocol: String,
        duration: f64,
        total: u64,
        success: u64,
        fail: u64,
        avg: f64,
        p50: f64,
        p90: f64,
        p99: f64,
        traffic_mb: f64,
        bytes_tx: u64,
        rate_limit: f64,
        errors: HashMap<String, u64>,
    },
    Error {
        message: String,
    },
}

type Out = Arc<Mutex<tokio::io::Stdout>>;

async fn emit(out: &Out, event: &Event) {
    if let Ok(mut line) = serde_json::to_string(event) {
        line.push('\n');
        let mut guard = out.lock().await;
        let _ = guard.write_all(line.as_bytes()).await;
        let _ = guard.flush().await;
    }
}

// ---------------------------------------------------------------------------
// Statistics
// ---------------------------------------------------------------------------

struct Stats {
    total: AtomicU64,
    success: AtomicU64,
    fail: AtomicU64,
    bytes_tx: AtomicU64,
    latencies: Mutex<Vec<f64>>,
    recent: Mutex<VecDeque<f64>>,
    buckets: Mutex<VecDeque<(Instant, u64)>>,
    errors: Mutex<HashMap<String, u64>>,
    last_error: Mutex<String>,
}

impl Stats {
    fn new() -> Self {
        Stats {
            total: AtomicU64::new(0),
            success: AtomicU64::new(0),
            fail: AtomicU64::new(0),
            bytes_tx: AtomicU64::new(0),
            latencies: Mutex::new(Vec::new()),
            recent: Mutex::new(VecDeque::with_capacity(400)),
            buckets: Mutex::new(VecDeque::new()),
            errors: Mutex::new(HashMap::new()),
            last_error: Mutex::new(String::new()),
        }
    }

    async fn record(&self, ok: bool, err: Option<String>, sent: u64, elapsed_ms: f64) {
        self.total.fetch_add(1, Ordering::Relaxed);
        self.bytes_tx.fetch_add(sent, Ordering::Relaxed);
        if ok {
            self.success.fetch_add(1, Ordering::Relaxed);
        } else {
            self.fail.fetch_add(1, Ordering::Relaxed);
        }
        if let Some(code) = err {
            {
                let mut errors = self.errors.lock().await;
                *errors.entry(code.clone()).or_insert(0) += 1;
            }
            *self.last_error.lock().await = code;
        }
        {
            let mut recent = self.recent.lock().await;
            recent.push_back(elapsed_ms);
            while recent.len() > 400 {
                recent.pop_front();
            }
        }
        {
            let mut lat = self.latencies.lock().await;
            if lat.len() < 100_000 {
                lat.push(elapsed_ms);
            }
        }
        {
            let now = Instant::now();
            let mut buckets = self.buckets.lock().await;
            match buckets.back_mut() {
                Some(b) if now.duration_since(b.0) < Duration::from_millis(100) => b.1 += 1,
                _ => buckets.push_back((now, 1)),
            }
            while let Some(front) = buckets.front() {
                if now.duration_since(front.0) > Duration::from_secs(1) {
                    buckets.pop_front();
                } else {
                    break;
                }
            }
        }
    }
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let n = sorted.len() as f64;
    let idx = ((p / 100.0) * (n - 1.0)).round();
    let idx = idx.max(0.0).min(n - 1.0) as usize;
    sorted[idx]
}

// ---------------------------------------------------------------------------
// Rate limiting (token bucket, shared across tasks)
// ---------------------------------------------------------------------------

struct RateLimiter {
    rate: f64,
    capacity: f64,
    state: Mutex<(f64, Instant)>,
}

impl RateLimiter {
    fn new(rate: f64) -> Self {
        let rate = rate.max(0.1);
        let capacity = rate.max(1.0);
        // Start with one token, not a full bucket: a full bucket let the first
        // second send the configured rate *plus* a whole burst, i.e. up to
        // twice the rate the job was authorized for.
        RateLimiter {
            rate,
            capacity,
            state: Mutex::new((capacity.min(1.0), Instant::now())),
        }
    }

    /// Returns true when a token was taken, false if stop/deadline came first.
    async fn acquire(&self, stop: &AtomicBool, deadline: Instant) -> bool {
        loop {
            if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
                return false;
            }
            let wait = {
                let mut guard = self.state.lock().await;
                let (mut tokens, updated) = *guard;
                let now = Instant::now();
                tokens = (tokens + now.duration_since(updated).as_secs_f64() * self.rate)
                    .min(self.capacity);
                if tokens >= 1.0 {
                    *guard = (tokens - 1.0, now);
                    return true;
                }
                *guard = (tokens, now);
                ((1.0 - tokens) / self.rate).min(0.1)
            };
            tokio::time::sleep(Duration::from_secs_f64(wait.max(0.0))).await;
        }
    }
}

// ---------------------------------------------------------------------------
// Request execution
// ---------------------------------------------------------------------------

fn classify_text(text: &str) -> String {
    let lower = text.to_lowercase();
    for (needle, code) in [
        ("timed out", "timeout"),
        ("timeout", "timeout"),
        ("refused", "refused"),
        ("reset", "reset"),
        ("unreachable", "unreachable"),
        ("dns", "dns"),
        ("resolve", "dns"),
        ("lookup", "dns"),
        ("certificate", "cert"),
        ("tls", "tls"),
        ("handshake", "tls"),
        ("alert", "tls"),
        ("ssl", "tls"),
        ("closed", "closed"),
    ] {
        if lower.contains(needle) {
            return code.to_string();
        }
    }
    "error".to_string()
}

fn error_chain(err: &reqwest::Error) -> String {
    // reqwest's own message embeds the URL, whose words ("/timeout",
    // "reset.example.com") must not decide the class, so leave it out.
    let mut parts = Vec::new();
    let mut source = err.source();
    while let Some(cause) = source {
        parts.push(cause.to_string());
        source = cause.source();
    }
    if parts.is_empty() {
        let mut text = err.to_string();
        if let Some(url) = err.url() {
            text = text.replace(url.as_str(), "");
        }
        parts.push(text);
    }
    parts.join("; ")
}

/// The class of an OS-level I/O error from its kind alone (locale-independent).
fn io_kind_code(err: &std::io::Error) -> Option<&'static str> {
    use std::io::ErrorKind;
    Some(match err.kind() {
        ErrorKind::TimedOut => "timeout",
        ErrorKind::ConnectionRefused => "refused",
        ErrorKind::ConnectionReset => "reset",
        // UnexpectedEof: the peer closed before the response was complete
        // (e.g. a body shorter than its Content-Length).
        ErrorKind::ConnectionAborted | ErrorKind::UnexpectedEof => "closed",
        ErrorKind::HostUnreachable | ErrorKind::NetworkUnreachable | ErrorKind::NetworkDown => {
            "unreachable"
        }
        ErrorKind::AddrNotAvailable | ErrorKind::AddrInUse => "addr",
        _ => return None,
    })
}

fn classify_io(err: &std::io::Error) -> String {
    match io_kind_code(err) {
        Some(code) => code.to_string(),
        None => classify_text(&err.to_string()),
    }
}

fn classify_reqwest(err: &reqwest::Error) -> String {
    if err.is_timeout() {
        return "timeout".to_string();
    }
    // Prefer the OS error kind (locale-independent) over English substrings.
    let mut source: Option<&(dyn std::error::Error + 'static)> =
        Some(err as &(dyn std::error::Error + 'static));
    while let Some(cause) = source {
        if let Some(io) = cause.downcast_ref::<std::io::Error>() {
            if let Some(code) = io_kind_code(io) {
                return code.to_string();
            }
            break;
        }
        source = cause.source();
    }
    // Otherwise match the whole chain: hyper/reqwest add English context
    // ("dns error", "tcp connect error", TLS alerts) even where the OS
    // message itself is localized, as on Chinese Windows.
    classify_text(&error_chain(err))
}

/// Bytes one GET puts on the wire (HTTP/1.1, no body): the request line, the
/// Host, user-agent and accept headers reqwest adds unless the job overrides
/// them, the job's own headers and the blank line. TLS overhead is not
/// counted, as in the Python engines. Computed once per job.
fn http_request_bytes(cfg: &JobConfig) -> u64 {
    let Ok(url) = reqwest::Url::parse(&cfg.url) else {
        return 0;
    };
    let custom = cfg.headers.as_ref();
    let has = |name: &str| custom.is_some_and(|h| h.keys().any(|k| k.eq_ignore_ascii_case(name)));
    let line = |name: &str, value_len: usize| name.len() + 2 + value_len + 2; // "name: value\r\n"
    let request_target = url.path().len() + url.query().map_or(0, |q| q.len() + 1);
    let mut total = "GET ".len() + request_target + " HTTP/1.1\r\n".len();
    if !has("host") {
        let port = url.port().map_or(0, |p| p.to_string().len() + 1);
        total += line("host", url.host_str().unwrap_or("").len() + port);
    }
    if !has("user-agent") {
        total += line("user-agent", USER_AGENT.len());
    }
    if !has("accept") {
        total += line("accept", "*/*".len());
    }
    // reqwest turns URL credentials into a Basic authorization header.
    if !url.username().is_empty() || url.password().is_some() {
        let decoded = |s: &str| s.len() - 2 * s.matches('%').count();
        let n = decoded(url.username()) + 1 + url.password().map_or(0, decoded);
        total += line("authorization", "Basic ".len() + n.div_ceil(3) * 4);
    }
    if let Some(headers) = custom {
        for (k, v) in headers {
            total += line(k, v.len());
        }
    }
    (total + 2) as u64 // the blank line that ends the request head
}

/// True when `url`'s host is `target` as the sending side parses it. Python's
/// gates check the host with urlsplit, which reads some URLs differently, e.g.
/// `http://other:80\@allowed/` is "allowed" to urlsplit but is sent to
/// "other". Refusing on any disagreement keeps load on the authorized host.
fn url_host_matches(url: &str, target: &str) -> bool {
    let target = target.trim().trim_start_matches('[').trim_end_matches(']');
    if target.is_empty() || target.contains(['/', '\\', '@', '?', '#']) {
        return false;
    }
    let target_url = if target.contains(':') {
        format!("http://[{target}]/")
    } else {
        format!("http://{target}/")
    };
    match (reqwest::Url::parse(url), reqwest::Url::parse(&target_url)) {
        (Ok(a), Ok(b)) => match (a.host_str(), b.host_str()) {
            (Some(x), Some(y)) => !x.is_empty() && x.trim_end_matches('.') == y.trim_end_matches('.'),
            _ => false,
        },
        _ => false,
    }
}

async fn do_request(
    client: &reqwest::Client,
    cfg: &JobConfig,
    http_bytes: u64,
) -> (bool, Option<String>, u64) {
    match cfg.protocol.as_str() {
        "HTTP" | "HTTPS" => {
            // The timeouts are the client's connect + per-read timeouts (like
            // requests' timeout=t), so a response that keeps arriving is not
            // cut off at t, and one that stalls fails.
            let mut builder = client.get(cfg.url.as_str());
            if let Some(headers) = &cfg.headers {
                for (k, v) in headers {
                    builder = builder.header(k.as_str(), v.as_str());
                }
            }
            let mut resp = match builder.send().await {
                Ok(resp) => resp,
                Err(e) => return (false, Some(classify_reqwest(&e)), 0),
            };
            let status = resp.status().as_u16();
            // Read the whole body, as requests does, but chunk by chunk so a
            // large response never sits in memory. A truncated, reset or
            // stalled body is a failed request whatever the status line said.
            loop {
                match resp.chunk().await {
                    Ok(Some(_)) => {}
                    Ok(None) => break,
                    Err(e) => return (false, Some(classify_reqwest(&e)), 0),
                }
            }
            if status < 400 {
                (true, None, http_bytes)
            } else {
                (false, Some(format!("HTTP {status}")), http_bytes)
            }
        }
        "TCP" => {
            let payload = vec![b'X'; cfg.packet_size.max(1) as usize];
            let sent = payload.len() as u64;
            let addr = format!("{}:{}", cfg.target, cfg.port);
            let timeout = Duration::from_millis(cfg.timeout);
            // Connect and write each get their own timeout; a load target may
            // never reply, so sending the packet counts as success (like the
            // Python worker's recv() timeout).
            let mut stream = match tokio::time::timeout(
                timeout,
                tokio::net::TcpStream::connect(addr.as_str()),
            )
            .await
            {
                Ok(Ok(stream)) => stream,
                Ok(Err(e)) => return (false, Some(classify_io(&e)), 0),
                Err(_) => return (false, Some("timeout".to_string()), 0),
            };
            match tokio::time::timeout(timeout, stream.write_all(&payload)).await {
                Ok(Ok(())) => {}
                Ok(Err(e)) => return (false, Some(classify_io(&e)), 0),
                Err(_) => return (false, Some("timeout".to_string()), 0),
            }
            let mut buf = [0u8; 1];
            match tokio::time::timeout(timeout, stream.read(&mut buf)).await {
                // A reset (or any error) after the send is a failure, as
                // Python's recv() raises; a reply, EOF or no reply in time all
                // count as sent.
                Ok(Err(e)) => (false, Some(classify_io(&e)), 0),
                _ => (true, None, sent),
            }
        }
        _ => (false, Some("unsupported protocol".to_string()), 0),
    }
}

// ---------------------------------------------------------------------------
// Job runner
// ---------------------------------------------------------------------------

async fn build_snapshot(
    stats: &Stats,
    sem: &Semaphore,
    threads: u32,
    t0: Instant,
    duration: f64,
) -> Event {
    let now = Instant::now();
    let qps = {
        let mut buckets = stats.buckets.lock().await;
        while let Some(front) = buckets.front() {
            if now.duration_since(front.0) > Duration::from_secs(1) {
                buckets.pop_front();
            } else {
                break;
            }
        }
        buckets.iter().map(|b| b.1).sum::<u64>() as f64
    };
    let avg = {
        let recent = stats.recent.lock().await;
        if recent.is_empty() {
            0.0
        } else {
            recent.iter().sum::<f64>() / recent.len() as f64
        }
    };
    let available = sem.available_permits() as u64;
    let active = (threads as u64).saturating_sub(available);
    let progress = (now.duration_since(t0).as_secs_f64() / duration.max(0.1)).min(1.0);
    Event::Snapshot {
        running: true,
        total: stats.total.load(Ordering::Relaxed),
        success: stats.success.load(Ordering::Relaxed),
        fail: stats.fail.load(Ordering::Relaxed),
        tx: stats.bytes_tx.load(Ordering::Relaxed),
        qps,
        avg,
        active,
        progress,
        last_error: stats.last_error.lock().await.clone(),
    }
}

async fn run_job(cfg: JobConfig, stop: Arc<AtomicBool>, out: Out) {
    // Defense in depth: the Python side already validated this job, but the
    // engine still refuses to run without a concrete target.
    if cfg.target.trim().is_empty() {
        emit(&out, &Event::Error { message: "job has no target".into() }).await;
        return;
    }
    if matches!(cfg.protocol.as_str(), "HTTP" | "HTTPS") && !url_host_matches(&cfg.url, &cfg.target) {
        emit(&out, &Event::Error { message: "url host does not match the job target".into() }).await;
        return;
    }
    let threads = cfg.threads.max(1);
    let duration = cfg.duration.max(0.0);

    let timeout = Duration::from_millis(cfg.timeout.max(1));
    let client = match reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .no_proxy() // never route load through an env/system proxy
        .redirect(reqwest::redirect::Policy::none()) // stay on the authorized host
        .pool_max_idle_per_host(threads as usize)
        .user_agent(USER_AGENT)
        // requests' timeout=t: a connect timeout plus a per-read timeout,
        // not a cap on the whole exchange.
        .connect_timeout(timeout)
        .read_timeout(timeout)
        // As lenient as Python's http.client with sloppy response headers.
        .http1_ignore_invalid_headers_in_responses(true)
        .http1_allow_spaces_after_header_name_in_responses(true)
        .http1_allow_obsolete_multiline_headers_in_responses(true)
        .build()
    {
        Ok(client) => client,
        Err(e) => {
            emit(&out, &Event::Error { message: format!("http client: {e}") }).await;
            return;
        }
    };

    let stats = Arc::new(Stats::new());
    let sem = Arc::new(Semaphore::new(threads as usize));
    let limiter = RateLimiter::new(cfg.rate);
    let t0 = Instant::now();
    let deadline = t0 + Duration::from_secs_f64(duration);

    // Snapshot ticker.
    let snap = {
        let stats = stats.clone();
        let sem = sem.clone();
        let out = out.clone();
        let stop = stop.clone();
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(Duration::from_millis(500));
            ticker.tick().await; // consume the immediate first tick
            loop {
                ticker.tick().await;
                if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
                    break;
                }
                let event = build_snapshot(&stats, &sem, threads, t0, duration).await;
                emit(&out, &event).await;
            }
        })
    };

    // Dispatch loop: one token, one permit, one request task.
    let http_bytes = http_request_bytes(&cfg);
    let cfg = Arc::new(cfg);
    // Flipped once the drain grace is over; cancels requests still in flight.
    let (cancel_tx, cancel_rx) = tokio::sync::watch::channel(false);
    while !stop.load(Ordering::Relaxed) && Instant::now() < deadline {
        if !limiter.acquire(&stop, deadline).await {
            break;
        }
        // Wait for a free slot, re-checking stop: when every slot is busy on a
        // slow target, a stop must not wait for an in-flight request to end.
        let permit = loop {
            let wake = (Instant::now() + Duration::from_millis(100)).min(deadline);
            match tokio::time::timeout_at(wake, sem.clone().acquire_owned()).await {
                Ok(Ok(permit)) => break Some(permit),
                Ok(Err(_)) => break None, // semaphore closed
                Err(_) if stop.load(Ordering::Relaxed) || Instant::now() >= deadline => break None,
                Err(_) => {}
            }
        };
        let Some(permit) = permit else { break };
        if stop.load(Ordering::Relaxed) {
            break;
        }
        let stats = stats.clone();
        let cfg = cfg.clone();
        let client = client.clone();
        let mut cancel = cancel_rx.clone();
        tokio::spawn(async move {
            let started = Instant::now();
            tokio::select! {
                (ok, err, sent) = do_request(&client, &cfg, http_bytes) => {
                    let elapsed = started.elapsed().as_secs_f64() * 1000.0;
                    stats.record(ok, err, sent, elapsed).await;
                }
                // Past the drain grace the request is dropped uncounted, like
                // a Python worker the engine stops waiting for. (The flag only
                // ever flips false -> true, so any change means cancel.)
                _ = cancel.changed() => {}
            }
            drop(permit);
        });
    }

    // The test window ends here (a stop is seen within ~100 ms); time spent
    // draining in-flight requests is not test time (matches the Python engines).
    let ended = Instant::now().min(deadline);
    stop.store(true, Ordering::Relaxed);
    snap.abort();

    // In-flight requests get a short grace to finish (and count), then the
    // rest are cancelled. Waiting for every permit afterwards means no task is
    // still recording while the report is read.
    let grace = Duration::from_millis(cfg.timeout.saturating_add(500)).min(DRAIN_GRACE);
    if tokio::time::timeout(grace, sem.acquire_many(threads)).await.is_err() {
        let _ = cancel_tx.send(true);
        let _ = tokio::time::timeout(Duration::from_millis(500), sem.acquire_many(threads)).await;
    }
    drop(cancel_rx);

    // Final report.
    let elapsed = ended.duration_since(t0).as_secs_f64();
    let mut lats = stats.latencies.lock().await.clone();
    lats.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let avg = if lats.is_empty() {
        0.0
    } else {
        lats.iter().sum::<f64>() / lats.len() as f64
    };
    let bytes_tx = stats.bytes_tx.load(Ordering::Relaxed);
    let report = Event::Report {
        target: cfg.target.clone(),
        protocol: cfg.protocol.clone(),
        duration: elapsed,
        total: stats.total.load(Ordering::Relaxed),
        success: stats.success.load(Ordering::Relaxed),
        fail: stats.fail.load(Ordering::Relaxed),
        avg,
        p50: percentile(&lats, 50.0),
        p90: percentile(&lats, 90.0),
        p99: percentile(&lats, 99.0),
        traffic_mb: bytes_tx as f64 / 1024.0 / 1024.0,
        bytes_tx,
        rate_limit: cfg.rate,
        errors: stats.errors.lock().await.clone(),
    };
    emit(&out, &report).await;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() {
    let out: Out = Arc::new(Mutex::new(tokio::io::stdout()));
    emit(
        &out,
        &Event::Hello {
            engine: "flowbench-engine",
            version: env!("CARGO_PKG_VERSION"),
            protocol: PROTOCOL,
        },
    )
    .await;

    let stop = Arc::new(AtomicBool::new(false));
    let mut reader = BufReader::new(tokio::io::stdin()).lines();

    // Read commands until the first `start` (or an early stop / EOF).
    let cfg = loop {
        match reader.next_line().await {
            Ok(Some(line)) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                match serde_json::from_str::<Command>(trimmed) {
                    Ok(Command::Start { config, .. }) => break config,
                    Ok(Command::Stop) => std::process::exit(0),
                    Err(e) => {
                        emit(&out, &Event::Error { message: format!("bad command: {e}") }).await;
                    }
                }
            }
            Ok(None) => std::process::exit(0), // EOF before any job
            Err(_) => std::process::exit(0),
        }
    };

    // Keep reading stdin during the job: a stop command or EOF (parent gone)
    // both stop the run.
    let watcher = {
        let stop = stop.clone();
        tokio::spawn(async move {
            loop {
                match reader.next_line().await {
                    Ok(Some(line)) => {
                        if let Ok(Command::Stop) = serde_json::from_str::<Command>(line.trim()) {
                            stop.store(true, Ordering::Relaxed);
                            break;
                        }
                    }
                    _ => {
                        stop.store(true, Ordering::Relaxed);
                        break;
                    }
                }
            }
        })
    };

    run_job(cfg, stop.clone(), out.clone()).await;
    watcher.abort();
    // tokio's blocking stdin read cannot be cancelled, so a normal return
    // would hang on runtime shutdown. Exit explicitly now that the report
    // has been flushed.
    std::process::exit(0);
}
