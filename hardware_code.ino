#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ESP_I2S.h>
#include <esp_system.h>

// Board settings (Arduino IDE, Tools menu):
//   Board: XIAO_ESP32S3    PSRAM: "OPI PSRAM"    (needed for the 128KB speaker buffer)

// --- Wi-Fi & Bridge Config ---
const char* WIFI_SSID      = "your hotspot name";
const char* WIFI_PASS      = "your hotspot password";
const char* BRIDGE_HOST    = "your PC's IP; hotspot IPs can change"; 
const uint16_t BRIDGE_PORT = 8765;

// --- Hardware Pins ---
#define I2C_SDA       5
#define I2C_SCL       6
#define PDM_CLK_PIN   42
#define PDM_DATA_PIN  41
#define SPK_BCLK_PIN  2
#define SPK_LRC_PIN   1
#define SPK_DOUT_PIN  3

// --- Audio ---
#define SAMPLE_RATE          16000U
#define CHUNK_SAMPLES        640        // 40ms mic chunks (1280 bytes)
#define MIC_GAIN             3          // was 6; speech peaked near full scale
#define SPK_VOLUME_PCT       70         // lower this if the board resets while speaking
#define SPK_RING_SIZE        131072     // 128KB PSRAM ring buffer (~4s of TTS audio)
#define SPK_RING_FALLBACK    32768      // used only if PSRAM is not enabled
#ifndef SPK_SILENCE_PEAK
#define SPK_SILENCE_PEAK     100        // agent frames quieter than this count as silence
#endif
#define SPK_HANGOVER_MS      400UL      // keep accepting audio this long after speech (natural pauses)

// --- Timing ---
#define POST_SPK_MS          500UL      // quiet time after speaker audio before the mic is used
#define POST_FLUSH_MS        300UL      // mic stays muted this long after resuming
#define MIC_WARMUP_CHUNKS    10         // mic chunks thrown away right after the mic starts
#define MIC_FLUSH_CHUNKS     4          // stale mic chunks thrown away after playback
#define GREETING_TIMEOUT_MS  15000UL    // start listening even if no greeting ever arrives
#define WIFI_CHECK_MS        10000UL

Adafruit_SSD1306 display(128, 64, &Wire, -1);
I2SClass i2sMic;
I2SClass i2sSpk;
WebSocketsClient webSocket;

int16_t micBuffer[CHUNK_SAMPLES];
uint8_t* spkRing = nullptr;
size_t spkRingSize = 0;
volatile size_t ringHead = 0;
volatile size_t ringTail = 0;

bool wsConnected = false;
bool greetingReceived = false;
bool micDriverUp = false;
bool micActive = false;
bool wasPlaying = false;
bool playbackActive = false;
unsigned long audioHangoverUntil = 0;
unsigned long silentFrames = 0;
int silentMaxPeak = 0;
unsigned long lastSilenceLogMs = 0;
size_t playbackBytes = 0;
unsigned long lastSpeakerMs = 0;
unsigned long wsConnectedAt = 0;
unsigned long micResumeMs = 0;
unsigned long lastMicTryMs = 0;
unsigned long lastLogMs = 0;
unsigned long lastWifiCheckMs = 0;
float dcEstimate = 0;
bool dcReady = false;
int emptyReads = 0;

// ---------------------------------------------------------------- HUD

void showHUD(const String& status, const String& text) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(status);
  display.println("---------------------");
  display.println(text);
  display.display();
}

// ---------------------------------------------------------------- speaker ring buffer

size_t ringFill() {
  if (spkRingSize == 0) return 0;
  return (ringHead + spkRingSize - ringTail) % spkRingSize;
}

bool hasSpeakerAudio() {
  return ringFill() >= 2;   // whole 16-bit samples only
}

void resetRing() {
  ringHead = 0;
  ringTail = 0;
}

void pushSpeakerAudio(const uint8_t* data, size_t len) {
  if (!spkRing || spkRingSize == 0) return;
  for (size_t i = 0; i < len; i++) {
    size_t nextHead = (ringHead + 1) % spkRingSize;
    if (nextHead == ringTail) {
      ringTail = (ringTail + 1) % spkRingSize;   // buffer full: drop the oldest byte
    }
    spkRing[ringHead] = data[i];
    ringHead = nextHead;
  }
}

