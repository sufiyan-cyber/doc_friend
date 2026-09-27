/*
 * ============================================================================
 * OperatorOS — ESP32 SPI TFT Merchant Dashboard & Two-Way Comms (.ino)
 * ============================================================================
 * Hardware Pinout (ESP32 -> SPI Display):
 *   VCC        -> VIN  (5V Power)
 *   GND        -> GND  (Ground)
 *   CS         -> D14  (GPIO 14 - Display CS)
 *   RESET      -> D4   (GPIO 4  - Display Reset)
 *   D/C        -> D27  (GPIO 27 - Data / Command)
 *   SDI (MOSI) -> D23  (GPIO 23 - Shared SPI MOSI)
 *   SCK        -> D18  (GPIO 18 - Shared SPI Clock)
 *   LED        -> 3V3  (Backlight Power)
 *   SDO (MISO) -> D19  (GPIO 19 - Shared SPI MISO)
 *
 * Required Arduino Libraries:
 *   1. Adafruit GFX Library
 *   2. Adafruit ILI9341
 * ============================================================================
 */

#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>

// -------------------- HARDWARE PIN DEFINITIONS --------------------
#define TFT_CS    14  // D14 - Display CS
#define TFT_RST    4  // D4  - Display Reset
#define TFT_DC    27  // D27 - Data / Command
#define TFT_MOSI  23  // D23 - Shared SPI MOSI (SDI)
#define TFT_SCK   18  // D18 - Shared SPI Clock (SCK)
#define TFT_MISO  19  // D19 - Shared SPI MISO (SDO)

#define BTN_BOOT   0  // Built-in ESP32 BOOT button for physical interaction

// -------------------- OPTIONAL WI-FI & BACKEND CONFIG -------------
// Leave WIFI_SSID empty ("") for instant Standalone/Serial Interactive Mode,
// or set your Wi-Fi + PC IP running OperatorOS (port 8000) for live sync.
const char* WIFI_SSID  = "";
const char* WIFI_PASS  = "";
const char* SERVER_URL = "http://192.168.1.100:8000"; // OperatorOS FastAPI Server

// -------------------- RGB565 DASHBOARD COLOR PALETTE --------------
#define COL_BG         0x0861  // Deep Slate Background (#080D18)
#define COL_HEADER     0x0904  // Dark Navy Header (#0A2042)
#define COL_CARD       0x1124  // Card Surface (#112442)
#define COL_BORDER     0x2A69  // Subtle Card Border
#define COL_WHITE      0xFFFF
#define COL_MUTED      0x8C71  // Muted Label Gray
#define COL_GREEN      0x07E8  // Emerald Green (Sales / Approve)
#define COL_CYAN       0x07FF  // Electric Cyan (Cash / Agent)
#define COL_AMBER      0xFD20  // Warning Amber (Low Stock / Approval)
#define COL_CORAL      0xF986  // Coral Red (Overdue Dues / Deny)
#define COL_APPROVAL_BG 0x2940 // Dark Amber Approval Box

Adafruit_ILI9341 tft = Adafruit_ILI9341(&SPI, TFT_DC, TFT_CS, TFT_RST);

// -------------------- MERCHANT DASHBOARD STATE --------------------
struct DashboardState {
  String storeName      = "Green Valley Grocers";
  int    salesToday     = 9850;
  int    cashInDrawer   = 14850;
  int    lowStockCount  = 4;
  int    overdueDues    = 4250;
  int    overdueCount   = 2;
  String agentStatus    = "ONLINE";
  bool   waitingApproval = false;
  String lastMerchantCmd = "Ready at counter";
  String agentMessage   = "Namaste Rajesh-ji! Sales: Rs 9850. 4 dairy/bakery items low before 8PM cutoff. Select an action below or type in Serial.";
  String pendingType    = ""; // "CLOSE_SHOP", "CAMPAIGN", "DUES"
} state;

int selectedTab = 0; // 0: Close Shop, 1: Weekend Promo, 2: Collect Dues, 3: +POS Sale
const char* tabLabels[4] = { "1:Close", "2:Promo", "3:Dues", "4:+Sale" };

unsigned long lastSyncMs = 0;
unsigned long btnDownMs  = 0;
bool btnWasDown          = false;

