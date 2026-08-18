import { chromium, devices } from "playwright";
import { createServer } from "vite";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const webPlayerDir = __dirname;
const outputDir = "/home/brgo-solventyc/Escritorio/nexora_img";

if (!fs.existsSync(outputDir)) {
  fs.mkdirSync(outputDir, { recursive: true });
}

console.log("=================================================");
console.log("📸 GENERADOR DE CAPTURAS MÓVILES (PLAYWRIGHT)");
console.log(`📁 Carpeta destino: ${outputDir}`);
console.log("=================================================");

async function main() {
  console.log("▶️  Iniciando servidor de desarrollo Vite...");
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

  // --- 1. GOOGLE PLAY PHONE — MÓVIL REAL (1080 x 2400 escalado mobile) ---
  console.log("📱 [1/7] Generando capturas para Google Play Teléfono (Vista móvil nativa)...");
  {
    const context = await browser.newContext({
      viewport: { width: 412, height: 915 },
      deviceScaleFactor: 2.625, // Genera imagen de 1081 x 2401 px nítida
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "login");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "01_google_play_phone_login.png") });

    const pageHome = await context.newPage();
    await injectMockState(pageHome, "home");
    await pageHome.goto(url, { waitUntil: "networkidle" });
    await pageHome.waitForTimeout(600);
    await pageHome.screenshot({ path: path.join(outputDir, "02_google_play_phone_canales.png") });
    await context.close();
  }

  // --- 2. GOOGLE PLAY ANDROID TV (1920 x 1080) ---
  console.log("📺 [2/7] Generando capturas para Android TV (1920x1080)...");
  {
    const context = await browser.newContext({
      viewport: { width: 1920, height: 1080 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await injectMockState(page, "home");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "03_google_play_android_tv_guia.png") });
    await context.close();
  }

  // --- 3. GOOGLE PLAY TABLET 10" (2560 x 1600) ---
  console.log("📱 [3/7] Generando capturas para Tablet 10\"...");
  {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "home");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "05_google_play_tablet_10in.png") });
    await context.close();
  }

  // --- 4. APPLE APP STORE - IPHONE 6.7" (1290 x 2796) ---
  console.log("🍎 [4/7] Generando capturas para iPhone 6.7\" (Mobile Nativo)...");
  {
    const context = await browser.newContext({
      viewport: { width: 430, height: 932 },
      deviceScaleFactor: 3, // Genera 1290 x 2796 px exactos
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "login");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "06_app_store_iphone_login.png") });

    const pageHome = await context.newPage();
    await injectMockState(pageHome, "home");
    await pageHome.goto(url, { waitUntil: "networkidle" });
    await pageHome.waitForTimeout(600);
    await pageHome.screenshot({ path: path.join(outputDir, "07_app_store_iphone_canales.png") });
    await context.close();
  }

  // --- 5. APPLE APP STORE - IPAD PRO (2048 x 2732) ---
  console.log("🍎 [5/7] Generando capturas para iPad Pro 12.9\"...");
  {
    const context = await browser.newContext({
      viewport: { width: 1024, height: 1366 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const page = await context.newPage();
    await injectMockState(page, "home");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "08_app_store_ipad_pro.png") });
    await context.close();
  }

  // --- 6. FEATURE GRAPHIC / BANNER (1024 x 500) ---
  console.log("🎨 [6/7] Generando Banner Google Play (1024x500)...");
  {
    const context = await browser.newContext({
      viewport: { width: 1024, height: 500 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await injectMockState(page, "home");
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outputDir, "09_feature_graphic_1024x500.png") });
    await context.close();
  }

  // --- 7. APP ICON (512 x 512) ---
  console.log("🎨 [7/7] Generando Icono de la App (512x512)...");
  {
    const context = await browser.newContext({
      viewport: { width: 512, height: 512 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(outputDir, "10_app_icon_512x512.png") });
    await context.close();
  }

  await browser.close();
  await server.close();

  console.log("=================================================");
  console.log("✅ ¡CAPTURAS MÓVILES REGENERADAS CON ÉXITO!");
  console.log(`📁 Revisa la carpeta: ${outputDir}`);
  console.log("=================================================");
}

main().catch((err) => {
  console.error("❌ Error generando capturas:", err);
  process.exit(1);
});
