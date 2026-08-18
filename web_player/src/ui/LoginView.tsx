import { FormEvent, useMemo, useState } from "react";
import { Eye, EyeOff, KeyRound, Lock, LogIn, UserRound } from "lucide-react";
import { messageForError } from "../api/errors";
import { NexoraBrand } from "./NexoraBrand";

type LoginViewProps = {
  onLogin: (username: string, password?: string, activationCode?: string) => Promise<void>;
};

export function LoginView({ onLogin }: LoginViewProps) {
  const [mode, setMode] = useState<"password" | "activation">("password");
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const canSubmit = useMemo(
    () => username.trim().length > 0 && secret.trim().length > 0 && !loading,
    [username, secret, loading],
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError("");
    try {
      await onLogin(
        username,
        mode === "password" ? secret : undefined,
        mode === "activation" ? secret : undefined,
      );
    } catch (err) {
      setError(messageForError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="screen auth-screen">
      <div className="bg-layer" />
      <section className="login-shell" aria-label="Nexora login">
        <NexoraBrand />
        <form className="login-panel" onSubmit={submit}>
          <div className="segmented" role="tablist" aria-label="Modo de acceso">
            <button
              type="button"
              className={mode === "password" ? "selected" : ""}
              onClick={() => {
                setMode("password");
                setSecret("");
              }}
            >
              <Lock size={16} />
              <span>Password</span>
            </button>
            <button
              type="button"
              className={mode === "activation" ? "selected" : ""}
              onClick={() => {
                setMode("activation");
                setSecret("");
              }}
            >
              <KeyRound size={16} />
              <span>Codigo</span>
            </button>
          </div>

          <label className="field">
            <span>Usuario</span>
            <div className="field-input">
              <UserRound size={18} />
              <input
                autoFocus
                autoComplete="username"
                value={username}
                placeholder="Ingresa tu usuario"
                onChange={(event) => setUsername(event.target.value)}
              />
            </div>
          </label>

          <label className="field">
            <span>{mode === "password" ? "Password" : "Codigo de activacion"}</span>
            <div className="field-input">
              {mode === "password" ? <Lock size={18} /> : <KeyRound size={18} />}
              <input
                type={mode === "password" ? (showSecret ? "text" : "password") : "text"}
                autoComplete={mode === "password" ? "current-password" : "one-time-code"}
                value={secret}
                placeholder={mode === "password" ? "Ingresa tu contraseña" : "Ej: ACT-1234"}
                onChange={(event) => setSecret(event.target.value)}
              />
              {mode === "password" && (
                <button
                  type="button"
                  className="password-toggle-btn"
                  onClick={() => setShowSecret((prev) => !prev)}
                  tabIndex={-1}
                  title={showSecret ? "Ocultar contraseña" : "Ver contraseña"}
                  aria-label={showSecret ? "Ocultar contraseña" : "Ver contraseña"}
                >
                  {showSecret ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              )}
            </div>
          </label>

          {error && <div className="inline-error">{error}</div>}

          <button className="primary-action" type="submit" disabled={!canSubmit}>
            <LogIn size={18} />
            <span>{loading ? "Conectando" : "Entrar"}</span>
          </button>
        </form>
      </section>
    </main>
  );
}
