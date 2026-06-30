import { useState } from 'react'
import type { ReactNode } from 'react'
import { CodeBlock } from './quickstartShared'
import styles from './mcpConfig.module.css'

type Mode = 'local' | 'ssh' | 'http'

const TABS: { id: Mode; label: string; blurb: string }[] = [
  { id: 'local', label: 'Local', blurb: 'Codex and the reviewer on one machine — stdio, the default.' },
  { id: 'ssh', label: 'SSH', blurb: 'Codex local, reviewer on a remote host — stdio tunnelled over SSH.' },
  { id: 'http', label: 'HTTP', blurb: 'Reviewer reachable over HTTP with a bearer token — org-wide.' },
]

const CLIENT = '~/.codex/config.toml'

type Block = { label: string; code: string }
type Config = { steps: ReactNode[]; blocks: Block[]; note?: ReactNode }

const CONFIG: Record<Mode, Config> = {
  local: {
    steps: [
      'Nothing to enable — stdio is the default transport.',
      <>
        Point the client at <code>bin/bubo mcp</code>.
      </>,
    ],
    blocks: [
      {
        label: `Client — ${CLIENT}`,
        code: `[mcp_servers.bubo]
command = "/absolute/path/to/bubo/bin/bubo"
args    = ["mcp"]
startup_timeout_sec = 20
tool_timeout_sec    = 1800   # >= [review].timeout_seconds for review_change`,
      },
    ],
  },
  ssh: {
    steps: [
      'Use key-based SSH to the reviewer host — an interactive password breaks the stdio loop.',
      <>
        Keep stdout clean: <code>PrintMotd no</code> server-side, or{' '}
        <code>LogLevel QUIET</code> client-side.
      </>,
      <>
        Point the client at <code>ssh</code> running the remote <code>bubo mcp</code>.
      </>,
    ],
    blocks: [
      {
        label: `Client — ${CLIENT}`,
        code: `[mcp_servers.bubo]
command = "ssh"
args = [
    "-T",                              # no pty; keeps stdout clean for MCP framing
    "-o", "ServerAliveInterval=30",    # keep long review_change calls alive across NAT
    "bubo.example.com",                # ssh_config Host alias, or user@host
    "/opt/bubo/bin/bubo mcp",          # absolute path on the server
]
startup_timeout_sec = 30
tool_timeout_sec    = 1800`,
      },
    ],
  },
  http: {
    steps: [
      <>
        On the reviewer host, set <code>[mcp_server]</code> to HTTP with a bearer token.
      </>,
      <>
        Run <code>bin/bubo mcp</code> (e.g. under a systemd unit).
      </>,
      'Front it with TLS (nginx/caddy) or a VPN — the server does not terminate TLS.',
      'Point the client at the URL with the matching token.',
    ],
    blocks: [
      {
        label: 'Reviewer host — config/env.toml',
        code: `[mcp_server]
transport    = "http"
host         = "0.0.0.0"               # or a specific interface
port         = 8765
bearer_token = "\${BUBO_MCP_TOKEN}"     # generate: openssl rand -hex 32`,
      },
      {
        label: `Client — ${CLIENT}`,
        code: `[mcp_servers.bubo]
url          = "https://reviewer.example.com/mcp"
bearer_token = "..."                   # matches BUBO_MCP_TOKEN
startup_timeout_sec = 30
tool_timeout_sec    = 1800`,
      },
    ],
    note: (
      <>
        The exact client key for HTTP servers varies across Codex versions — check{' '}
        <code>codex --help</code> if these names look wrong.
      </>
    ),
  },
}

export function McpConfig() {
  const [mode, setMode] = useState<Mode>('local')
  const cfg = CONFIG[mode]
  const active = TABS.find((t) => t.id === mode)!

  return (
    <div className={styles.wrap}>
      <div className={styles.tabs} role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={mode === t.id}
            className={`${styles.tab} ${mode === t.id ? styles.tabActive : ''}`}
            onClick={() => setMode(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className={styles.blurb}>{active.blurb}</p>

      <ol className={styles.steps}>
        {cfg.steps.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ol>

      {cfg.blocks.map((b) => (
        <div key={b.label} className={styles.block}>
          <span className={styles.blockLabel}>{b.label}</span>
          <CodeBlock code={b.code} />
        </div>
      ))}

      {cfg.note && <p className={styles.note}>{cfg.note}</p>}
    </div>
  )
}