// ============================================================================
// MINIMAL JSON PARSER (Zero external library dependencies)
// ============================================================================
String extractJsonString(const String& json, const char* key, const String& fallback) {
  String pattern = String("\"") + key + "\":";
  int idx = json.indexOf(pattern);
  if (idx < 0) return fallback;
  int start = json.indexOf('"', idx + pattern.length());
  if (start < 0) return fallback;
  start++;
  String out = "";
  for (int i = start; i < (int)json.length(); i++) {
    if (json[i] == '\\' && i + 1 < (int)json.length()) {
      out += json[i + 1];
      i++;
    } else if (json[i] == '"') {
      break;
    } else {
      out += json[i];
    }
  }
  return out;
}

int extractJsonInt(const String& json, const char* key, int fallback) {
  String pattern = String("\"") + key + "\":";
  int idx = json.indexOf(pattern);
  if (idx < 0) return fallback;
  int start = idx + pattern.length();
  while (start < (int)json.length() && (json[start] == ' ' || json[start] == '"')) start++;
  int end = start;
  while (end < (int)json.length() && (isDigit(json[end]) || json[end] == '-')) end++;
  if (end == start) return fallback;
  return json.substring(start, end).toInt();
}

// ============================================================================
// UI RENDERING ENGINE (320x240 Landscape)
// ============================================================================

void drawHeader() {
  tft.fillRect(0, 0, 320, 28, COL_HEADER);
  tft.drawFastHLine(0, 28, 320, COL_BORDER);

  // Brand + Store Title
  tft.setTextSize(1);
  tft.setTextColor(COL_CYAN);
  tft.setCursor(8, 6);
  tft.print("OPERATOR-OS");

  tft.setTextColor(COL_WHITE);
  tft.setCursor(8, 16);
  tft.print(state.storeName.substring(0, 22));

  // Status Pill on Right
  uint16_t pillCol = COL_GREEN;
  String badge = state.agentStatus;
  if (state.waitingApproval) {
    pillCol = COL_AMBER;
    badge = "APPROVAL!";
  } else if (badge == "WORKING") {
    pillCol = COL_CYAN;
  }

  tft.fillRoundRect(222, 5, 92, 18, 4, pillCol);
  tft.setTextColor(0x0000);
  int textX = 222 + (92 - badge.length() * 6) / 2;
  tft.setCursor(max(225, textX), 10);
  tft.print(badge);
}

void drawMetricCard(int x, int y, int w, int h, const char* label, const String& val, uint16_t accent) {
  tft.fillRoundRect(x, y, w, h, 4, COL_CARD);
  tft.drawRoundRect(x, y, w, h, 4, COL_BORDER);
  tft.fillRoundRect(x, y, 4, h, 2, accent);

  tft.setTextSize(1);
  tft.setTextColor(COL_MUTED);
  tft.setCursor(x + 10, y + 5);
  tft.print(label);

  tft.setTextSize(2);
  tft.setTextColor(COL_WHITE);
  tft.setCursor(x + 10, y + 16);
  tft.print(val);
}

void drawKpiGrid() {
  // 2x2 Grid of Merchant Metrics
  drawMetricCard(6,   33, 151, 35, "TODAY'S SALES",  "Rs " + String(state.salesToday),   COL_GREEN);
  drawMetricCard(163, 33, 151, 35, "CASH DRAWER",    "Rs " + String(state.cashInDrawer), COL_CYAN);
  drawMetricCard(6,   72, 151, 35, "LOW STOCK (<8P)", String(state.lowStockCount) + " Items", COL_AMBER);
  drawMetricCard(163, 72, 151, 35, "OVERDUE DUES",   "Rs " + String(state.overdueDues),  COL_CORAL);
}

void drawWrappedText(int x, int y, int maxCharsPerLine, int maxLines, const String& text, uint16_t color) {
  tft.setTextSize(1);
  tft.setTextColor(color);

  int len = text.length();
  int pos = 0;
  for (int line = 0; line < maxLines && pos < len; line++) {
    int end = min(pos + maxCharsPerLine, len);
    if (end < len) {
      int lastSpace = text.lastIndexOf(' ', end);
      if (lastSpace > pos) end = lastSpace;
    }
    String slice = text.substring(pos, end);
    slice.trim();
    tft.setCursor(x, y + line * 11);
    tft.print(slice);
    pos = end;
    while (pos < len && text[pos] == ' ') pos++;
  }
}

