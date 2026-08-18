import { chromium } from "playwright";
import { createServer } from "vite";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const webPlayerDir = __dirname;
const baseOut = "/home/brgo-solventyc/Escritorio/nexora_img";

const ios65Dir = path.join(baseOut, "App_Store_iPhone_6.5_Pulgadas");
const ios67Dir = path.join(baseOut, "App_Store_iPhone_6.7_Pulgadas");
const ipadDir  = path.join(baseOut, "App_Store_iPad_12.9_Pulgadas");
const gplayDir = path.join(baseOut, "Google_Play_Store");

[baseOut, ios65Dir, ios67Dir, ipadDir, gplayDir].forEach((dir) => {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
});

console.log("=================================================");
console.log("📸 GENERANDO CAPTURAS REALES CON REPRODUCTOR Y CANALES");
console.log("=================================================");

async function main() {
  const server = await createServer({
    root: webPlayerDir,
    server: { port: 5183, host: "127.0.0.1" },
  });
  await server.listen();
  const url = "http://127.0.0.1:5183";

  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const mockChannels = [
    { id: "44def099", channel_key: "canal-1", number: 1, name: "TELENOSTALGIA HD", category: "peliculas", enabled: true, epg_now: "La Pasión de Vivir (En Vivo)", epg_next: "Cine de Oro Clásico" },
    { id: "9e9b8710", channel_key: "canal-5", number: 5, name: "GOLDEN PLUS HD", category: "peliculas", enabled: true, epg_now: "Estreno: Diamante Salvaje", epg_next: "Noche de Acción" },
    { id: "265f82cb", channel_key: "canal-6", number: 6, name: "GOLDEN PREMIER", category: "peliculas", enabled: true, epg_now: "El Vencedor - Directo", epg_next: "Thriller a Medianoche" },
    { id: "8f1a23bc", channel_key: "canal-7", number: 7, name: "LAS ESTRELLAS HD", category: "entretenimiento", enabled: true, epg_now: "Noticiero Estelar Internacional", epg_next: "Novela Central" },
    { id: "3c9d12ae", channel_key: "canal-8", number: 8, name: "TLNOVELAS", category: "entretenimiento", enabled: true, epg_now: "Amor y Destino - Cap 45", epg_next: "Especial Novelas" },
    { id: "1a8e94fb", channel_key: "canal-9", number: 9, name: "CINE HISPANO", category: "peliculas", enabled: true, epg_now: "Voces de la Selva 4K", epg_next: "Documental Andino" },
  ];

  const mockProfile = {
    subscriber_id: "90a0f9f5-85cc-4df9-96b1-4477fd295490",
    username: "testuser1",
    plan_name: "Plan Premium Full HD + Deportes",
    active_screens: 1,
    max_screens: 3,
    status: "active",
  };

  async function setupMockRoutes(page, state = "home") {
    await page.route("**/api/client/auth/login", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "mock-jwt",
          refresh_token: "mock-refresh",
          token_type: "bearer",
          expires_in: 86400,
          subscriber_id: "90a0f9f5-85cc-4df9-96b1-4477fd295490",
          device_registration: "registered",
        }),
      })
    );

    await page.route("**/api/client/profile", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockProfile) })
    );

    await page.route("**/api/client/profile/devices", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ id: "dev-1", device_id: "test-device-001", is_blocked: false }]),
      })
    );

    await page.route("**/api/client/catalog/channels", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockChannels) })
    );

    await page.route("**/api/client/playback/authorize", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          token: "mock-token",
          playback_url: "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
          channel_id: "canal-1",
        }),
      })
    );

    if (state !== "login") {
      await page.addInitScript(() => {
        localStorage.setItem(
          "nexora.web_player.session.v1",
          JSON.stringify({
            accessToken: "mock-jwt",
            refreshToken: "mock-refresh",
            tokenType: "bearer",
            subscriberId: "90a0f9f5-85cc-4df9-96b1-4477fd295490",
            accessExpiresAt: Date.now() + 86400000,
          })
        );
        localStorage.setItem("nexora.web_player.device_id.v1", "test-device-001");
      });
    } else {
      await page.addInitScript(() => {
        localStorage.clear();
      });
    }
  }

  // --- 1. IPHONE 6.5" (1284 x 2778) ---
  console.log("📱 [1/4] Generando iPhone 6.5\" (1284 x 2778)...");
  {
    const context = await browser.newContext({
      viewport: { width: 428, height: 926 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
    });
    
    // Login
    const p1 = await context.newPage();
    await setupMockRoutes(p1, "login");
    await p1.goto(url, { waitUntil: "networkidle" });
    await p1.waitForTimeout(600);
    await p1.screenshot({ path: path.join(ios65Dir, "01_login_1284x2778.png") });

    // Canales
    const p2 = await context.newPage();
    await setupMockRoutes(p2, "home");
    await p2.goto(url, { waitUntil: "networkidle" });
    await p2.waitForTimeout(600);
    await p2.screenshot({ path: path.join(ios65Dir, "02_canales_1284x2778.png") });

    // Reproductor en vivo
    try {
      const channelBtn = p2.locator(".channel-card, .channel-item, button").filter({ hasText: /TELENOSTALGIA/i }).first();
      if (await channelBtn.isVisible()) {
        await channelBtn.click();
        await p2.waitForTimeout(800);
      }
    } catch {}
    await p2.screenshot({ path: path.join(ios65Dir, "03_reproductor_1284x2778.png") });

    await context.close();
  }

  // --- 2. IPHONE 6.7" (1290 x 2796) ---
  console.log("📱 [2/4] Generando iPhone 6.7\" (1290 x 2796)...");
  {
    const context = await browser.newContext({
      viewport: { width: 430, height: 932 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
    });

    const p1 = await context.newPage();
    await setupMockRoutes(p1, "login");
    await p1.goto(url, { waitUntil: "networkidle" });
    await p1.waitForTimeout(600);
    await p1.screenshot({ path: path.join(ios67Dir, "01_login_1290x2796.png") });

    const p2 = await context.newPage();
    await setupMockRoutes(p2, "home");
    await p2.goto(url, { waitUntil: "networkidle" });
    await p2.waitForTimeout(600);
    await p2.screenshot({ path: path.join(ios67Dir, "02_canales_1290x2796.png") });

    try {
      const channelBtn = p2.locator(".channel-card, .channel-item, button").filter({ hasText: /TELENOSTALGIA/i }).first();
      if (await channelBtn.isVisible()) {
        await channelBtn.click();
        await p2.waitForTimeout(800);
      }
    } catch {}
    await p2.screenshot({ path: path.join(ios67Dir, "03_reproductor_1290x2796.png") });

    await context.close();
  }

  // --- 3. IPAD PRO 12.9" / 13" (2048 x 2732 & 2732 x 2048) ---
  console.log("📱 [3/4] Generando iPad Pro 12.9\"/13\" (2048 x 2732)...");
  {
    const contextV = await browser.newContext({
      viewport: { width: 1024, height: 1366 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const pIpadV = await contextV.newPage();
    await setupMockRoutes(pIpadV, "home");
    await pIpadV.goto(url, { waitUntil: "networkidle" });
    await pIpadV.waitForTimeout(700);
    await pIpadV.screenshot({ path: path.join(ipadDir, "01_ipad_canales_2048x2732.png") });
    await contextV.close();

    const contextH = await browser.newContext({
      viewport: { width: 1366, height: 1024 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const pIpadH = await contextH.newPage();
    await setupMockRoutes(pIpadH, "home");
    await pIpadH.goto(url, { waitUntil: "networkidle" });
    await pIpadH.waitForTimeout(700);
    await pIpadH.screenshot({ path: path.join(ipadDir, "02_ipad_horizontal_2732x2048.png") });
    await contextH.close();
  }

  // --- 4. GOOGLE PLAY STORE ---
  console.log("🤖 [4/4] Generando Google Play Store...");
  {
    const context = await browser.newContext({
      viewport: { width: 412, height: 915 },
      deviceScaleFactor: 2.625,
      isMobile: true,
      hasTouch: true,
    });
    const p1 = await context.newPage();
    await setupMockRoutes(p1, "login");
    await p1.goto(url, { waitUntil: "networkidle" });
    await p1.waitForTimeout(600);
    await p1.screenshot({ path: path.join(gplayDir, "01_phone_login_1080x2400.png") });

    const p2 = await context.newPage();
    await setupMockRoutes(p2, "home");
    await p2.goto(url, { waitUntil: "networkidle" });
    await p2.waitForTimeout(600);
    await p2.screenshot({ path: path.join(gplayDir, "02_phone_canales_1080x2400.png") });
    await context.close();
  }

  await browser.close();
  await server.close();

  console.log("=================================================");
  console.log("✅ ¡CAPTURAS EXACTAS GENERADAS CON ÉXITO!");
  console.log(`📁 Revisa: ${baseOut}`);
  console.log("=================================================");
}

main().catch((err) => {
  console.error("❌ Error generando capturas:", err);
  process.exit(1);
});
