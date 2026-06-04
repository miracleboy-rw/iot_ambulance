import os
import json
import time
import math
import random
import threading
from datetime import datetime
from flask import Flask, render_template, render_template_string
from flask_socketio import SocketIO, emit

# ============================================================
# KONFIGURASI — Ganti sesuai port Serial ESP32 kamu
# ============================================================
SERIAL_PORT = "COM3"      # Windows: "COM3", "COM4", dll
BAUD_RATE   = 115200
DEMO_MODE   = True        # True = gunakan data simulasi (tanpa ESP32)
                          # False = baca dari Serial USB nyata
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = "health_monitor_secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# State global (tidak pakai database)
latest_data = {
    "ecg": 0.0,
    "bpm": None,
    "spo2": None,
    "timestamp": "--:--:--",
    "connected": False,
}


# ─────────────────────────────────────────────
# Status helpers
# ─────────────────────────────────────────────
def bpm_status(bpm):
    if bpm is None:
        return "TUNGGU", "waiting"
    if bpm < 60:
        return "RENDAH", "low"
    if bpm > 100:
        return "TINGGI", "high"
    return "AMAN", "normal"


def spo2_status(spo2):
    if spo2 is None:
        return "TUNGGU", "waiting"
    if spo2 < 95:
        return "RENDAH", "low"
    return "NORMAL", "normal"


def patient_status(bpm, spo2):
    if bpm is None or spo2 is None:
        return "TUNGGU", "waiting"
    if 60 <= bpm <= 100 and spo2 >= 95:
        return "AMAN", "normal"
    return "PERHATIAN", "attention"


# ─────────────────────────────────────────────
# Demo mode — simulasi sinyal ECG + sensor
# ─────────────────────────────────────────────
_demo_t = 0.0
_demo_bpm = 75
_demo_spo2 = 98


def _ecg_simulate(t):
    """Simulasi bentuk gelombang ECG sederhana (PQRST)."""
    cycle = t % 1.0
    # Baseline
    val = 0.0
    # P wave
    if 0.05 < cycle < 0.15:
        val = 0.25 * math.sin(math.pi * (cycle - 0.05) / 0.10)
    # QRS complex
    elif 0.18 < cycle < 0.22:
        val = -0.15 * math.sin(math.pi * (cycle - 0.18) / 0.02)
    elif 0.22 < cycle < 0.30:
        val = 1.20 * math.sin(math.pi * (cycle - 0.22) / 0.08)
    elif 0.30 < cycle < 0.34:
        val = -0.35 * math.sin(math.pi * (cycle - 0.30) / 0.04)
    # T wave
    elif 0.38 < cycle < 0.55:
        val = 0.35 * math.sin(math.pi * (cycle - 0.38) / 0.17)
    val += random.gauss(0, 0.015)
    return round(val, 4)


def demo_reader():
    """Thread simulasi data saat DEMO_MODE=True."""
    global _demo_t, _demo_bpm, _demo_spo2, latest_data
    while True:
        _demo_t += 0.02
        # Sedikit variasi BPM & SpO2 agar terlihat "hidup"
        if random.random() < 0.005:
            _demo_bpm = max(58, min(105, _demo_bpm + random.randint(-3, 3)))
        if random.random() < 0.003:
            _demo_spo2 = max(92, min(100, _demo_spo2 + random.randint(-1, 1)))

        ecg_val = _ecg_simulate(_demo_t)
        now = datetime.now().strftime("%H:%M:%S")

        latest_data = {
            "ecg": ecg_val,
            "bpm": _demo_bpm,
            "spo2": _demo_spo2,
            "timestamp": now,
            "connected": True,
        }
        socketio.emit("sensor_data", latest_data)
        time.sleep(0.02)   # ~50Hz


