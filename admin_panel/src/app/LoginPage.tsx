import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { LogIn } from "lucide-react";

import { messageForError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { Button, Field, TextInput } from "../ui/primitives";
import { HOME_PATH } from "./sections";

type LocationState = { from?: string } | null;

/** Login contra POST {prefix}/auth/login (app/api/v1/auth.py:16). */
export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as LocationState)?.from;
  const target = from && from !== "/" ? from : HOME_PATH;

  if (status === "authed") return <Navigate to={target} replace />;

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await login({ username, password });
      navigate(target, { replace: true });
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-brand">
          <span className="sidebar-brand-mark" aria-hidden />
          <span>
            Nexora <strong>Admin</strong>
          </span>
        </div>
        <p className="login-subtitle">Panel de administracion</p>

        <Field label="Usuario" required>
          {(props) => (
            <TextInput
              {...props}
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(event) => setUsername(event.target.value)}
            />
          )}
        </Field>

        <Field label="Contrasena" required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>

        {error ? (
          <p className="login-error" role="alert">
            {error}
          </p>
        ) : null}

        <Button
          type="submit"
          variant="primary"
          block
          loading={busy}
          icon={<LogIn size={16} />}
          disabled={!username || !password}
        >
          Entrar
        </Button>
      </form>
    </div>
  );
}
