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
console.log("📸 GENERANDO CAPTURAS CON DIMENSIONES EXACTAS APPLE");
console.log("=================================================");

async function main() {
  const server = await createServer({
    root: webPlayerDir,
    server: { port: 5179, host: "127.0.0.1" },
  });
  await server.listen();
  const url = "http://127.0.0.1:5179";

  const browser = await chromium.launch({ headless: true });

  const mockChannels = [
    { id: 1, channel_key: "noticias_hd", name: "Nexora Noticias HD", category: "NOTICIAS", enabled: true, epg_now: "Noticiero Central en Vivo", epg_next: "Resumen Internacional" },
    { id: 2, channel_key: "deportes_max", name: "Nexora Sports Max", category: "DEPORTES", enabled: true, epg_now: "Liga Nacional de Fútbol - En Directo", epg_next: "Análisis Deportivo" },
    { id: 3, channel_key: "cinema_plus", name: "Nexora Cinema Plus", category: "PELICULAS", enabled: true, epg_now: "Estreno: Misión Galáctica", epg_next: "Clásico del Cine" },
    { id: 4, channel_key: "mundo_geo", name: "Nexora Planeta Discovery", category: "DOCUMENTALES", enabled: true, epg_now: "Océanos Secretos 4K", epg_next: "Vida Salvaje" },
    { id: 5, channel_key: "kids_tv", name: "Nexora Kids Channel", category: "INFANTIL", enabled: true, epg_now: "Aventuras Animadas", epg_next: "El Bosque Mágico" },
    { id: 6, channel_key: "musica_hits", name: "Nexora Hits 24/7", category: "MUSICA", enabled: true, epg_now: "Top 40 Éxitos Globales", epg_next: "Conciertos Acústicos" },
  ];

  const mockProfile = {
    subscriber_id: 1042,
    username: "suscriptor.demo",
    plan_name: "Plan Premium 4K + Deportes",
    active_screens: 1,
    max_screens: 3,
    status: "active",
  };

  async function injectMockState(page, view = "home") {
    await page.route("**/api/client/profile", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockProfile) })
    );
    await page.route("**/api/client/channels", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockChannels) })
    );
    await page.route("**/api/client/playback/authorize", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          playback_url: "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
          session_id: "demo-sess-1234",
          expires_at: new Date(Date.now() + 3600000).toISOString(),
        }),
      })
    );

    if (view !== "login") {
      await page.addInitScript(() => {
        localStorage.setItem(
          "nexora_session",
          JSON.stringify({
            accessToken: "mock-jwt-token-123",
            refreshToken: "mock-refresh-token-456",
            username: "suscriptor.demo",
          })
        );
      });
    } else {
      await page.addInitScript(() => {
        localStorage.clear();
      });
    }
  }

  // --- 1. IPHONE 6.5" (EXACTO: 1284 x 2778 px) ---
  console.log("📱 [1/4] Generando iPhone 6.5\" (1284 x 2778)...");
  {
    const context = await browser.newContext({
      viewport: { width: 428, height: 926 },
      deviceScaleFactor: 3, // 428*3 = 1284, 926*3 = 2778
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "login");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(ios65Dir, "01_login_1284x2778.png") });

    const pageHome = await context.newPage();
    await injectMockState(pageHome, "home");
    await pageHome.goto(url, { waitUntil: "networkidle" });
    await pageHome.waitForTimeout(500);
    await pageHome.screenshot({ path: path.join(ios65Dir, "02_canales_1284x2778.png") });
    await context.close();
  }

  // --- 2. IPHONE 6.7" (EXACTO: 1290 x 2796 px) ---
  console.log("📱 [2/4] Generando iPhone 6.7\" (1290 x 2796)...");
  {
    const context = await browser.newContext({
      viewport: { width: 430, height: 932 },
      deviceScaleFactor: 3, // 430*3 = 1290, 932*3 = 2796
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "login");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(ios67Dir, "01_login_1290x2796.png") });

    const pageHome = await context.newPage();
    await injectMockState(pageHome, "home");
    await pageHome.goto(url, { waitUntil: "networkidle" });
    await pageHome.waitForTimeout(500);
    await pageHome.screenshot({ path: path.join(ios67Dir, "02_canales_1290x2796.png") });
    await context.close();
  }

  // --- 3. IPAD PRO 12.9" (EXACTO: 2048 x 2732 px) ---
  console.log("📱 [3/4] Generando iPad Pro 12.9\" (2048 x 2732)...");
  {
    const context = await browser.newContext({
      viewport: { width: 1024, height: 1366 },
      deviceScaleFactor: 2, // 1024*2 = 2048, 1366*2 = 2732
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "home");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(ipadDir, "01_ipad_canales_2048x2732.png") });
    await context.close();
  }

  // --- 4. GOOGLE PLAY PHONE (1080 x 2400) & TV (1920 x 1080) ---
  console.log("🤖 [4/4] Generando Google Play Store...");
  {
    const context = await browser.newContext({
      viewport: { width: 412, height: 915 },
      deviceScaleFactor: 2.625,
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "login");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(gplayDir, "01_phone_login_1080x2400.png") });

    const pageHome = await context.newPage();
    await injectMockState(pageHome, "home");
    await pageHome.goto(url, { waitUntil: "networkidle" });
    await pageHome.waitForTimeout(500);
    await pageHome.screenshot({ path: path.join(gplayDir, "02_phone_canales_1080x2400.png") });
    await context.close();
  }

  await browser.close();
  await server.close();

  console.log("=================================================");
  console.log("✅ ¡CAPTURAS EXACTAS GENERADAS EN CARPETAS INDIVIDUALES!");
  console.log(`📁 Revisa: ${baseOut}`);
  console.log("=================================================");
}

main().catch((err) => {
  console.error("❌ Error generando capturas:", err);
  process.exit(1);
});
