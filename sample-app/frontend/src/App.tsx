import { useState, useEffect } from "react";
import {
  signInWithRedirect,
  signOut,
  fetchAuthSession,
  getCurrentUser,
} from "aws-amplify/auth";
import { Hub } from "aws-amplify/utils";

const App = () => {
  const [user, setUser] = useState<string | null>(null);
  const [whoami, setWhoami] = useState<{ email: string; groups: string[] } | null>(null);
  const [loading, setLoading] = useState(true);

  const checkUser = async () => {
    try {
      await getCurrentUser();
      const session = await fetchAuthSession();
      const email = session.tokens?.idToken?.payload?.email as string;
      setUser(email ?? "unknown");
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const hubListener = Hub.listen("auth", ({ payload }) => {
      switch (payload.event) {
        case "signedIn":
        case "signInWithRedirect":
        case "tokenRefresh":
          checkUser();
          break;
        case "signInWithRedirect_failure":
          console.error("OAuth redirect failed:", payload);
          setLoading(false);
          break;
      }
    });
    checkUser();
    return () => hubListener();
  }, []);

  const handleSignIn = async () => {
    await signInWithRedirect({ provider: { custom: "IDC" } });
  };

  const handleSignOut = async () => {
    await signOut();
    setUser(null);
    setWhoami(null);
  };

  const handleWhoAmI = async () => {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    const resp = await fetch("/api/whoami", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await resp.json();
    setWhoami({ email: data.email, groups: data.groups });
  };

  if (loading) return <p>Loading...</p>;

  return (
    <div style={{ fontFamily: "system-ui", maxWidth: 480, margin: "80px auto", textAlign: "center" }}>
      <h1>IDC SAML Sample</h1>
      {!user ? (
        <button onClick={handleSignIn} style={btnStyle}>
          Sign In with IDC
        </button>
      ) : (
        <div>
          <p>
            Signed in as: <strong>{user}</strong>
          </p>
          <button onClick={handleWhoAmI} style={btnStyle}>
            Who Am I?
          </button>
          {whoami && (
            <div style={{ textAlign: "left", margin: "16px auto", maxWidth: 320 }}>
              <p>Email: <strong>{whoami.email}</strong></p>
              <p>Groups: <strong>{whoami.groups.length > 0 ? whoami.groups.join(", ") : "(none)"}</strong></p>
            </div>
          )}
          <br />
          <button onClick={handleSignOut} style={{ ...btnStyle, background: "#888" }}>
            Sign Out
          </button>
        </div>
      )}
    </div>
  );
};

const btnStyle: React.CSSProperties = {
  padding: "12px 24px",
  fontSize: 16,
  cursor: "pointer",
  border: "none",
  borderRadius: 6,
  background: "#0073e6",
  color: "#fff",
  margin: 8,
};

export default App;