void drawCommsPanel() {
  uint16_t boxBg     = state.waitingApproval ? COL_APPROVAL_BG : COL_CARD;
  uint16_t boxBorder = state.waitingApproval ? COL_AMBER       : COL_CYAN;

  tft.fillRoundRect(6, 111, 308, 95, 5, boxBg);
  tft.drawRoundRect(6, 111, 308, 95, 5, boxBorder);

  // Sub-header inside Comms Panel
  tft.setTextSize(1);
  tft.setTextColor(state.waitingApproval ? COL_AMBER : COL_CYAN);
  tft.setCursor(12, 116);
  if (state.waitingApproval) {
    tft.print("! HUMAN APPROVAL CHECKPOINT REQUIRED !");
  } else {
    tft.print("MERCHANT <-> OPERATOR-OS COMMS");
  }

  // Last Merchant Command
  tft.setTextColor(COL_MUTED);
  tft.setCursor(12, 128);
  tft.print("> You: ");
  tft.setTextColor(COL_WHITE);
  tft.print(state.lastMerchantCmd.substring(0, 38));

  tft.drawFastHLine(12, 138, 296, COL_BORDER);

  // OperatorOS Spoken / Text Response
  int maxLines = state.waitingApproval ? 3 : 5;
  drawWrappedText(12, 142, 48, maxLines, state.agentMessage, COL_WHITE);

  // If Waiting for Approval, render [A] APPROVE and [D] DENY buttons
  if (state.waitingApproval) {
    // Approve Button
    tft.fillRoundRect(14, 180, 140, 20, 4, COL_GREEN);
    tft.setTextColor(0x0000);
    tft.setCursor(24, 186);
    tft.print("[A / TAP] APPROVE");

    // Deny Button
    tft.fillRoundRect(166, 180, 140, 20, 4, COL_CORAL);
    tft.setTextColor(COL_WHITE);
    tft.setCursor(184, 186);
    tft.print("[D / HOLD] DENY");
  }
}

void drawBottomActionDock() {
  tft.fillRect(0, 210, 320, 30, COL_HEADER);
  tft.drawFastHLine(0, 210, 320, COL_BORDER);

  for (int i = 0; i < 4; i++) {
    int bx = 6 + i * 78;
    bool active = (i == selectedTab) && !state.waitingApproval;
    uint16_t bg = active ? COL_CYAN   : COL_CARD;
    uint16_t fg = active ? 0x0000     : COL_WHITE;
    uint16_t bd = active ? COL_WHITE  : COL_BORDER;

    tft.fillRoundRect(bx, 214, 72, 22, 4, bg);
    tft.drawRoundRect(bx, 214, 72, 22, 4, bd);
    tft.setTextSize(1);
    tft.setTextColor(fg);
    int tx = bx + (72 - strlen(tabLabels[i]) * 6) / 2;
    tft.setCursor(tx, 221);
    tft.print(tabLabels[i]);
  }
}

void renderDashboard() {
  drawHeader();
  drawKpiGrid();
  drawCommsPanel();
  drawBottomActionDock();
}

// ============================================================================
// MERCHANT COMMAND & COMMUNICATION ENGINE (Wi-Fi Backend + Local Fallback)
// ============================================================================

bool sendCommandToBackend(const String& cmd) {
  if (WiFi.status() != WL_CONNECTED || strlen(SERVER_URL) == 0) return false;

  HTTPClient http;
  String url = String(SERVER_URL) + "/api/esp32/command";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);

  String payload = "{\"business_id\":\"biz_001\",\"command\":\"" + cmd + "\"}";
  int code = http.POST(payload);
  if (code == 200) {
    String body = http.getString();
    state.storeName       = extractJsonString(body, "store_name", state.storeName);
    state.salesToday      = extractJsonInt(body, "sales_today", state.salesToday);
    state.cashInDrawer    = extractJsonInt(body, "cash_drawer", state.cashInDrawer);
    state.lowStockCount   = extractJsonInt(body, "low_stock", state.lowStockCount);
    state.overdueDues     = extractJsonInt(body, "overdue_dues", state.overdueDues);
    state.agentStatus     = extractJsonString(body, "agent_status", "ONLINE");
    state.waitingApproval = (extractJsonInt(body, "waiting_approval", 0) == 1);
    state.lastMerchantCmd = extractJsonString(body, "last_cmd", cmd);
    state.agentMessage    = extractJsonString(body, "agent_msg", state.agentMessage);
    http.end();
    return true;
  }
  http.end();
  return false;
}

