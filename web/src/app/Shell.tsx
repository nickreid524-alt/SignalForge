/**
 * The persistent application shell: charcoal navigation, a thin status bar, and the working canvas.
 *
 * The shell states three facts at all times, because they are the ones a reviewer must not have to
 * guess: the environment is synthetic, which provider mode is in use, and whether the MCP server and
 * API are reachable.
 */

import { NavLink, Outlet } from "react-router-dom";
import { Badge } from "@/components/primitives";
import { useSystem } from "@/hooks/useSystem";

const NAV = [
  { to: "/incidents", label: "Incidents", icon: IconIncidents },
  { to: "/investigations", label: "Investigations", icon: IconInvestigations },
  { to: "/mcp", label: "MCP", icon: IconMcp },
  { to: "/evaluations", label: "Evaluations", icon: IconEvaluations },
  { to: "/system", label: "System", icon: IconSystem },
];

export function Shell() {
  const { meta, health, error } = useSystem();
  const online = !error && health?.status === "ok";
  const liveEnabled = meta?.live_providers_enabled ?? false;

  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="nav__brand">
          <Mark />
          <span className="nav__name">SignalForge</span>
        </div>
        <ul className="nav__list">
          {NAV.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink to={to} className="nav__link">
                <Icon />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="nav__footer">
          <p className="nav__env">
            <strong>{meta?.environment ?? "SignalForge Demo Commerce"}</strong>
            {meta?.dataset_label ?? "Synthetic Operations Environment"}
          </p>
          <p className="nav__env dim" style={{ fontSize: 10 }}>
            All data is synthetic and describes a fictional company.
          </p>
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <div className="topbar__left">
            <Badge tone="synthetic" title="Every service, log, metric and incident here is generated from a fixed seed.">
              SYNTHETIC OPERATIONS ENVIRONMENT
            </Badge>
          </div>
          <div className="topbar__right">
            <Badge tone={liveEnabled ? "live" : "neutral"} title={meta?.mode}>
              {liveEnabled ? "LIVE PROVIDERS ENABLED" : "SCRIPTED DEMONSTRATION"}
            </Badge>
            <Badge
              tone={online ? "ok" : "danger"}
              title={meta?.mcp_protocol_version ? `MCP protocol ${meta.mcp_protocol_version}` : undefined}
            >
              <span className="dot" aria-hidden="true" />
              MCP {meta?.mcp_server_name ?? "server"}
            </Badge>
            <Badge tone={online ? "ok" : "danger"}>
              <span className="dot" aria-hidden="true" />
              API {online ? "online" : "offline"}
            </Badge>
          </div>
        </header>
        <main id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Mark() {
  return (
    <svg className="nav__mark" viewBox="0 0 26 26" aria-hidden="true" focusable="false">
      <rect x="0.5" y="0.5" width="25" height="25" rx="5" fill="#23282f" stroke="#3c444e" />
      <path d="M5 17.5l4-6 3.4 4 3.2-8 5.4 10" fill="none" stroke="#4f8ce0" strokeWidth="1.9"
        strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12.4" cy="15.5" r="1.9" fill="#1b1f24" stroke="#4f8ce0" strokeWidth="1.6" />
    </svg>
  );
}

function icon(path: React.ReactNode) {
  return (
    <svg className="nav__icon" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {path}
    </svg>
  );
}

function IconIncidents() {
  return icon(<><path d="M8 1.8l6.2 11.4H1.8L8 1.8z" /><path d="M8 6.4v3.1" /><path d="M8 11.4h.01" /></>);
}
function IconInvestigations() {
  return icon(<><circle cx="7" cy="7" r="4.4" /><path d="M10.4 10.4L14 14" /></>);
}
function IconMcp() {
  return icon(<><rect x="1.8" y="2.4" width="12.4" height="4" rx="1" /><rect x="1.8" y="9.6" width="12.4" height="4" rx="1" /><path d="M4.4 4.4h.01M4.4 11.6h.01" /></>);
}
function IconEvaluations() {
  return icon(<><path d="M2.2 13.4V6.8M6.7 13.4V2.6M11.2 13.4V8.8" /><path d="M1 13.8h14" /></>);
}
function IconSystem() {
  return icon(<><circle cx="8" cy="8" r="2.1" /><path d="M8 1.4v2M8 12.6v2M14.6 8h-2M3.4 8h-2M12.7 3.3l-1.4 1.4M4.7 11.3l-1.4 1.4M12.7 12.7l-1.4-1.4M4.7 4.7L3.3 3.3" /></>);
}