# ─────────────────────────────────────────────
# Real Serial reader
# ─────────────────────────────────────────────
def serial_reader():
    """Baca data JSON dari ESP32 via Serial USB."""
    global latest_data
    try:
        import serial
    except ImportError:
        print("[ERROR] PySerial belum terinstall. Jalankan: pip install pyserial")
        return

    ser = None
    while True:
        try:
            if ser is None or not ser.is_open:
                print(f"[Serial] Menghubungkan ke {SERIAL_PORT} @ {BAUD_RATE}...")
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                print("[Serial] Terhubung.")
                socketio.emit("device_status", {"connected": True})

            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                ecg  = float(data.get("ecg", 0.0))
                bpm  = int(data.get("bpm", 0)) or None
                spo2 = int(data.get("spo2", 0)) or None
                now  = datetime.now().strftime("%H:%M:%S")

                latest_data = {
                    "ecg": ecg,
                    "bpm": bpm,
                    "spo2": spo2,
                    "timestamp": now,
                    "connected": True,
                }
                socketio.emit("sensor_data", latest_data)
            except (json.JSONDecodeError, ValueError, KeyError):
                # Abaikan baris tidak valid
                pass

        except Exception as e:
            print(f"[Serial] Error: {e}")
            latest_data["connected"] = False
            socketio.emit("device_status", {"connected": False})
            if ser:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            time.sleep(3)