void syncFromBackend() {
  if (WiFi.status() != WL_CONNECTED || strlen(SERVER_URL) == 0) return;

  HTTPClient http;
  String url = String(SERVER_URL) + "/api/esp32/dashboard?business_id=biz_001";
  http.begin(url);
  http.setTimeout(2500);

  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    int newSales   = extractJsonInt(body, "sales_today", state.salesToday);
    int newDrawer  = extractJsonInt(body, "cash_drawer", state.cashInDrawer);
    bool newAppr   = (extractJsonInt(body, "waiting_approval", 0) == 1);
    String newMsg  = extractJsonString(body, "agent_msg", state.agentMessage);

    if (newSales != state.salesToday || newDrawer != state.cashInDrawer ||
        newAppr != state.waitingApproval || newMsg != state.agentMessage) {
      state.storeName       = extractJsonString(body, "store_name", state.storeName);
      state.salesToday      = newSales;
      state.cashInDrawer    = newDrawer;
      state.lowStockCount   = extractJsonInt(body, "low_stock", state.lowStockCount);
      state.overdueDues     = extractJsonInt(body, "overdue_dues", state.overdueDues);
      state.agentStatus     = extractJsonString(body, "agent_status", "ONLINE");
      state.waitingApproval = newAppr;
      state.agentMessage    = newMsg;
      renderDashboard();
    }
  }
  http.end();
}

void handleMerchantCommand(String rawInput) {
  rawInput.trim();
  if (rawInput.length() == 0) return;

  Serial.println("\n[MERCHANT] > " + rawInput);
  state.lastMerchantCmd = rawInput;
  state.agentStatus = "WORKING";
  drawHeader();

  // Map numeric shortcuts to full merchant commands
  String cmd = rawInput;
  String lower = rawInput;
  lower.toLowerCase();

  if (lower == "1") cmd = "Close my shop for today";
  else if (lower == "2") cmd = "Run a weekend campaign";
  else if (lower == "3") cmd = "Send payment reminders for overdue customer dues";
  else if (lower == "4") cmd = "+sale";

  state.lastMerchantCmd = cmd;

  // Try live OperatorOS Backend over Wi-Fi first
  if (sendCommandToBackend(cmd)) {
    Serial.println("[OPERATOR-OS LIVE] " + state.agentMessage);
    renderDashboard();
    return;
  }

  // Built-in Interactive Agent Engine (works offline / over USB Serial)
  lower = cmd;
  lower.toLowerCase();

  if (state.waitingApproval && (lower == "a" || lower == "approve" || lower == "yes")) {
    state.waitingApproval = false;
    state.agentStatus = "ONLINE";
    if (state.pendingType == "CLOSE_SHOP") {
      state.lowStockCount = 0;
      state.cashInDrawer  = 5000; // Rs 9,850 moved to safe, Rs 5,000 float kept
      state.agentMessage  = "APPROVED! MilkyWay PO (Rs 4,500) dispatched via CALL-E & n8n. Cash drawer reset to Rs 5,000 float.";
    } else if (state.pendingType == "CAMPAIGN") {
      state.agentMessage  = "APPROVED! 15% OFF Weekend Organic Harvest broadcast sent to 142 loyal customers (Est ROI: Rs 22,397).";
    } else if (state.pendingType == "DUES") {
      state.overdueDues   = 0;
      state.agentMessage  = "APPROVED! Payment reminders sent via Telegram & WhatsApp to Priya (Rs 1,850) & Vikram (Rs 2,400).";
    }
    state.pendingType = "";
  }
  else if (state.waitingApproval && (lower == "d" || lower == "deny" || lower == "no")) {
    state.waitingApproval = false;
    state.agentStatus     = "ONLINE";
    state.pendingType     = "";
    state.agentMessage    = "ACTION CANCELLED by merchant. Zero external mutations were executed.";
  }
  else if (lower.indexOf("close") >= 0 || lower.indexOf("reconcile") >= 0 || lower.indexOf("supplier") >= 0) {
    state.waitingApproval = true;
    state.pendingType     = "CLOSE_SHOP";
    state.agentStatus     = "APPROVAL!";
    state.agentMessage    = "Drawer reconciled: Deposit Rs " + String(state.salesToday) + " to safe, keep Rs 5000 float. Staged MilkyWay PO for 4 low items (Rs 4500). Approve?";
  }
  else if (lower.indexOf("campaign") >= 0 || lower.indexOf("promo") >= 0 || lower.indexOf("weekend") >= 0) {
    state.waitingApproval = true;
    state.pendingType     = "CAMPAIGN";
    state.agentStatus     = "APPROVAL!";
    state.agentMessage    = "Staged WhatsApp 15% OFF Honey & Coconut Oil bundle for 142 repeat shoppers (Est. revenue Rs 22,397). Approve broadcast?";
  }
  else if (lower.indexOf("due") >= 0 || lower.indexOf("remind") >= 0 || lower.indexOf("payment") >= 0) {
    state.waitingApproval = true;
    state.pendingType     = "DUES";
    state.agentStatus     = "APPROVAL!";
    state.agentMessage    = "Staged polite payment reminders for Priya Sharma (Rs 1,850) & Vikram Rao (Rs 2,400) overdue >7 days. Approve dispatch?";
  }
  else if (lower == "+sale" || lower.indexOf("record sale") >= 0) {
    state.salesToday   += 650;
    state.cashInDrawer += 650;
    state.agentStatus   = "ONLINE";
    state.agentMessage  = "Recorded walk-in Cash POS sale of Rs 650! Today's sales updated to Rs " + String(state.salesToday) + ".";
  }
  else {
    state.agentStatus  = "ONLINE";
    state.agentMessage = "Status: Sales Rs " + String(state.salesToday) + " | Drawer Rs " + String(state.cashInDrawer) + " | Low Stock: " + String(state.lowStockCount) + " items | Overdue: Rs " + String(state.overdueDues) + ".";
  }

  Serial.println("[OPERATOR-OS] " + state.agentMessage);
  renderDashboard();
}

