import { useState } from "react";
import { getToken, setToken } from "../api/client";

// Dev-only auth affordance - see api/client.ts header comment. In a real
// deployment this bar is replaced by an OpenAM login redirect.
export default function TokenBar() {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);

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
      <button
        onClick={() => {
          setToken(value);
          setSaved(true);
        }}
      >
        {saved ? "Saved" : "Set token"}
      </button>
    </div>
  );
}