# ─────────────────────────────────────────────
# HTML Template (dirender oleh Python/Jinja2)
# ─────────────────────────────────────────────
DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Patient Health Monitoring Dashboard</title>
  <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&family=Fraunces:wght@700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:        #F0F2F5;
      --surface:   #FFFFFF;
      --border:    #E4E8EE;
      --text-1:    #0D1117;
      --text-2:    #5A6478;
      --text-3:    #8C95A6;
      --accent:    #1A7FDB;
      --normal-bg: #EBF8F2;
      --normal-fg: #0E7A4A;
      --low-bg:    #FFF3E0;
      --low-fg:    #E65100;
      --high-bg:   #FFEBEE;
      --high-fg:   #C62828;
      --wait-bg:   #F5F5F5;
      --wait-fg:   #9E9E9E;
      --ecg-line:  #1A7FDB;
      --shadow:    0 2px 12px rgba(0,0,0,.07);
      --radius:    14px;
    }

    html, body {
      height: 100%;
      background: var(--bg);
      font-family: 'DM Sans', sans-serif;
      color: var(--text-1);
    }

    body {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
      padding: 0 0 32px;
    }

    /* ── Header ── */
    .header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 18px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 1px 8px rgba(0,0,0,.05);
    }

    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .header-icon {
      width: 38px; height: 38px;
      background: var(--accent);
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
    }
    .header-icon svg { width:20px; height:20px; fill:none; stroke:#fff; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }

    .header-title {
      font-family: 'Fraunces', serif;
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--text-1);
    }
    .header-subtitle {
      font-size: .72rem;
      color: var(--text-3);
      margin-top: 1px;
      font-weight: 400;
    }

    .header-right {
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .refresh-time {
      font-family: 'DM Mono', monospace;
      font-size: .8rem;
      color: var(--text-2);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .refresh-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: #ccc;
      transition: background .4s;
    }
    .refresh-dot.online { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.2); }
    .refresh-dot.offline { background: #ef4444; }

    .device-badge {
      font-size: .72rem;
      padding: 3px 10px;
      border-radius: 20px;
      font-weight: 500;
      background: var(--wait-bg);
      color: var(--wait-fg);
      transition: all .3s;
    }
    .device-badge.online { background: var(--normal-bg); color: var(--normal-fg); }
    .device-badge.offline { background: var(--high-bg); color: var(--high-fg); }

    /* ── Main layout ── */
    .main {
      flex: 1;
      padding: 28px 32px 0;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
    }

    /* ── Card grid ── */
    .cards-row {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      margin-bottom: 20px;
    }

    .card {
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      padding: 24px 28px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      transition: box-shadow .2s;
    }
    .card:hover { box-shadow: 0 4px 24px rgba(0,0,0,.10); }

    .card-label-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .card-icon {
      width: 36px; height: 36px;
      border-radius: 9px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1rem;
    }
    .card-icon.bpm   { background: #FFF0F3; }
    .card-icon.spo2  { background: #EBF3FF; }
    .card-icon.status{ background: #F0FFF8; }

    .card-category {
      font-size: .72rem;
      font-weight: 600;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--text-3);
    }

    .card-value {
      font-family: 'Fraunces', serif;
      font-size: 3.2rem;
      font-weight: 700;
      line-height: 1;
      letter-spacing: -0.04em;
      color: var(--text-1);
      margin: 4px 0 2px;
    }
    .card-value .unit {
      font-family: 'DM Sans', sans-serif;
      font-size: 1rem;
      font-weight: 400;
      color: var(--text-3);
      margin-left: 4px;
      letter-spacing: 0;
    }
    .card-value.big-status {
      font-size: 2.4rem;
    }

    .card-desc {
      font-size: .82rem;
      color: var(--text-2);
      margin-bottom: 10px;
    }

    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: .75rem;
      font-weight: 600;
      letter-spacing: .05em;
      text-transform: uppercase;
      padding: 4px 10px;
      border-radius: 20px;
      width: fit-content;
    }
    .status-chip::before {
      content: '';
      width: 6px; height: 6px;
      border-radius: 50%;
    }
    .status-chip.normal  { background: var(--normal-bg); color: var(--normal-fg); }
    .status-chip.normal::before  { background: var(--normal-fg); }
    .status-chip.low     { background: var(--low-bg); color: var(--low-fg); }
    .status-chip.low::before     { background: var(--low-fg); }
    .status-chip.high    { background: var(--high-bg); color: var(--high-fg); }
    .status-chip.high::before    { background: var(--high-fg); }
    .status-chip.waiting { background: var(--wait-bg); color: var(--wait-fg); }
    .status-chip.waiting::before { background: var(--wait-fg); }
    .status-chip.attention { background: #FFF3E0; color: #E65100; }
    .status-chip.attention::before { background: #E65100; }

    /* Status card big display */
    .patient-status-value {
      font-family: 'Fraunces', serif;
      font-size: 2.6rem;
      font-weight: 700;
      letter-spacing: -.03em;
      margin: 8px 0 4px;
      transition: color .4s;
    }
    .patient-status-value.normal    { color: var(--normal-fg); }
    .patient-status-value.attention { color: #E65100; }
    .patient-status-value.waiting   { color: var(--wait-fg); }

    /* ── ECG Card ── */
    .ecg-card {
      background: var(--surface);
      border-radius: var(--radius);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      padding: 24px 28px;
    }

    .ecg-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .ecg-title-group { display: flex; flex-direction: column; gap: 2px; }
    .ecg-title {
      font-family: 'Fraunces', serif;
      font-size: 1rem;
      font-weight: 700;
      letter-spacing: -.01em;
    }
    .ecg-subtitle { font-size: .72rem; color: var(--text-3); }
    .ecg-badge {
      font-size: .7rem;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 3px 10px;
      border-radius: 20px;
      background: #EBF3FF;
      color: var(--accent);
    }

    .ecg-canvas-wrap {
      position: relative;
      height: 200px;
    }
    #ecgChart { width: 100% !important; height: 100% !important; }

    /* ── Divider line ── */
    .section-divider {
      height: 1px;
      background: var(--border);
      margin: 4px 0 20px;
    }

    /* ── Footer ── */
    .footer {
      text-align: center;
      font-size: .7rem;
      color: var(--text-3);
      padding: 20px 32px 0;
      max-width: 1400px;
      margin: 0 auto;
      width: 100%;
    }

    /* ── Responsive ── */
    @media (max-width: 900px) {
      .cards-row { grid-template-columns: 1fr 1fr; }
    }
    @media (max-width: 600px) {
      .main { padding: 16px 14px 0; }
      .header { padding: 14px 16px; }
      .cards-row { grid-template-columns: 1fr; }
      .card-value { font-size: 2.4rem; }
    }

    /* ── Pulse animation for live dot ── */
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: .4; }
    }
    .refresh-dot.online { animation: pulse 2s infinite; }
  </style>
</head>
<body>

<!-- ===== HEADER ===== -->
<header class="header">
  <div class="header-left">
    <div class="header-icon">
      <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
    </div>
    <div>
      <div class="header-title">Patient Health Monitoring Dashboard</div>
      <div class="header-subtitle">Prototipe Monitoring Tanda-Tanda Vital Real-Time</div>
    </div>
  </div>
  <div class="header-right">
    <div class="refresh-time">
      <span class="refresh-dot" id="statusDot"></span>
      Data refreshed at <span id="refreshTime">--:--:--</span>
    </div>
    <span class="device-badge" id="deviceBadge">⏳ Menunggu Perangkat</span>
  </div>
</header>

<!-- ===== MAIN ===== -->
<main class="main">

  <!-- Vital Cards -->
  <div class="cards-row">

    <!-- BPM -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon bpm">❤️</div>
        <span class="card-category">Heart Rate</span>
      </div>
      <div class="card-value" id="bpmValue">-- <span class="unit">bpm</span></div>
      <div class="card-desc">Detak Jantung</div>
      <span class="status-chip waiting" id="bpmChip">Menunggu</span>
    </div>

    <!-- SpO2 -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon spo2">🩸</div>
        <span class="card-category">SpO₂</span>
      </div>
      <div class="card-value" id="spo2Value">-- <span class="unit">%</span></div>
      <div class="card-desc">Saturasi Oksigen</div>
      <span class="status-chip waiting" id="spo2Chip">Menunggu</span>
    </div>

    <!-- Patient Status -->
    <div class="card">
      <div class="card-label-row">
        <div class="card-icon status">🏥</div>
        <span class="card-category">Status Pasien</span>
      </div>
      <div class="patient-status-value waiting" id="patientStatus">TUNGGU</div>
      <div class="card-desc">Kondisi keseluruhan pasien</div>
      <span class="status-chip waiting" id="patientChip">Belum ada data</span>
    </div>

  </div><!-- /cards-row -->

  <div class="section-divider"></div>

  <!-- ECG Card -->
  <div class="ecg-card">
    <div class="ecg-header">
      <div class="ecg-title-group">
        <div class="ecg-title">ECG Waveform</div>
        <div class="ecg-subtitle">Single-Lead ECG · AD8232 Sensor</div>
      </div>
      <span class="ecg-badge">Live</span>
    </div>
    <div class="ecg-canvas-wrap">
      <canvas id="ecgChart"></canvas>
    </div>
  </div>

</main>

<footer class="footer">
  ⚠️ Sistem prototipe — bukan alat medis klinis. Data real-time, tidak tersimpan. 
  Single-lead ECG (AD8232) · SpO₂ (MAX30102) · Berjalan via Flask + Socket.IO
</footer>

<!-- ===== SCRIPTS ===== -->
<script>
// ── ECG Chart setup ──
const MAX_POINTS = 350;
const ecgData = new Array(MAX_POINTS).fill(0);
const ecgLabels = new Array(MAX_POINTS).fill('');

const ctx = document.getElementById('ecgChart').getContext('2d');
const ecgChart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: ecgLabels,
    datasets: [{
      data: ecgData,
      borderColor: '#1A7FDB',
      borderWidth: 1.8,
      pointRadius: 0,
      tension: 0.3,
      fill: {
        target: 'origin',
        above: 'rgba(26,127,219,0.06)',
        below: 'rgba(26,127,219,0.02)',
      },
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'nearest', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { enabled: false },
    },
    scales: {
      x: {
        display: false,
        grid: { display: false },
      },
      y: {
        grid: {
          color: 'rgba(0,0,0,.05)',
          drawBorder: false,
        },
        ticks: {
          color: '#8C95A6',
          font: { family: 'DM Mono', size: 10 },
          maxTicksLimit: 5,
        },
        border: { display: false },
      }
    }
  }
});

// ── Status helpers ──
function bpmStatus(v) {
  if (v === null || v === undefined) return ['Menunggu','waiting'];
  if (v < 60) return ['RENDAH','low'];
  if (v > 100) return ['TINGGI','high'];
  return ['AMAN','normal'];
}
function spo2Status(v) {
  if (v === null || v === undefined) return ['Menunggu','waiting'];
  if (v < 95) return ['RENDAH','low'];
  return ['NORMAL','normal'];
}
function patientStatusFn(bpm, spo2) {
  if (bpm === null || spo2 === null || bpm === undefined || spo2 === undefined)
    return ['TUNGGU','waiting','Belum ada data'];
  if (bpm >= 60 && bpm <= 100 && spo2 >= 95)
    return ['AMAN','normal','Kondisi stabil'];
  return ['PERHATIAN','attention','Memerlukan perhatian'];
}
function setChip(el, text, cls) {
  el.textContent = text;
  el.className = 'status-chip ' + cls;
}

// ── Socket.IO connection ──
const socket = io();

socket.on('connect', () => {
  console.log('[Socket] Terhubung ke server');
});

socket.on('sensor_data', (d) => {
  // Refresh time
  document.getElementById('refreshTime').textContent = d.timestamp || '--:--:--';

  // Device online
  const dot = document.getElementById('statusDot');
  const badge = document.getElementById('deviceBadge');
  dot.className = 'refresh-dot online';
  badge.textContent = '✅ Perangkat Online';
  badge.className = 'device-badge online';

  // BPM
  const bpmEl = document.getElementById('bpmValue');
  bpmEl.innerHTML = (d.bpm !== null && d.bpm !== undefined)
    ? d.bpm + ' <span class="unit">bpm</span>'
    : '-- <span class="unit">bpm</span>';
  const [bLabel, bCls] = bpmStatus(d.bpm);
  setChip(document.getElementById('bpmChip'), bLabel, bCls);

  // SpO2
  const spo2El = document.getElementById('spo2Value');
  spo2El.innerHTML = (d.spo2 !== null && d.spo2 !== undefined)
    ? d.spo2 + ' <span class="unit">%</span>'
    : '-- <span class="unit">%</span>';
  const [sLabel, sCls] = spo2Status(d.spo2);
  setChip(document.getElementById('spo2Chip'), sLabel, sCls);

  // Patient status
  const [pLabel, pCls, pDesc] = patientStatusFn(d.bpm, d.spo2);
  const psEl = document.getElementById('patientStatus');
  psEl.textContent = pLabel;
  psEl.className = 'patient-status-value ' + pCls;
  setChip(document.getElementById('patientChip'), pDesc, pCls);

  // ECG chart — sliding window
  ecgData.shift();
  ecgData.push(d.ecg);
  ecgChart.update('none');
});

socket.on('device_status', (d) => {
  const dot   = document.getElementById('statusDot');
  const badge = document.getElementById('deviceBadge');
  if (d.connected) {
    dot.className = 'refresh-dot online';
    badge.textContent = '✅ Perangkat Online';
    badge.className = 'device-badge online';
  } else {
    dot.className = 'refresh-dot offline';
    badge.textContent = '❌ Perangkat Offline';
    badge.className = 'device-badge offline';
  }
});

socket.on('disconnect', () => {
  document.getElementById('statusDot').className = 'refresh-dot offline';
  document.getElementById('deviceBadge').textContent = '❌ Koneksi Terputus';
  document.getElementById('deviceBadge').className = 'device-badge offline';
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@socketio.on("connect")
def handle_connect():
    """Kirim data terakhir saat client baru terhubung."""
    emit("sensor_data", latest_data)
    emit("device_status", {"connected": latest_data.get("connected", False)})


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Patient Health Monitoring Dashboard")
    print("=" * 55)
    if DEMO_MODE:
        print("  Mode: DEMO (data simulasi tanpa ESP32)")
        t = threading.Thread(target=demo_reader, daemon=True)
    else:
        print(f"  Mode: REAL — Serial {SERIAL_PORT} @ {BAUD_RATE}")
        t = threading.Thread(target=serial_reader, daemon=True)
    t.start()
    print("  Buka browser: http://localhost:5000")
    print("=" * 55)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)