void drainSpeakerAudio() {
  alignas(4) static uint8_t tempOut[512];

  size_t n = ringFill();
  if (n > sizeof(tempOut)) n = sizeof(tempOut);
  n &= ~(size_t)1;                                // even number of bytes
  if (n == 0) return;

  for (size_t i = 0; i < n; i++) {
    tempOut[i] = spkRing[ringTail];
    ringTail = (ringTail + 1) % spkRingSize;
  }

  if (SPK_VOLUME_PCT != 100) {
    int16_t* s = (int16_t*)tempOut;
    for (size_t i = 0; i < n / 2; i++) {
      s[i] = (int16_t)(((int32_t)s[i] * SPK_VOLUME_PCT) / 100);
    }
  }

  if (!playbackActive) {
    playbackActive = true;
    playbackBytes = 0;
    Serial.println("[SPK] playback start (mic muted)");
  }
  playbackBytes += n;
  lastSpeakerMs = millis();
  wasPlaying = true;
  i2sSpk.write(tempOut, n);
}

// ---------------------------------------------------------------- WebSocket

void onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      greetingReceived = false;
      micActive = false;
      wasPlaying = false;
      wsConnectedAt = millis();
      lastSpeakerMs = 0;
      audioHangoverUntil = 0;
      resetRing();
      Serial.println("[WS] Connected to LiveKit Bridge!");
      showHUD("ALTEREGO LIVE", "Starting up...");
      break;

    case WStype_DISCONNECTED:
      if (wsConnected) {
        Serial.println("[WS] Disconnected from Bridge!");
        showHUD("DISCONNECTED", "Reconnecting...");
      }
      wsConnected = false;
      micActive = false;
      wasPlaying = false;
      resetRing();
      break;

    case WStype_TEXT: {
      String msg;
      msg.concat((const char*)payload, length);
      if (msg.startsWith("TEXT:")) {
        showHUD("ALTEREGO", msg.substring(5));
      }
      break;
    }

    case WStype_BIN: {
      // The agent's audio track keeps sending silent frames while it is NOT speaking.
      // If those reach the speaker path they keep the mic muted forever, so drop them.
      int pk = 0;
      for (size_t i = 0; i + 1 < length; i += 2) {
        int v = (int16_t)((uint16_t)payload[i] | ((uint16_t)payload[i + 1] << 8));
        if (v < 0) v = -v;
        if (v > pk) pk = v;
      }
      unsigned long now = millis();
      if (pk >= SPK_SILENCE_PEAK) {
        audioHangoverUntil = now + SPK_HANGOVER_MS;
      } else if ((long)(now - audioHangoverUntil) > 0) {
        silentFrames++;
        if (pk > silentMaxPeak) silentMaxPeak = pk;
        break;   // silence outside the hangover window: ignore it completely
      }
      if (!greetingReceived) Serial.println("[WS] First audio from agent");
      greetingReceived = true;
      pushSpeakerAudio(payload, length);
      break;
    }

    default:
      break;
  }
}

// ---------------------------------------------------------------- microphone

// Read and throw away mic chunks (startup spike, or audio left over from playback).
void discardMicChunks(int count) {
  for (int i = 0; i < count; i++) {
    i2sMic.readBytes((char*)micBuffer, sizeof(micBuffer));
    webSocket.loop();
  }
}

bool startMic() {
  if (millis() - lastMicTryMs < 2000UL && lastMicTryMs != 0) return false;
  lastMicTryMs = millis();

  if (!micDriverUp) {
    i2sMic.setPinsPdmRx(PDM_CLK_PIN, PDM_DATA_PIN);
    micDriverUp = i2sMic.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
    i2sMic.setTimeout(50);
    Serial.printf("[MIC] driver start ok=%d\n", micDriverUp ? 1 : 0);
    if (!micDriverUp) return false;
  }

  discardMicChunks(MIC_WARMUP_CHUNKS);
  dcReady = false;
  emptyReads = 0;
  wasPlaying = false;
  micActive = true;
  micResumeMs = millis();
  Serial.println("[MIC] Listening");
  showHUD("ALTEREGO LIVE", "Listening...");
  return true;
}

void stopMicDriver() {
  i2sMic.end();
  micDriverUp = false;
  micActive = false;
  Serial.println("[MIC] driver stopped (will restart)");
}

