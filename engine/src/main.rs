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
        RateLimiter {
            rate,
            capacity,
            state: Mutex::new((capacity, Instant::now())),
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
    let mut text = err.to_string();
    let mut source = err.source();
    while let Some(cause) = source {
        text.push_str("; ");
        text.push_str(&cause.to_string());
        source = cause.source();
    }
    text
}

fn classify_reqwest(err: &reqwest::Error) -> String {
    if err.is_timeout() {
        return "timeout".to_string();
    }
    classify_text(&error_chain(err))
}

fn estimate_http_bytes(cfg: &JobConfig) -> u64 {
    let mut total = 3 + cfg.url.len() + 12; // "GET" + url + request-line overhead
    if let Some(headers) = &cfg.headers {
        for (k, v) in headers {
            total += k.len() + v.len() + 4;
        }
    }
    total as u64
}

async fn do_request(client: &reqwest::Client, cfg: &JobConfig) -> (bool, Option<String>, u64) {
    match cfg.protocol.as_str() {
        "HTTP" | "HTTPS" => {
            let mut builder = client
                .get(cfg.url.as_str())
                .timeout(Duration::from_millis(cfg.timeout));
            if let Some(headers) = &cfg.headers {
                for (k, v) in headers {
                    builder = builder.header(k.as_str(), v.as_str());
                }
            }
            match builder.send().await {
                Ok(resp) => {
                    let status = resp.status().as_u16();
                    let ok = status < 400;
                    let sent = estimate_http_bytes(cfg);
                    let _ = resp.bytes().await; // drain so the socket can be reused
                    let err = if ok { None } else { Some(format!("HTTP {status}")) };
                    (ok, err, sent)
                }
                Err(e) => (false, Some(classify_reqwest(&e)), 0),
            }
        }
        _ => {
            let payload = vec![b'X'; cfg.packet_size.max(1) as usize];
            let sent = payload.len() as u64;
            let addr = format!("{}:{}", cfg.target, cfg.port);
            let timeout = Duration::from_millis(cfg.timeout);
            let attempt = async {
                let mut stream = tokio::net::TcpStream::connect(addr.as_str())
                    .await
                    .map_err(|e| e.to_string())?;
                stream.write_all(&payload).await.map_err(|e| e.to_string())?;
                let mut buf = [0u8; 1];
                let _ = tokio::time::timeout(timeout, stream.read(&mut buf)).await;
                Ok::<(), String>(())
            };
            match tokio::time::timeout(timeout, attempt).await {
                Ok(Ok(())) => (true, None, sent),
                Ok(Err(e)) => (false, Some(classify_text(&e)), 0),
                Err(_) => (false, Some("timeout".to_string()), 0),
            }
        }
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
    let threads = cfg.threads.max(1);
    let duration = cfg.duration.max(0.0);

    let client = match reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .pool_max_idle_per_host(threads as usize)
        .user_agent("FlowBench-Engine")
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
    let cfg = Arc::new(cfg);
    while !stop.load(Ordering::Relaxed) && Instant::now() < deadline {
        if !limiter.acquire(&stop, deadline).await {
            break;
        }
        let permit = match sem.clone().acquire_owned().await {
            Ok(permit) => permit,
            Err(_) => break,
        };
        let stats = stats.clone();
        let cfg = cfg.clone();
        let client = client.clone();
        tokio::spawn(async move {
            let started = Instant::now();
            let (ok, err, sent) = do_request(&client, &cfg).await;
            let elapsed = started.elapsed().as_secs_f64() * 1000.0;
            stats.record(ok, err, sent, elapsed).await;
            drop(permit);
        });
    }

    stop.store(true, Ordering::Relaxed);
    snap.abort();

    // Drain in-flight requests (bounded by the per-request timeout).
    let grace = Duration::from_millis(cfg.timeout + 2000);
    let _ = tokio::time::timeout(grace, sem.acquire_many(threads)).await;

    // Final report.
    let elapsed = t0.elapsed().as_secs_f64();
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
                    Ok(Command::Stop) => return,
                    Err(e) => {
                        emit(&out, &Event::Error { message: format!("bad command: {e}") }).await;
                    }
                }
            }
            Ok(None) => return, // EOF before any job
            Err(_) => return,
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
}
