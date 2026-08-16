import { Routes, Route, Link } from "react-router-dom";
import TokenBar from "./components/TokenBar";
import IncidentList from "./pages/IncidentList";
import IncidentDetail from "./pages/IncidentDetail";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="brand">
          GenieBot L1 Assistant
        </Link>
        <TokenBar />
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<IncidentList />} />
          <Route path="/incidents/:id" element={<IncidentDetail />} />
        </Routes>
      </main>
    </div>
  );
}