// ============================================================================
// SETUP & MAIN LOOP
// ============================================================================

void setup() {
  Serial.begin(115200);
  pinMode(BTN_BOOT, INPUT_PULLUP);

  // Ensure TFT CS is HIGH before starting shared SPI bus
  pinMode(TFT_CS, OUTPUT);
  digitalWrite(TFT_CS, HIGH);

  // Initialize Shared Hardware SPI Bus (SCK=18, MISO=19, MOSI=23, CS=14)
  SPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS);

  tft.begin();
  tft.setRotation(1); // Landscape 320x240
  tft.fillScreen(COL_BG);

  // Connect to Wi-Fi if configured
  if (strlen(WIFI_SSID) > 0) {
    state.agentStatus  = "WIFI...";
    state.agentMessage = "Connecting to Wi-Fi: " + String(WIFI_SSID) + "...";
    renderDashboard();

    WiFi.begin(WIFI_SSID, WIFI_PASS);
    unsigned long startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 6000) {
      delay(250);
    }
    if (WiFi.status() == WL_CONNECTED) {
      state.agentStatus  = "ONLINE";
      state.agentMessage = "Connected (" + WiFi.localIP().toString() + "). Synced with OperatorOS.";
      syncFromBackend();
    } else {
      state.agentStatus  = "LOCAL UI";
      state.agentMessage = "Wi-Fi offline. Running interactive counter mode via Serial & BOOT button.";
    }
  }

  renderDashboard();

  Serial.println("================================================================");
  Serial.println("  OPERATOR-OS MERCHANT DASHBOARD READY (ESP32 SPI TFT)");
  Serial.println("================================================================");
  Serial.println("Commands (Type in Serial Monitor or use ESP32 BOOT Button):");
  Serial.println("  [1] Close Shop & Reconcile Cash   [2] Run Weekend Campaign");
  Serial.println("  [3] Send Overdue Dues Reminders   [4] Record +Rs 650 POS Sale");
  Serial.println("  [A] Approve Pending Action        [D] Deny Pending Action");
  Serial.println("  Or type any custom question/command for OperatorOS!");
  Serial.println("================================================================");
}

void loop() {
  // 1. Handle Two-Way Serial Communication from Merchant
  if (Serial.available()) {
    String input = Serial.readStringUntil('\n');
    handleMerchantCommand(input);
  }

  // 2. Handle Physical ESP32 BOOT Button (GPIO 0)
  //    - Short Press (<600ms): Cycle bottom action tabs (or APPROVE if waiting for approval)
  //    - Long Press (>=600ms): Execute selected tab (or DENY if waiting for approval)
  bool isDown = (digitalRead(BTN_BOOT) == LOW);
  if (isDown && !btnWasDown) {
    btnDownMs = millis();
    btnWasDown = true;
  } else if (!isDown && btnWasDown) {
    unsigned long heldMs = millis() - btnDownMs;
    btnWasDown = false;
    if (heldMs > 40) { // debounce
      if (state.waitingApproval) {
        if (heldMs >= 600) {
          handleMerchantCommand("deny");
        } else {
          handleMerchantCommand("approve");
        }
      } else {
        if (heldMs >= 600) {
          handleMerchantCommand(String(selectedTab + 1));
        } else {
          selectedTab = (selectedTab + 1) % 4;
          drawBottomActionDock();
        }
      }
    }
  }

  // 3. Periodic Live Dashboard Sync over Wi-Fi (every 5 seconds)
  if (WiFi.status() == WL_CONNECTED && millis() - lastSyncMs > 5000) {
    lastSyncMs = millis();
    syncFromBackend();
  }
}