void processAndSendMic() {
  size_t bytesRead = i2sMic.readBytes((char*)micBuffer, sizeof(micBuffer));
  if (bytesRead < 2) {
    if (++emptyReads > 50) stopMicDriver();   // I2S stalled: restart it
    return;
  }
  emptyReads = 0;

  int samples = bytesRead / 2;

  if (!dcReady) {                             // seed the DC estimate with this chunk's mean
    int64_t sum = 0;
    for (int i = 0; i < samples; i++) sum += micBuffer[i];
    dcEstimate = (float)(sum / samples);
    dcReady = true;
  }

  int peak = 0;
  for (int i = 0; i < samples; i++) {
    float x = (float)micBuffer[i];
    dcEstimate += (x - dcEstimate) * 0.002f;  // slow DC tracking (about 5 Hz high-pass)
    int32_t boosted = (int32_t)((x - dcEstimate) * MIC_GAIN);
    if (boosted > 32767) boosted = 32767;
    if (boosted < -32768) boosted = -32768;
    int16_t val = (int16_t)boosted;
    micBuffer[i] = val;
    int absVal = val < 0 ? -val : val;
    if (absVal > peak) peak = absVal;
  }

  bool sending = wsConnected && (millis() - micResumeMs > POST_FLUSH_MS);
  if (sending) {
    webSocket.sendBIN((uint8_t*)micBuffer, samples * 2);
  }

  if (millis() - lastLogMs > 1000) {
    lastLogMs = millis();
    Serial.printf("[MIC] peak=%d sending=%d ws=%d\n", peak, sending ? 1 : 0, wsConnected ? 1 : 0);
  }
}

// ---------------------------------------------------------------- Wi-Fi

void logSilenceStats() {
  if (millis() - lastSilenceLogMs < 5000UL) return;
  lastSilenceLogMs = millis();
  if (silentFrames > 0) {
    Serial.printf("[WS] ignored %lu silent agent frames (max peak %d)\n", silentFrames, silentMaxPeak);
    silentFrames = 0;
    silentMaxPeak = 0;
  }
}

void maintainWifi() {
  if (millis() - lastWifiCheckMs < WIFI_CHECK_MS) return;
  lastWifiCheckMs = millis();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Lost connection, reconnecting...");
    WiFi.reconnect();
  }
}

// ---------------------------------------------------------------- setup / loop

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\n[BOOT] reset reason=%d (1=power-on 3=software 4=panic 5-7=watchdog 9=brownout)\n",
                (int)esp_reset_reason());

  spkRing = (uint8_t*)ps_malloc(SPK_RING_SIZE);
  if (spkRing) {
    spkRingSize = SPK_RING_SIZE;
  } else {
    Serial.println("[WARN] PSRAM alloc failed! Set Tools -> PSRAM -> OPI PSRAM. Using a small buffer.");
    spkRing = (uint8_t*)malloc(SPK_RING_FALLBACK);
    spkRingSize = spkRing ? SPK_RING_FALLBACK : 0;
  }
  Serial.printf("[BOOT] speaker ring=%u bytes, free heap=%u, free psram=%u\n",
                (unsigned)spkRingSize, (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());

  Wire.begin(I2C_SDA, I2C_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[WARN] OLED init failed");
  }
  showHUD("ALTEREGO V1", "Connecting Wi-Fi...");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi IP: " + WiFi.localIP().toString());

  i2sSpk.setPins(SPK_BCLK_PIN, SPK_LRC_PIN, SPK_DOUT_PIN);
  i2sSpk.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);

  webSocket.begin(BRIDGE_HOST, BRIDGE_PORT, "/");
  webSocket.onEvent(onWebSocketEvent);
  webSocket.setReconnectInterval(3000);
  webSocket.enableHeartbeat(15000, 4000, 2);   // detect a dead bridge connection
}

void loop() {
  webSocket.loop();
  maintainWifi();
  logSilenceStats();

  // 1) Agent audio waiting: play it. The I2S write paces this loop.
  if (hasSpeakerAudio()) {
    drainSpeakerAudio();
    return;
  }

  if (playbackActive) {
    playbackActive = false;
    Serial.printf("[SPK] playback done, %u bytes (mic resumes shortly)\n", (unsigned)playbackBytes);
  }

  if (!wsConnected) {
    delay(5);
    return;
  }

  // If the greeting never arrives, still start listening so the glasses are not stuck.
  if (!greetingReceived && millis() - wsConnectedAt > GREETING_TIMEOUT_MS) {
    Serial.println("[WARN] No greeting received, listening anyway.");
    greetingReceived = true;
  }

  // 2) Wait for the speaker to have been quiet for a moment.
  if (!greetingReceived || millis() - lastSpeakerMs <= POST_SPK_MS) {
    delay(2);
    return;
  }

  // 3) Start the mic once, then keep it running.
  if (!micActive) {
    if (!startMic()) {
      delay(20);
      return;
    }
  }

  // 4) After playback, drop stale mic audio (it contains the agent's own voice).
  if (wasPlaying) {
    discardMicChunks(MIC_FLUSH_CHUNKS);
    wasPlaying = false;
    dcReady = false;
    micResumeMs = millis();
    return;
  }

  processAndSendMic();
}