import { FormEvent, useMemo, useState, useEffect } from "react";
import { Eye, EyeOff, KeyRound, Lock, LogIn, UserRound, Fingerprint } from "lucide-react";
import { messageForError } from "../api/errors";
import { NexoraBrand } from "./NexoraBrand";
import { Capacitor } from "@capacitor/core";
import { NativeBiometric } from "@capgo/capacitor-native-biometric";

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
  const [hasBiometrics, setHasBiometrics] = useState(false);
  const [isBiometryAvailable, setIsBiometryAvailable] = useState(false);

  useEffect(() => {
    const checkBiometrics = async () => {
      if (!Capacitor.isNativePlatform()) return;
      try {
        const available = await NativeBiometric.isAvailable();
        if (available.isAvailable) {
          setIsBiometryAvailable(true);
          try {
            const creds = await NativeBiometric.getCredentials({ server: "nexora.login" });
            if (creds && creds.username && creds.password) {
              setHasBiometrics(true);
            }
          } catch {
            // No credentials saved yet
          }
        }
      } catch (err) {
        console.warn("Biometrics check failed", err);
      }
    };
    checkBiometrics();
  }, []);

  const loginWithBiometrics = async () => {
    if (loading) return;
    setLoading(true);
    setError("");
    try {
      await NativeBiometric.verifyIdentity({
        reason: "Inicia sesión rápidamente con tu huella o rostro",
        title: "Nexora Play",
        subtitle: "Inicio de sesión",
        description: "Usa biometría para entrar a Nexora",
      });
      const creds = await NativeBiometric.getCredentials({ server: "nexora.login" });
      await onLogin(creds.username, creds.password, undefined);
    } catch (err) {
      if (String(err).includes("User cancelled") || String(err).includes("cancelado")) {
        // Just ignore cancellations
      } else {
        setError("La validación biométrica falló o fue cancelada.");
      }
    } finally {
      setLoading(false);
    }
  };

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
      // If login succeeds and we are on password mode and device supports it, save creds
      if (mode === "password" && isBiometryAvailable) {
        try {
          await NativeBiometric.setCredentials({
            username: username.trim(),
            password: secret.trim(),
            server: "nexora.login",
          });
        } catch (e) {
          console.warn("Could not save biometric credentials", e);
        }
      }
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

          <div style={{ display: "flex", gap: "10px", marginTop: "1rem" }}>
            <button className="primary-action" style={{ flex: 1 }} type="submit" disabled={!canSubmit}>
              <LogIn size={18} />
              <span>{loading ? "Conectando" : "Entrar"}</span>
            </button>
            
            {hasBiometrics && (
              <button
                type="button"
                className="secondary-action"
                style={{
                  padding: "0 1.5rem",
                  background: "var(--surface-sunken)",
                  border: "1px solid var(--border-base)",
                  borderRadius: "var(--radius-md)",
                  color: "var(--text-bright)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: "pointer",
                  transition: "background 0.2s"
                }}
                onClick={loginWithBiometrics}
                disabled={loading}
                title="Iniciar sesión con huella"
                aria-label="Iniciar sesión con huella"
              >
                <Fingerprint size={24} />
              </button>
            )}
          </div>
        </form>
      </section>
    </main>
  );
}
