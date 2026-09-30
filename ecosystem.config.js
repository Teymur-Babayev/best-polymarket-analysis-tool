module.exports = {
  apps: [
    {
      name: "pmanalysis",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "-m pmanalysis start",
      interpreter: "none",
      env: {
        PM_HOST: "0.0.0.0",
        PM_PORT: "8000",
        // Small-VPS throttles: the 62 Hz quote-push / 2 Hz REST book-poll
        // defaults pinned the CPU and starved the CLOB WebSocket reader
        // ("slow consumer"), and unthrottled httpx INFO logging grew the PM2
        // error log into the multi-GB range. See pmanalysis/config.py for
        // what each knob does.
        PM_QUOTE_PUSH_SEC: "0.25",
        PM_BOOK_POLL_SEC: "3",
        PM_CLOB_STALE_SEC: "8",
        PM_LIVE_PUSH_MS: "150",
        PM_TICK_RETENTION_HOURS: "72",
      },
      autorestart: true,
      max_restarts: 20,
      min_uptime: "5s",
      time: true,
      // Restart cleanly before the kernel OOM-kills the process (1.9 GiB
      // host, no headroom for an unbounded RSS climb).
      max_memory_restart: "700M",
    },
  ],
};
