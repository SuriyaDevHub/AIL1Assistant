import { useState } from "react";
import { generateDevToken, getToken, setToken } from "../api/client";

// Dev-only auth affordance - see api/client.ts header comment. In a real
// deployment this bar is replaced by an OpenAM login redirect. Two ways
// to get a token: paste one you already have, or generate one against
// the backend's placeholder user directory (POST /auth/dev-token) -
// the generator validates the picked role against that user, it isn't
// a free pass to any role.
export default function TokenBar({ onTokenChange }) {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);

  const [username, setUsername] = useState("");
  const [role, setRole] = useState("end_user");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState(null);

  function applyToken(token) {
    setValue(token);
    setToken(token);
    setSaved(true);
    onTokenChange?.(token);
  }

  async function handleGenerate() {
    if (!username.trim()) {
      setGenError("enter a username");
      return;
    }
    setGenerating(true);
    setGenError(null);
    try {
      const result = await generateDevToken(username.trim(), role);
      applyToken(result.token);
    } catch (e) {
      setGenError(String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="token-bar">
      <input
        type="password"
        placeholder="dev bearer token"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setSaved(false);
        }}
      />
      <button onClick={() => applyToken(value)}>{saved ? "Saved" : "Set token"}</button>
      <span className="token-bar-divider">or generate</span>
      <input
        placeholder="username"
        value={username}
        onChange={(e) => {
          setUsername(e.target.value);
          setGenError(null);
        }}
        className="token-bar-username"
      />
      <select value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="end_user">end_user</option>
        <option value="l2_support">l2_support</option>
        <option value="admin">admin</option>
      </select>
      <button disabled={generating} onClick={handleGenerate}>
        {generating ? "Generating..." : "Generate"}
      </button>
      {genError && <span className="error token-bar-error">{genError}</span>}
    </div>
  );
}
