import { useState } from "react";
import { Routes, Route, Link, NavLink } from "react-router-dom";
import { decodeTokenRole, getToken } from "./api/client";
import TokenBar from "./components/TokenBar";
import IncidentList from "./pages/IncidentList";
import IncidentDetail from "./pages/IncidentDetail";
import Dashboard from "./pages/Dashboard";

export default function App() {
  // Display-only role read from whatever token is currently set - hides
  // the Dashboard link for an end_user (GenieBot L1 Enhancement Plan,
  // "Scope what each role can see"). Defaults to showing it (role
  // unknown/no token yet) rather than hiding by default - the API is the
  // real enforcement point either way, so this only affects nav-link
  // convenience, not access.
  const [role, setRole] = useState(() => decodeTokenRole(getToken()));

  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="brand">
          GenieBot L1 Assistant
        </Link>
        <nav className="nav-links">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Incidents
          </NavLink>
          {role !== "end_user" && (
            <NavLink to="/dashboard" className={({ isActive }) => (isActive ? "active" : "")}>
              Dashboard
            </NavLink>
          )}
        </nav>
        <TokenBar onTokenChange={(token) => setRole(decodeTokenRole(token))} />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<IncidentList />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/incidents/:id" element={<IncidentDetail />} />
        </Routes>
      </main>
    </div>
  );
}
